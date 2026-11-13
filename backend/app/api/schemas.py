"""Request and response shapes for the HTTP API. Validation lives here; business rules do not."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent.service import TurnResult
from app.storage.postgres.users import USER_ID_PATTERN


class LoginIn(BaseModel):
    user_id: str = Field(pattern=USER_ID_PATTERN, description="Dummy login: any id of 1-64 safe characters.")


class LoginOut(BaseModel):
    user_id: str
    thread_id: str


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ResumeIn(BaseModel):
    decision: Literal["approve", "reject"]


class TurnOut(BaseModel):
    """One conversation step. status is answered | awaiting_approval | blocked | busy | decided | no_pending."""

    model_config = ConfigDict(extra="forbid")

    status: str
    reply: str
    pending: dict[str, Any] | None = None

    @classmethod
    def from_result(cls, result: TurnResult) -> "TurnOut":
        return cls(status=result.status, reply=result.reply, pending=result.pending)


class HistoryOut(BaseModel):
    messages: list[dict[str, str]]


class PendingOut(BaseModel):
    pending: dict[str, Any] | None


class AuditOut(BaseModel):
    events: list[dict[str, Any]]


class HealthOut(BaseModel):
    status: str
    llm_configured: bool
