"""Graph state. It holds conversation facts and references only. No computed findings or amounts are stored here.

Recomputed values come from the tools on every turn, so a stored finding can never go stale.
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    user_id: str
    as_of: str  # ISO date; changed in chat with set_as_of_date
    active_plan_id: str | None  # plan in focus, resolved for follow-up questions
    pending: dict | None  # the one proposal waiting for approval, if any
    steps: int  # model calls this turn (loop guard)
    retries: int  # grounding regenerations this turn
    guard_blocked: bool  # the guard refused this turn
