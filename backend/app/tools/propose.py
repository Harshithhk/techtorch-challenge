"""Propose tools: build a draft from the domain and park it as the pending proposal. They write nothing.

Each draft stores its `args` so the approval gate can re-derive it from the ledger before writing.
Only one proposal may wait at a time. A second one is refused until the first is approved or rejected.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from app.domain.detectors import investigate
from app.domain.models import Cadence, Ledger, Plan
from app.domain import proposals as domain
from app.domain.proposals import ActionDraft, ProposalError
from app.tools.context import ToolContext, ToolInputError, parse_date, resolve_plan_id
from app.tools.read import _plan_or_raise, _safe, family_of
from app.tools.serialize import draft_view


def _guard_no_pending(ctx: ToolContext) -> None:
    """Refuse a new proposal while one is waiting. Raises ProposalError with the reason."""
    if ctx.pending is not None:
        raise ProposalError(
            f"a proposal is already waiting for approval ({ctx.pending['summary_title']}); "
            "the user must approve or reject it before another is made"
        )


def _decimal(value: Any, field: str) -> Decimal | None:
    """Optional LLM-supplied amount. Returns None if empty, otherwise a Decimal. Never a float."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ToolInputError(f"{field} must be a number, got {value!r}") from exc


def _pending(draft: ActionDraft, args: dict[str, Any], title: str) -> dict[str, Any]:
    """Pending proposal stored in graph state: the draft view plus the args needed to re-derive it."""
    return {
        **draft_view(draft),
        "proposal_id": uuid4().hex[:12],
        "summary_title": title,
        "args": args,
    }


def _findings_for(ctx: ToolContext, ledger: Ledger, plan_id: str) -> list:
    """Live findings for one plan's family. Raises UnknownPlanError if the plan does not exist."""
    _plan_or_raise(ledger, plan_id)
    return investigate(ledger, ctx.as_of, ctx.term_months, plan_id=plan_id)


@_safe
def propose_make_good_invoice(
    ctx: ToolContext,
    plan_id: str | None = None,
    period_start: str | None = None,
    amount: str | None = None,
) -> dict[str, Any]:
    """Draft a make-good invoice for a missing or underbilled period. The amount is the domain gap.

    `amount` is optional and only lowers the amount. It can never exceed the computed gap.
    """
    _guard_no_pending(ctx)
    pid = resolve_plan_id(ctx, plan_id)
    ledger = ctx.load_ledger()
    findings = _findings_for(ctx, ledger, pid)
    start = parse_date(period_start, "period_start") if period_start else None
    requested = _decimal(amount, "amount")
    draft = domain.propose_make_good(findings, pid, start, requested)
    args = {"plan_id": pid, "period_start": draft.period_start.isoformat(), "requested_amount": str(requested) if requested is not None else None}
    title = f"make-good for {pid}"
    pending = _pending(draft, args, title)
    return {"proposal": pending, "awaiting_approval": True, "__pending__": pending, "__state__": {"active_plan_id": pid}}


@_safe
def propose_credit_memo(ctx: ToolContext, invoice_id: str, amount: str | None = None) -> dict[str, Any]:
    """Draft a credit memo for the overbilled excess of one invoice. The amount is the domain excess."""
    _guard_no_pending(ctx)
    ledger = ctx.load_ledger()
    invoice = next((i for i in ledger.invoices if i.invoice_id == invoice_id), None)
    if invoice is None:
        raise ToolInputError(f"invoice {invoice_id} not found")
    if not invoice.plan_id:
        raise ProposalError(f"invoice {invoice_id} has no plan reference, so it cannot be credited automatically")
    findings = _findings_for(ctx, ledger, invoice.plan_id)
    requested = _decimal(amount, "amount")
    draft = domain.propose_credit_memo(findings, invoice_id, requested)
    args = {"invoice_id": invoice_id, "requested_amount": str(requested) if requested is not None else None}
    title = f"credit memo against {invoice_id}"
    pending = _pending(draft, args, title)
    return {"proposal": pending, "awaiting_approval": True, "__pending__": pending, "__state__": {"active_plan_id": draft.plan_id}}


@_safe
def propose_plan_amendment(
    ctx: ToolContext,
    plan_id: str | None = None,
    effective_date: str = "",
    new_total: str | None = None,
    new_cadence: str | None = None,
    add_entitlements: list[str] | None = None,
) -> dict[str, Any]:
    """Draft an amendment that supersedes the plan's latest version from effective_date (ISO date)."""
    _guard_no_pending(ctx)
    pid = resolve_plan_id(ctx, plan_id)
    ledger = ctx.load_ledger()
    members = family_of(ledger, pid)
    base: Plan = members[-1]  # the latest version is the one being amended
    eff = parse_date(effective_date, "effective_date")
    if eff <= base.start_date:
        raise ProposalError(
            f"effective date {eff.isoformat()} must be after {base.plan_id} starts on {base.start_date.isoformat()}"
        )
    total = _decimal(new_total, "new_total")
    cadence = Cadence(new_cadence) if new_cadence else None
    draft = domain.propose_plan_amendment(base, eff, ctx.term_months, new_total=total, new_cadence=cadence,
                                   add_entitlements=add_entitlements or [])
    args = {
        "plan_id": base.plan_id,
        "effective_date": eff.isoformat(),
        "new_total": str(total) if total is not None else None,
        "new_cadence": cadence.value if cadence else None,
        "add_entitlements": list(add_entitlements or []),
    }
    title = f"amendment of {base.plan_id} effective {eff.isoformat()}"
    pending = _pending(draft, args, title)
    return {"proposal": pending, "awaiting_approval": True, "__pending__": pending, "__state__": {"active_plan_id": base.plan_id}}


@_safe
def propose_rollback(ctx: ToolContext, action_id: str | None = None) -> dict[str, Any]:
    """Draft a rollback of an applied action. Defaults to the most recent one. Requires approval."""
    _guard_no_pending(ctx)
    applied = ctx.sandbox.read_applied()
    if not applied:
        raise ProposalError("nothing has been applied yet, so there is nothing to roll back")
    entry = next((e for e in applied if e["action_id"] == action_id), None) if action_id else applied[-1]
    if entry is None:
        raise ProposalError(f"no applied action {action_id}")
    title = f"rollback of {entry['action_id']}"
    pending = {
        "proposal_id": uuid4().hex[:12],
        "summary_title": title,
        "action_kind": "rollback",
        "plan_id": entry["plan_id"],
        "target_action_id": entry["action_id"],
        "reason": f"Undo {entry['kind']} {entry['action_id']}: {entry['summary']}",
        "amount": None,
        "calculation": [f"Applied at {entry['applied_at']}", f"Removes {entry['table']} record {entry['key_value']}"],
        "args": {"action_id": entry["action_id"]},
    }
    return {"proposal": pending, "awaiting_approval": True, "__pending__": pending}

