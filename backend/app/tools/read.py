"""Read tools: look things up, never change state. Each returns a dict; errors come back as {"error": ...}.

The domain decides what is a finding. These tools only expose data and the investigation results.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.detectors import investigate
from app.domain.fx import convert
from app.domain.models import DomainError, Ledger, Plan, UnknownPlanError
from app.domain.money import Money
from app.domain.schedule import build_families, build_periods, plan_segments
from app.tools.context import ToolContext, parse_date, resolve_plan_id
from app.tools.serialize import finding_view, invoice_view, money_view, plan_view


def _safe(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Turn domain and input errors into {"error": message} so the model can recover."""

    def wrapper(ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(ctx, **kwargs)
        except DomainError as exc:
            return {"error": str(exc)}

    wrapper.__name__ = fn.__name__
    return wrapper


def _plan_or_raise(ledger: Ledger, plan_id: str) -> Plan:
    plan = next((p for p in ledger.plans if p.plan_id == plan_id), None)
    if plan is None:
        raise UnknownPlanError(f"plan {plan_id} not found")
    return plan


def family_of(ledger: Ledger, plan_id: str) -> list[Plan]:
    """The amendment family containing plan_id, sorted by start date. Raises UnknownPlanError if absent."""
    families = build_families(ledger.plans)
    for members in families.values():
        if any(p.plan_id == plan_id for p in members):
            return members
    raise UnknownPlanError(f"plan {plan_id} not found")


@_safe
def load_plan(ctx: ToolContext, plan_id: str | None = None) -> dict[str, Any]:
    """Plan details, its amendment family, active windows and expected periods. Sets the plan in focus."""
    pid = resolve_plan_id(ctx, plan_id)
    ledger = ctx.load_ledger()
    _plan_or_raise(ledger, pid)
    family = family_of(ledger, pid)
    windows = plan_segments(family, ctx.term_months)
    periods = build_periods(family, ctx.term_months)
    return {
        "plan": plan_view(_plan_or_raise(ledger, pid)),
        "family": [p.plan_id for p in family],
        "active_windows": [{"plan_id": w.plan_id, "start": w.start.isoformat(), "end": w.end.isoformat()} for w in windows],
        "expected_periods": [
            {"plan_id": per.plan_id, "start": per.start.isoformat(), "end": per.end.isoformat(),
             "expected": money_view(per.expected)}
            for per in periods
        ],
        "term_months_assumed": ctx.term_months,
        "__state__": {"active_plan_id": pid},
    }


@_safe
def list_plans(ctx: ToolContext, customer_name: str | None = None) -> dict[str, Any]:
    """All plans, optionally filtered by a case-insensitive customer name substring."""
    ledger = ctx.load_ledger()
    plans = [
        p for p in ledger.plans
        if not customer_name or customer_name.casefold() in p.customer_name.casefold()
    ]
    return {"plans": [plan_view(p) for p in plans], "count": len(plans)}


@_safe
def query_invoices(
    ctx: ToolContext,
    plan_id: str | None = None,
    customer_name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Invoices matching all given filters. Dates are ISO strings; status is 'paid' or 'unpaid'."""
    ledger = ctx.load_ledger()
    start = parse_date(date_from, "date_from") if date_from else None
    end = parse_date(date_to, "date_to") if date_to else None
    rows = []
    for inv in ledger.invoices:
        if plan_id and inv.plan_id != plan_id:
            continue
        if customer_name and customer_name.casefold() not in inv.customer_name.casefold():
            continue
        if start and inv.issue_date < start:
            continue
        if end and inv.issue_date > end:
            continue
        if status and inv.status != status:
            continue
        rows.append(invoice_view(inv))
    return {"invoices": rows, "count": len(rows)}


@_safe
def fx_convert(
    ctx: ToolContext,
    amount: str,
    from_currency: str,
    to_currency: str,
    on_date: str | None = None,
) -> dict[str, Any]:
    """Convert an amount between currencies with the domain FX rule. Defaults to the as-of date."""
    try:
        value = Decimal(str(amount))
    except InvalidOperation as exc:
        raise DomainError(f"amount must be a number, got {amount!r}") from exc
    when = parse_date(on_date, "on_date") if on_date else ctx.as_of
    result = convert(Money(value, from_currency.upper()), to_currency.upper(), when, ctx.load_ledger().fx_rates)
    return {
        "input": money_view(Money(value, from_currency.upper())),
        "converted": money_view(result.money),
        "rate": str(result.rate),
        "rate_date": result.rate_date.isoformat() if result.rate_date else None,
        "method": result.method,
        "evidence": result.describe(Money(value, from_currency.upper())),
    }


@_safe
def investigate_plan(ctx: ToolContext, plan_id: str | None = None) -> dict[str, Any]:
    """Run every detector live for one plan's amendment family. Sets the plan in focus."""
    pid = resolve_plan_id(ctx, plan_id)
    ledger = ctx.load_ledger()
    findings = investigate(ledger, ctx.as_of, ctx.term_months, plan_id=pid)
    return {
        "plan_id": pid,
        "as_of": ctx.as_of.isoformat(),
        "findings": [finding_view(f) for f in findings],
        "count": len(findings),
        "__state__": {"active_plan_id": pid},
    }


@_safe
def scan_customer(ctx: ToolContext, customer_name: str) -> dict[str, Any]:
    """Investigate every plan whose customer name matches, plus orphan invoices that point at them."""
    ledger = ctx.load_ledger()
    plans = [p for p in ledger.plans if customer_name.casefold() in p.customer_name.casefold()]
    if not plans:
        return {"error": f"no plan has a customer matching {customer_name!r}"}
    seen: dict[str, Any] = {}
    for plan in plans:
        for finding in investigate(ledger, ctx.as_of, ctx.term_months, plan_id=plan.plan_id):
            seen.setdefault(finding.finding_id, finding)
    findings = sorted(seen.values(), key=lambda f: (f.plan_id or "", f.kind.value, f.finding_id))
    return {
        "customer_query": customer_name,
        "plans": [p.plan_id for p in plans],
        "findings": [finding_view(f) for f in findings],
        "count": len(findings),
    }


@_safe
def scan_book(ctx: ToolContext) -> dict[str, Any]:
    """Investigate every plan at once. Use for 'all', 'every' or book-wide questions.

    Returns counts per finding kind, the money findings (one per missing or underbilled or overbilled
    period, with ISO dates and amounts), the total shortfall per currency, and the other findings.
    """
    from collections import Counter
    from decimal import Decimal

    from app.domain.detectors import FindingKind

    money_kinds = {FindingKind.MISSING_PERIOD, FindingKind.AMOUNT_UNDERBILLED, FindingKind.AMOUNT_OVERBILLED}
    findings = investigate(ctx.load_ledger(), ctx.as_of, ctx.term_months)
    money = [f for f in findings if f.kind in money_kinds]
    other = [f for f in findings if f.kind not in money_kinds]

    shortfall: dict[str, Decimal] = {}
    for f in money:
        if f.delta is not None and f.delta.amount < 0:
            shortfall[f.delta.currency] = shortfall.get(f.delta.currency, Decimal("0")) + (-f.delta.amount)

    return {
        "as_of": ctx.as_of.isoformat(),
        "counts": dict(Counter(f.kind.value for f in findings)),
        "money_findings": [finding_view(f) for f in money],
        "money_shortfall_total": {ccy: str(v) for ccy, v in shortfall.items()},
        "other_findings": [
            {"kind": f.kind.value, "plan_id": f.plan_id, "invoice_ids": list(f.invoice_ids),
             "candidate_plan_ids": list(f.candidate_plan_ids), "summary": f.summary}
            for f in other
        ],
        "note": "Report every money finding. Quote dates exactly as given (ISO).",
    }


@_safe
def set_as_of_date(ctx: ToolContext, as_of: str) -> dict[str, Any]:
    """Change the investigation date for this conversation. Later results use the new date."""
    new_date: date = parse_date(as_of, "as_of")
    return {"as_of": new_date.isoformat(), "__state__": {"as_of": new_date.isoformat()}}
