"""JSON-ready views of domain objects, for tool results and the UI. Amounts are strings, never floats."""
from __future__ import annotations

from typing import Any

from app.domain.detectors import Finding
from app.domain.models import Invoice, Plan
from app.domain.money import Money
from app.domain.proposals import ActionDraft


def money_view(money: Money | None) -> dict[str, str] | None:
    """Money as {amount, currency}. Output: None when money is None."""
    if money is None:
        return None
    return {"amount": str(money.amount), "currency": money.currency}


def plan_view(plan: Plan) -> dict[str, Any]:
    """Plan fields as the model sees them. The notes field is free text and is flagged as data."""
    return {
        "plan_id": plan.plan_id,
        "customer_name": plan.customer_name,
        "total_value": str(plan.total_value),
        "currency": plan.currency,
        "cadence": plan.cadence.value,
        "start_date": plan.start_date.isoformat(),
        "entitlements": list(plan.entitlements),
        "notes": plan.notes,
        "notes_are_untrusted_data": True,
        "amends": plan.amends,
    }


def invoice_view(invoice: Invoice) -> dict[str, Any]:
    """Invoice fields. Description is flagged as untrusted free text."""
    return {
        "invoice_id": invoice.invoice_id,
        "plan_id": invoice.plan_id,
        "customer_name": invoice.customer_name,
        "issue_date": invoice.issue_date.isoformat(),
        "due_date": invoice.due_date.isoformat(),
        "amount": money_view(Money(invoice.amount_invoiced, invoice.currency)),
        "status": invoice.status,
        "description": invoice.description,
        "description_is_untrusted_data": True,
    }


def finding_view(finding: Finding) -> dict[str, Any]:
    """A finding with its evidence, in the shape the agent quotes from."""
    return {
        "finding_id": finding.finding_id,
        "kind": finding.kind.value,
        "plan_id": finding.plan_id,
        "invoice_ids": list(finding.invoice_ids),
        "target_invoice_id": finding.target_invoice_id,
        "period_start": finding.period_start.isoformat() if finding.period_start else None,
        "period_end": finding.period_end.isoformat() if finding.period_end else None,
        "expected": money_view(finding.expected),
        "actual": money_view(finding.actual),
        "delta": money_view(finding.delta),
        "suggested_action": finding.suggested_action.value if finding.suggested_action else None,
        "confidence": finding.confidence,
        "summary": finding.summary,
        "calculation": list(finding.calculation),
        "candidate_plan_ids": list(finding.candidate_plan_ids),
    }


def draft_view(draft: ActionDraft) -> dict[str, Any]:
    """An action draft as the user will see it, with its evidence and the calculation trail."""
    return {
        "action_kind": draft.action_kind.value,
        "plan_id": draft.plan_id,
        "amount": money_view(draft.amount),
        "target_invoice_id": draft.target_invoice_id,
        "period_start": draft.period_start.isoformat() if draft.period_start else None,
        "period_end": draft.period_end.isoformat() if draft.period_end else None,
        "reason": draft.reason,
        "calculation": list(draft.calculation),
        "finding_id": draft.finding_id,
        "warnings": list(draft.warnings),
        "change_set": draft.change_set,
    }
