"""Write operations: apply an approved proposal, or roll one back. NEVER exposed to the model.

Only the approval gate (agent layer) calls these, and only after the human has approved the
pending proposal. Before writing, each proposal is re-derived from the current ledger. If the
amount has changed since it was drafted, the write is refused and the user is asked to re-propose.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.domain.models import CreditMemo, Invoice, Plan, Cadence
from app.domain.proposals import ProposalError, propose_credit_memo, propose_make_good, propose_plan_amendment
from app.tools.context import ToolContext, parse_date
from app.tools.propose import _decimal, _findings_for
from app.tools.read import family_of

STALE_MESSAGE = "the ledger has changed since this proposal was drafted; nothing was written. Please ask for a new proposal."


def _new_action_id() -> str:
    return f"ACT-{uuid4().hex[:10].upper()}"


def _rederive(fn, *args, **kwargs):
    """Run a domain derivation during apply. Any refusal here means the ledger moved, so say so."""
    try:
        return fn(*args, **kwargs)
    except ProposalError as exc:
        raise ProposalError(f"{STALE_MESSAGE} Reason: {exc}") from exc


def _check_unchanged(fresh_amount, pending: dict[str, Any]) -> None:
    """Raise ProposalError if the freshly derived amount differs from the drafted one."""
    drafted = Decimal(pending["amount"]["amount"]) if pending.get("amount") else None
    if drafted is None or fresh_amount.amount != drafted:
        raise ProposalError(STALE_MESSAGE)


def _apply_make_good(ctx: ToolContext, pending: dict[str, Any], action_id: str) -> dict[str, Any]:
    args = pending["args"]
    ledger = ctx.load_ledger()
    findings = _findings_for(ctx, ledger, args["plan_id"])
    fresh = _rederive(propose_make_good, findings, args["plan_id"], parse_date(args["period_start"], "period_start"),
                      _decimal(args.get("requested_amount"), "requested_amount"))
    _check_unchanged(fresh.amount, pending)
    plan = next(p for p in ledger.plans if p.plan_id == args["plan_id"])
    start = fresh.period_start
    record = Invoice(
        invoice_id=f"INV-MG-{action_id[4:]}",
        plan_id=plan.plan_id,
        customer_name=plan.customer_name,
        issue_date=start,
        due_date=start + timedelta(days=30),
        amount_invoiced=fresh.amount.amount,
        currency=fresh.amount.currency,
        status="unpaid",
        description=f"Make-good invoice for period {start.isoformat()} to {fresh.period_end.isoformat()}",
    )
    ctx.sandbox.commit_action(action_id=action_id, kind=pending["action_kind"], table="invoices",
                              record=record.model_dump(mode="json"), plan_id=plan.plan_id,
                              summary=pending["reason"], user_id=ctx.user_id)
    return {"record_id": record.invoice_id, "table": "invoices"}


def _apply_credit_memo(ctx: ToolContext, pending: dict[str, Any], action_id: str) -> dict[str, Any]:
    args = pending["args"]
    ledger = ctx.load_ledger()
    invoice = next((i for i in ledger.invoices if i.invoice_id == args["invoice_id"]), None)
    if invoice is None or not invoice.plan_id:
        raise ProposalError(STALE_MESSAGE)
    findings = _findings_for(ctx, ledger, invoice.plan_id)
    fresh = _rederive(propose_credit_memo, findings, args["invoice_id"],
                      _decimal(args.get("requested_amount"), "requested_amount"))
    _check_unchanged(fresh.amount, pending)
    record = CreditMemo(
        memo_id=f"CM-{action_id[4:]}",
        plan_id=invoice.plan_id,
        invoice_id=invoice.invoice_id,
        amount=fresh.amount.amount,
        currency=fresh.amount.currency,
        issue_date=ctx.as_of,
        reason=f"Overbilling correction for {invoice.invoice_id}",
    )
    ctx.sandbox.commit_action(action_id=action_id, kind=pending["action_kind"], table="credit_memos",
                              record=record.model_dump(mode="json"), plan_id=invoice.plan_id,
                              summary=pending["reason"], user_id=ctx.user_id)
    return {"record_id": record.memo_id, "table": "credit_memos"}


def _apply_amendment(ctx: ToolContext, pending: dict[str, Any], action_id: str) -> dict[str, Any]:
    args = pending["args"]
    ledger = ctx.load_ledger()
    members = family_of(ledger, args["plan_id"])
    base: Plan = members[-1]
    eff = parse_date(args["effective_date"], "effective_date")
    cadence = Cadence(args["new_cadence"]) if args.get("new_cadence") else None
    fresh = _rederive(propose_plan_amendment, base, eff, ctx.term_months,
                      new_total=_decimal(args.get("new_total"), "new_total"),
                      new_cadence=cadence, add_entitlements=args.get("add_entitlements") or [])
    if fresh.change_set != pending.get("change_set"):
        raise ProposalError(STALE_MESSAGE)
    new_id = f"{members[0].plan_id}-A{len(members)}"
    after = fresh.change_set["after"]
    record = Plan(
        plan_id=new_id,
        customer_name=base.customer_name,
        total_value=Decimal(after["total_value"]),
        currency=base.currency,
        cadence=Cadence(after["cadence"]),
        start_date=eff,
        entitlements=tuple(after["entitlements"]),
        notes=f"Amendment of {base.plan_id} effective {eff.isoformat()} (applied from sandbox)",
        amends=base.plan_id,
    )
    ctx.sandbox.commit_action(action_id=action_id, kind=pending["action_kind"], table="plans",
                              record=record.model_dump(mode="json"), plan_id=new_id,
                              summary=pending["reason"], user_id=ctx.user_id)
    return {"record_id": new_id, "table": "plans"}


def _apply_rollback(ctx: ToolContext, pending: dict[str, Any], action_id: str) -> dict[str, Any]:
    target = pending["args"]["action_id"]
    entry = ctx.sandbox.revert_action(action_id=target, user_id=ctx.user_id)
    return {"rolled_back": target, "table": entry["table"], "record_id": entry["key_value"]}


_HANDLERS = {
    "make_good_invoice": _apply_make_good,
    "credit_memo": _apply_credit_memo,
    "plan_amendment": _apply_amendment,
    "rollback": _apply_rollback,
}


def apply_pending(ctx: ToolContext, pending: dict[str, Any]) -> dict[str, Any]:
    """Execute an approved proposal. Re-derives it first, then writes exactly one record and logs it.

    Input: context (ctx.pending may be set), the pending proposal dict from state.
    Output: {"status": "applied", "action_id", ...}.
    Raises ProposalError (nothing written) if the ledger changed or the proposal is no longer valid.
    """
    handler = _HANDLERS.get(pending["action_kind"])
    if handler is None:
        raise ProposalError(f"unknown action kind {pending['action_kind']}")
    action_id = _new_action_id()
    result = handler(ctx, pending, action_id)
    return {"status": "applied", "action_id": action_id if pending["action_kind"] != "rollback" else pending["args"]["action_id"],
            "action_kind": pending["action_kind"], **result}


def reject_pending(ctx: ToolContext, pending: dict[str, Any]) -> dict[str, Any]:
    """Record a rejection in the audit log. Writes no ledger data."""
    action_id = f"REJ-{pending['proposal_id']}"
    ctx.sandbox.log_rejection(action_id=action_id, kind=pending["action_kind"], plan_id=pending.get("plan_id") or "",
                              summary=pending["reason"], user_id=ctx.user_id)
    return {"status": "rejected", "action_kind": pending["action_kind"], "action_id": action_id}
