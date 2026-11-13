"""Turns findings into action drafts. Pure domain. Writes nothing; that is the approval gate's job.

The amount always comes from the domain (the finding's gap). An LLM-supplied `requested_amount`
is a user-dictated override: it is accepted only when it is positive and no larger than the
computed gap, and it is labelled as an override. Anything larger is refused, so the LLM can
never inflate a make-good or credit memo.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.domain.detectors import Finding, FindingKind
from app.domain.models import ActionKind, Cadence, DomainError, Plan
from app.domain.money import Money
from app.domain.schedule import expected_per_period


class ProposalError(DomainError):
    """The requested action cannot be drafted. The message is shown to the user as-is."""


@dataclass(frozen=True)
class ActionDraft:
    """A proposed, not-yet-applied action. Immutable, and fully explainable.

    amount: the money effect in the plan currency (make-good: positive; credit memo: positive;
    amendment: the change in total_value, which may be negative).
    change_set: only for plan amendments. The before and after terms.
    """

    action_kind: ActionKind
    plan_id: str
    amount: Money
    target_invoice_id: str | None
    period_start: date | None
    period_end: date | None
    reason: str
    calculation: tuple[str, ...]
    finding_id: str | None
    requested_amount: Decimal | None = None
    change_set: dict | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _resolve_amount(gap: Money, requested: Decimal | None) -> tuple[Money, tuple[str, ...]]:
    """Pick the amount to draft: the computed gap, or a smaller positive user override.

    Input: computed gap (positive Money), optional requested Decimal.
    Output: (Money, warnings). Raises ProposalError if requested is <= 0 or exceeds the gap.
    """
    if requested is None:
        return gap, ()
    if requested <= 0:
        raise ProposalError("requested amount must be positive")
    if requested > gap.amount:
        raise ProposalError(f"requested {requested} {gap.currency} exceeds the computed gap {gap}; refused")
    warning = ()
    if requested < gap.amount:
        warning = (f"user override: {requested} {gap.currency} is below the computed gap {gap}",)
    return Money(requested, gap.currency), warning


def propose_make_good(
    findings: Iterable[Finding],
    plan_id: str,
    period_start: date | None = None,
    requested_amount: Decimal | None = None,
) -> ActionDraft:
    """Draft a make-good invoice for a missing or underbilled period.

    Input: current findings (from investigate), plan_id, optional period_start (required when the
    plan has more than one open gap), optional requested_amount override.
    Output: ActionDraft with amount = the period gap (or a smaller override).
    Raises ProposalError when there is no matching gap, or several and no period is given.
    """
    candidates = [
        f
        for f in findings
        if f.plan_id == plan_id
        and f.kind in (FindingKind.MISSING_PERIOD, FindingKind.AMOUNT_UNDERBILLED)
        and (period_start is None or f.period_start == period_start)
    ]
    if not candidates:
        raise ProposalError(f"no missing or underbilled period found for plan {plan_id}")
    if len(candidates) > 1:
        starts = ", ".join(sorted(f.period_start.isoformat() for f in candidates if f.period_start))
        raise ProposalError(f"plan {plan_id} has several open gaps; specify a period start from: {starts}")

    finding = candidates[0]
    assert finding.delta is not None and finding.delta.amount < 0  # gap findings always carry a negative delta
    gap = -finding.delta
    amount, warnings = _resolve_amount(gap, requested_amount)
    calculation = finding.calculation + (
        (f"User requested {requested_amount}; drafted {amount}",) if requested_amount is not None else ()
    )
    return ActionDraft(
        action_kind=ActionKind.MAKE_GOOD_INVOICE,
        plan_id=plan_id,
        amount=amount,
        target_invoice_id=None,
        period_start=finding.period_start,
        period_end=finding.period_end,
        reason=finding.summary,
        calculation=calculation,
        finding_id=finding.finding_id,
        requested_amount=requested_amount,
        warnings=warnings,
    )


def propose_credit_memo(
    findings: Iterable[Finding],
    invoice_id: str,
    requested_amount: Decimal | None = None,
) -> ActionDraft:
    """Draft a credit memo for the overbilled excess of one invoice.

    Input: current findings, invoice_id, optional requested_amount override (must be <= the excess).
    Output: ActionDraft with amount = excess in the plan currency, targeting that invoice.
    Raises ProposalError if the invoice has no overbilling finding.
    """
    candidates = [f for f in findings if f.kind is FindingKind.AMOUNT_OVERBILLED and f.target_invoice_id == invoice_id]
    if not candidates:
        raise ProposalError(f"invoice {invoice_id} has no overbilling to credit")
    finding = candidates[0]
    assert finding.delta is not None and finding.delta.amount > 0
    excess = finding.delta
    amount, warnings = _resolve_amount(excess, requested_amount)
    calculation = finding.calculation + (
        (f"User requested {requested_amount}; drafted {amount}",) if requested_amount is not None else ()
    )
    return ActionDraft(
        action_kind=ActionKind.CREDIT_MEMO,
        plan_id=finding.plan_id or "",
        amount=amount,
        target_invoice_id=invoice_id,
        period_start=finding.period_start,
        period_end=finding.period_end,
        reason=finding.summary,
        calculation=calculation,
        finding_id=finding.finding_id,
        requested_amount=requested_amount,
        warnings=warnings,
    )


def propose_plan_amendment(
    plan: Plan,
    effective_date: date,
    term_months: int,
    new_total: Decimal | None = None,
    new_cadence: Cadence | None = None,
    add_entitlements: Iterable[str] = (),
) -> ActionDraft:
    """Draft a plan amendment that supersedes `plan` from effective_date.

    Input: current plan version, effective date, term length (for the per-period comparison),
        and at least one of: new_total, new_cadence, add_entitlements.
    Output: ActionDraft whose change_set holds before/after terms and per-period amounts.
    Raises ProposalError when nothing changes, the total is not positive, or the date is before the plan starts.
    Currency is never changed by an amendment.
    """
    new_entitlements = tuple(dict.fromkeys([*plan.entitlements, *add_entitlements]))
    entitlements_changed = new_entitlements != plan.entitlements
    total_changed = new_total is not None and new_total != plan.total_value
    cadence_changed = new_cadence is not None and new_cadence != plan.cadence
    if not (total_changed or cadence_changed or entitlements_changed):
        raise ProposalError("amendment changes nothing")
    if new_total is not None and new_total <= 0:
        raise ProposalError("new total must be positive")
    if effective_date < plan.start_date:
        raise ProposalError(f"effective date {effective_date.isoformat()} is before the plan starts {plan.start_date.isoformat()}")

    after_total = new_total if new_total is not None else plan.total_value
    after_cadence = new_cadence if new_cadence is not None else plan.cadence
    after_plan = plan.model_copy(update={"total_value": after_total, "cadence": after_cadence, "entitlements": new_entitlements})
    before_per = expected_per_period(plan, term_months)
    after_per = expected_per_period(after_plan, term_months)
    total_delta = Money(after_total - plan.total_value, plan.currency)

    change_set = {
        "amends": plan.plan_id,
        "customer_name": plan.customer_name,
        "currency": plan.currency,
        "effective_date": effective_date.isoformat(),
        "before": {"total_value": str(plan.total_value), "cadence": plan.cadence.value, "entitlements": list(plan.entitlements)},
        "after": {"total_value": str(after_total), "cadence": after_cadence.value, "entitlements": list(new_entitlements)},
        "per_period_before": str(before_per.amount),
        "per_period_after": str(after_per.amount),
    }
    calculation = (
        f"Total {plan.total_value} -> {after_total} {plan.currency} (change {total_delta})",
        f"Cadence {plan.cadence.value} -> {after_cadence.value}",
        f"Expected per period {before_per} -> {after_per} over a {term_months}-month term",
    )
    return ActionDraft(
        action_kind=ActionKind.PLAN_AMENDMENT,
        plan_id=plan.plan_id,
        amount=total_delta,
        target_invoice_id=None,
        period_start=effective_date,
        period_end=None,
        reason=f"Amend {plan.plan_id} effective {effective_date.isoformat()}",
        calculation=calculation,
        finding_id=None,
        change_set=change_set,
    )
