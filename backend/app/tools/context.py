"""Per-call tool context: who is asking, the as-of date, the plan in focus, and any pending proposal.

A fresh ToolContext is built from the graph state for each tool call. A state change (such as a
new as-of date) is therefore visible to the next tool. Data is read through load_ledger, so tests
can pass fixtures in.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from app.domain.models import DomainError, Ledger
from app.storage.sandbox import SandboxStore


class ToolInputError(DomainError):
    """A tool argument is missing or malformed. The message is returned to the model so it can retry."""


@dataclass
class ToolContext:
    """Dependencies and conversation state needed by one tool call.

    user_id: owner of the conversation (written to the audit log).
    as_of: investigation date. Periods starting after it are not judged.
    term_months: assumed contract term.
    load_ledger: returns a fresh snapshot (base plus sandbox) each time it is called.
    sandbox: the writable store. Only the write module uses it.
    active_plan_id: plan in focus, used when a tool is called without a plan id.
    pending: the proposal waiting for approval, if any. No new proposal is accepted while one waits.
    """

    user_id: str
    as_of: date
    term_months: int
    load_ledger: Callable[[], Ledger]
    sandbox: SandboxStore
    active_plan_id: str | None = None
    pending: dict | None = None


def resolve_plan_id(ctx: ToolContext, plan_id: str | None) -> str:
    """Use the given plan id, or fall back to the plan in focus.

    Input: explicit plan id (may be None or empty), context. Output: plan id string.
    Raises ToolInputError when neither is available, asking the user to name a plan.
    """
    if plan_id:
        return plan_id.strip()
    if ctx.active_plan_id:
        return ctx.active_plan_id
    raise ToolInputError("no plan given and none is in focus; ask the user which plan they mean")


def parse_date(value: str, field: str) -> date:
    """Parse an ISO date (YYYY-MM-DD). Raises ToolInputError with the field name if invalid."""
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ToolInputError(f"{field} must be an ISO date like 2025-09-01, got {value!r}") from exc
