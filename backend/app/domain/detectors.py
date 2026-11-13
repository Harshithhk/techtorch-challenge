"""Investigation: turns a ledger snapshot into findings. Pure domain, no LLM, no I/O.

Entry point: investigate(ledger, as_of, term_months, plan_id=None) -> list[Finding].

Every call recomputes from the snapshot. Nothing is cached or precomputed, and no finding
is hardcoded. Each Finding carries the amounts and a calculation trail so the LLM can explain
it without doing any arithmetic itself.

Money is compared in the plan's currency. Each invoice and credit memo is converted on its
own issue_date (see fx.py). A period's net = converted invoices - converted credit memos on those invoices.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.domain.fx import NoFxRateError, convert
from app.domain.models import (
    ActionKind,
    Invoice,
    Ledger,
    Plan,
    UnknownPlanError,
)
from app.domain.money import Money, total, zero
from app.domain.schedule import BillingPeriod, build_families, build_periods, plan_segments


class FindingKind(StrEnum):
    """Every category of anomaly the domain can report."""

    MISSING_PERIOD = "missing_period"  # period with no invoice in its window -> make-good
    AMOUNT_UNDERBILLED = "amount_underbilled"  # net billed < expected -> make-good for the gap
    AMOUNT_OVERBILLED = "amount_overbilled"  # net billed > expected -> credit memo for the excess
    DUPLICATE_BILLING = "duplicate_billing"  # more than one invoice in a single period (info)
    CURRENCY_MISMATCH = "currency_mismatch"  # invoice currency differs from plan currency (info)
    FX_RATE_MISSING = "fx_rate_missing"  # cannot convert, so the period cannot be judged (blocked)
    ORPHAN_INVOICE = "orphan_invoice"  # invoice with empty plan_id; needs a human decision
    UNKNOWN_PLAN = "unknown_plan"  # invoice references a plan_id not in billing_plans
    PLAN_WINDOW_VIOLATION = "plan_window_violation"  # invoice issued outside its plan's active window
    CREDIT_MEMO_ANOMALY = "credit_memo_anomaly"  # memo references missing invoice, mismatched plan, or excess
    OVERDUE_UNPAID = "overdue_unpaid"  # unpaid and past due date as of the as-of date
    ENTITLEMENT_NOT_EVIDENCED = "entitlement_not_evidenced"  # plan entitlement not seen in invoice text (low confidence)


@dataclass(frozen=True)
class Finding:
    """One detected anomaly with its evidence.

    All money is in the plan currency unless the finding is about a single invoice (overdue).
    `calculation` is the ordered trail a reviewer can recheck by hand.
    `finding_id` is deterministic, so the chat can refer back to "the same finding".
    """

    finding_id: str
    kind: FindingKind
    plan_id: str | None
    invoice_ids: tuple[str, ...]
    target_invoice_id: str | None
    period_start: date | None
    period_end: date | None
    expected: Money | None
    actual: Money | None
    delta: Money | None
    suggested_action: ActionKind | None
    confidence: str  # "high" or "low"
    summary: str
    calculation: tuple[str, ...]
    candidate_plan_ids: tuple[str, ...] = ()


def _finding(
    kind: FindingKind,
    *,
    plan_id: str | None = None,
    invoice_ids: Iterable[str] = (),
    target_invoice_id: str | None = None,
    period: BillingPeriod | None = None,
    expected: Money | None = None,
    actual: Money | None = None,
    delta: Money | None = None,
    action: ActionKind | None = None,
    confidence: str = "high",
    summary: str = "",
    calculation: Iterable[str] = (),
    candidates: Iterable[str] = (),
) -> Finding:
    """Build a Finding and derive its deterministic id from kind, plan, period and invoices."""
    invoice_tuple = tuple(invoice_ids)
    period_start = period.start if period else None
    fid = f"{kind.value}|{plan_id or ''}|{period_start.isoformat() if period_start else ''}|{','.join(invoice_tuple)}"
    return Finding(
        finding_id=fid,
        kind=kind,
        plan_id=plan_id,
        invoice_ids=invoice_tuple,
        target_invoice_id=target_invoice_id,
        period_start=period_start,
        period_end=period.end if period else None,
        expected=expected,
        actual=actual,
        delta=delta,
        suggested_action=action,
        confidence=confidence,
        summary=summary,
        calculation=tuple(calculation),
        candidate_plan_ids=tuple(candidates),
    )


def investigate(
    ledger: Ledger,
    as_of: date,
    term_months: int,
    plan_id: str | None = None,
) -> list[Finding]:
    """Run every detector over the ledger and return findings, sorted deterministically.

    Input:
        ledger: snapshot of plans, invoices, credit memos and FX rates.
        as_of: only periods that have started on or before this date are judged.
        term_months: assumed contract term for every plan.
        plan_id: optional. If set, only findings for that plan's amendment family are returned.
    Output: list[Finding] sorted by (plan, period start, kind, id).
    Raises: UnknownPlanError if plan_id is not in the ledger. InvalidLedgerError for broken structure.
    """
    families = build_families(ledger.plans)
    scope: set[str] | None = None
    if plan_id is not None:
        family = next((members for members in families.values() if any(p.plan_id == plan_id for p in members)), None)
        if family is None:
            raise UnknownPlanError(f"plan {plan_id} not found")
        scope = {p.plan_id for p in family}

    findings: list[Finding] = []
    for members in families.values():
        findings.extend(_family_findings(members, ledger, as_of, term_months))
    findings.extend(_orphan_and_unknown_invoices(ledger))
    findings.extend(_credit_memo_checks(ledger))
    findings.extend(_overdue_invoices(ledger, as_of))
    findings.extend(_entitlement_checks(ledger))

    scoped = [f for f in findings if _in_scope(f, scope)]
    return sorted(scoped, key=lambda f: (f.plan_id or "", f.period_start or date.min, f.kind.value, f.finding_id))


def _in_scope(finding: Finding, scope: set[str] | None) -> bool:
    """True if no scope is set, the finding's plan is in scope, or an orphan's candidate plan is in scope."""
    if scope is None:
        return True
    if finding.plan_id is not None and finding.plan_id in scope:
        return True
    return finding.kind is FindingKind.ORPHAN_INVOICE and bool(set(finding.candidate_plan_ids) & scope)


def _family_findings(family: Sequence[Plan], ledger: Ledger, as_of: date, term_months: int) -> list[Finding]:
    """Findings for one amendment family: window violations, currency notes, and per-period variances."""
    currency = family[0].currency
    plan_ids = {p.plan_id for p in family}
    segments = {s.plan_id: s for s in plan_segments(family, term_months)}
    family_invoices = [inv for inv in ledger.invoices if inv.plan_id in plan_ids]

    findings: list[Finding] = []
    for inv in family_invoices:
        seg = segments[inv.plan_id]
        if not seg.start <= inv.issue_date < seg.end:
            findings.append(
                _finding(
                    FindingKind.PLAN_WINDOW_VIOLATION,
                    plan_id=inv.plan_id,
                    invoice_ids=(inv.invoice_id,),
                    summary=(
                        f"Invoice {inv.invoice_id} is tagged to {inv.plan_id} but was issued {inv.issue_date.isoformat()}, "
                        f"outside that plan's active window {seg.start.isoformat()} to {seg.end.isoformat()}."
                    ),
                    calculation=(
                        f"Active window of {inv.plan_id}: [{seg.start.isoformat()}, {seg.end.isoformat()})",
                        f"Issue date of {inv.invoice_id}: {inv.issue_date.isoformat()}",
                    ),
                )
            )
        if inv.currency != currency:
            findings.append(
                _finding(
                    FindingKind.CURRENCY_MISMATCH,
                    plan_id=inv.plan_id,
                    invoice_ids=(inv.invoice_id,),
                    summary=f"Invoice {inv.invoice_id} is billed in {inv.currency}; plan {inv.plan_id} is in {currency}. Compared after conversion on the issue date.",
                    calculation=(f"Invoice currency {inv.currency} != plan currency {currency}",),
                )
            )

    for period in build_periods(family, term_months):
        if period.start > as_of:
            continue
        findings.extend(_period_findings(period, family_invoices, ledger))
    return findings


def _period_findings(period: BillingPeriod, family_invoices: Sequence[Invoice], ledger: Ledger) -> list[Finding]:
    """Compare one period's expected amount against its converted, credit-adjusted billing."""
    currency = period.expected.currency
    window_invoices = [i for i in family_invoices if period.start <= i.issue_date < period.end]
    expected = period.expected
    window_label = f"{period.start.isoformat()} to {period.end.isoformat()}"

    if not window_invoices:
        return [
            _finding(
                FindingKind.MISSING_PERIOD,
                plan_id=period.plan_id,
                period=period,
                expected=expected,
                actual=zero(currency),
                delta=-expected,
                action=ActionKind.MAKE_GOOD_INVOICE,
                summary=(
                    f"Plan {period.plan_id} has no invoice for the period {window_label}; "
                    f"expected {expected}, billed 0."
                ),
                calculation=(
                    f"Expected for period {window_label}: {expected}",
                    "Invoices issued inside the period: none",
                    f"Gap = {expected}",
                ),
            )
        ]

    try:
        converted = [
            (inv, convert(Money(inv.amount_invoiced, inv.currency), currency, inv.issue_date, ledger.fx_rates))
            for inv in window_invoices
        ]
        invoice_ids = {inv.invoice_id for inv in window_invoices}
        memos = [m for m in ledger.credit_memos if m.invoice_id in invoice_ids]
        converted_memos = [
            (m, convert(Money(m.amount, m.currency), currency, m.issue_date, ledger.fx_rates)) for m in memos
        ]
    except NoFxRateError as exc:
        return [
            _finding(
                FindingKind.FX_RATE_MISSING,
                plan_id=period.plan_id,
                invoice_ids=invoice_ids_of(window_invoices),
                period=period,
                expected=expected,
                confidence="low",
                summary=f"Cannot judge period {window_label} of {period.plan_id}: {exc}.",
                calculation=(f"Expected for period {window_label}: {expected}", str(exc)),
            )
        ]

    gross = total((c.money for _, c in converted), currency)
    credits = total((c.money for _, c in converted_memos), currency)
    net = gross - credits
    delta = net - expected

    calculation = [f"Expected for period {window_label}: {expected}"]
    calculation += [f"Invoice {inv.invoice_id}: {c.describe(Money(inv.amount_invoiced, inv.currency))}" for inv, c in converted]
    calculation += [f"Credit memo {m.memo_id} against {m.invoice_id}: {c.describe(Money(m.amount, m.currency))}" for m, c in converted_memos]
    calculation += [f"Net billed = invoices {gross} - credit memos {credits} = {net}", f"Variance = net - expected = {delta}"]

    findings: list[Finding] = []
    if len(window_invoices) > 1:
        findings.append(
            _finding(
                FindingKind.DUPLICATE_BILLING,
                plan_id=period.plan_id,
                invoice_ids=invoice_ids_of(window_invoices),
                period=period,
                expected=expected,
                actual=net,
                summary=f"{len(window_invoices)} invoices fall inside period {window_label} of {period.plan_id}.",
                calculation=calculation,
            )
        )

    if delta.amount < 0:
        findings.append(
            _finding(
                FindingKind.AMOUNT_UNDERBILLED,
                plan_id=period.plan_id,
                invoice_ids=invoice_ids_of(window_invoices),
                period=period,
                expected=expected,
                actual=net,
                delta=delta,
                action=ActionKind.MAKE_GOOD_INVOICE,
                summary=(
                    f"Plan {period.plan_id} was underbilled for {window_label}: expected {expected}, "
                    f"net billed {net}, short by {-delta}."
                ),
                calculation=calculation + [f"Make-good amount = {-delta}"],
            )
        )
    elif delta.amount > 0:
        target = max(window_invoices, key=lambda i: (i.issue_date, i.invoice_id))
        findings.append(
            _finding(
                FindingKind.AMOUNT_OVERBILLED,
                plan_id=period.plan_id,
                invoice_ids=invoice_ids_of(window_invoices),
                target_invoice_id=target.invoice_id,
                period=period,
                expected=expected,
                actual=net,
                delta=delta,
                action=ActionKind.CREDIT_MEMO,
                summary=(
                    f"Plan {period.plan_id} was overbilled for {window_label}: expected {expected}, "
                    f"net billed {net}, excess {delta}. Credit memo would target {target.invoice_id}."
                ),
                calculation=calculation + [f"Credit amount = {delta}"],
            )
        )
    return findings


def invoice_ids_of(invoices: Iterable[Invoice]) -> tuple[str, ...]:
    """Invoice ids in a stable order. Input: invoices. Output: tuple of invoice_id strings."""
    return tuple(sorted(inv.invoice_id for inv in invoices))


def _orphan_and_unknown_invoices(ledger: Ledger) -> list[Finding]:
    """Invoices with no plan reference (orphan) or a plan_id not in billing_plans (unknown).

    Orphans are never attached automatically. Plans for the same customer are listed as
    candidates so the agent can ask the user.
    """
    plan_ids = {p.plan_id for p in ledger.plans}
    findings: list[Finding] = []
    for inv in ledger.invoices:
        if inv.plan_id == "":
            candidates = sorted(p.plan_id for p in ledger.plans if p.customer_name == inv.customer_name)
            findings.append(
                _finding(
                    FindingKind.ORPHAN_INVOICE,
                    invoice_ids=(inv.invoice_id,),
                    candidates=candidates,
                    summary=(
                        f"Invoice {inv.invoice_id} ({inv.amount_invoiced} {inv.currency}, issued {inv.issue_date.isoformat()}) "
                        f"has no plan reference. Plans for {inv.customer_name}: {', '.join(candidates) or 'none'}. Not attached."
                    ),
                    calculation=(f"plan_id is empty for {inv.invoice_id}",),
                )
            )
        elif inv.plan_id not in plan_ids:
            findings.append(
                _finding(
                    FindingKind.UNKNOWN_PLAN,
                    plan_id=inv.plan_id,
                    invoice_ids=(inv.invoice_id,),
                    summary=f"Invoice {inv.invoice_id} references plan {inv.plan_id}, which is not in billing plans.",
                    calculation=(f"{inv.plan_id} not found in billing plans",),
                )
            )
    return findings


def _credit_memo_checks(ledger: Ledger) -> list[Finding]:
    """Flag memos that point at a missing invoice, a different plan, or more than the invoice amount.

    A memo's plan must match its invoice's plan. The excess check only runs when currencies match,
    so no cross-currency comparison happens here.
    """
    invoices = {inv.invoice_id: inv for inv in ledger.invoices}
    findings: list[Finding] = []
    for memo in ledger.credit_memos:
        inv = invoices.get(memo.invoice_id)
        problem: str | None = None
        if inv is None:
            problem = f"references invoice {memo.invoice_id}, which does not exist"
        elif inv.plan_id != memo.plan_id:
            problem = f"is tagged to plan {memo.plan_id} but its invoice {inv.invoice_id} is tagged to {inv.plan_id or 'no plan'}"
        elif memo.currency == inv.currency and memo.amount > inv.amount_invoiced:
            problem = f"amount {memo.amount} exceeds invoice {inv.invoice_id} amount {inv.amount_invoiced}"
        if problem:
            findings.append(
                _finding(
                    FindingKind.CREDIT_MEMO_ANOMALY,
                    plan_id=memo.plan_id or None,
                    invoice_ids=(memo.invoice_id,),
                    summary=f"Credit memo {memo.memo_id} {problem}.",
                    calculation=(f"memo {memo.memo_id}: {memo.amount} {memo.currency} against {memo.invoice_id}",),
                )
            )
    return findings


def _overdue_invoices(ledger: Ledger, as_of: date) -> list[Finding]:
    """Unpaid invoices whose due date is before as_of. Reported as collections, not as a make-good."""
    findings: list[Finding] = []
    for inv in ledger.invoices:
        if inv.status == "unpaid" and inv.due_date < as_of:
            days = (as_of - inv.due_date).days
            findings.append(
                _finding(
                    FindingKind.OVERDUE_UNPAID,
                    plan_id=inv.plan_id or None,
                    invoice_ids=(inv.invoice_id,),
                    actual=Money(inv.amount_invoiced, inv.currency),
                    summary=f"Invoice {inv.invoice_id} is unpaid and {days} days past its due date {inv.due_date.isoformat()}.",
                    calculation=(
                        f"status = unpaid, due {inv.due_date.isoformat()}, as of {as_of.isoformat()}",
                        f"Amount outstanding {Money(inv.amount_invoiced, inv.currency)}",
                    ),
                )
            )
    return findings


def _entitlement_checks(ledger: Ledger) -> list[Finding]:
    """Entitlements that no invoice description of the plan mentions. Low confidence: keyword match only.

    Only plans that have at least one invoice are checked, because with no invoices there is no
    evidence either way.
    """
    findings: list[Finding] = []
    for plan in ledger.plans:
        plan_invoices = [inv for inv in ledger.invoices if inv.plan_id == plan.plan_id]
        if not plan_invoices:
            continue
        for entitlement in plan.entitlements:
            needle = entitlement.casefold()
            if any(needle in inv.description.casefold() for inv in plan_invoices):
                continue
            findings.append(
                _finding(
                    FindingKind.ENTITLEMENT_NOT_EVIDENCED,
                    plan_id=plan.plan_id,
                    invoice_ids=invoice_ids_of(plan_invoices),
                    confidence="low",
                    summary=(
                        f"Plan {plan.plan_id} lists entitlement '{entitlement}', but none of its invoice descriptions mention it. "
                        "Keyword check only; this is not a money finding."
                    ),
                    calculation=(
                        f"entitlement '{entitlement}' searched in descriptions of {len(plan_invoices)} invoice(s)",
                    ),
                )
            )
    return findings
