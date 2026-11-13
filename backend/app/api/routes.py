"""HTTP routes only. Each handler delegates to a service; none contains business logic."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.agent.service import AgentService
from app.api.deps import ServiceDep, UserDep, get_users
from app.api.schemas import (
    AuditOut,
    ChatIn,
    HealthOut,
    HistoryOut,
    LoginIn,
    LoginOut,
    PendingOut,
    ResumeIn,
    TurnOut,
)
from app.storage.postgres.users import UserRecord

router = APIRouter()


@router.get("/health", response_model=HealthOut)
async def health(request: Request) -> HealthOut:
    """Liveness and whether the assistant has a model configured."""
    return HealthOut(status="ok", llm_configured=request.app.state.service is not None)


@router.post("/auth/login", response_model=LoginOut)
async def login(body: LoginIn, users=Depends(get_users)) -> LoginOut:
    """Dummy login: creates the user if new and returns the thread id that holds the conversation."""
    record = await users.login(body.user_id)
    return LoginOut(user_id=record.user_id, thread_id=record.thread_id)


@router.post("/chat", response_model=TurnOut)
async def chat(body: ChatIn, user: UserRecord = UserDep, service: AgentService = ServiceDep) -> TurnOut:
    """Send a message in the caller's conversation. May return awaiting_approval with a proposal."""
    result = await service.send(user.thread_id, user.user_id, body.message)
    return TurnOut.from_result(result)


@router.post("/chat/resume", response_model=TurnOut)
async def resume(body: ResumeIn, user: UserRecord = UserDep, service: AgentService = ServiceDep) -> TurnOut:
    """Approve or reject the caller's waiting proposal. Nothing is written on reject."""
    result = await service.decide(user.thread_id, body.decision)
    return TurnOut.from_result(result)


@router.get("/chat/history", response_model=HistoryOut)
async def history(user: UserRecord = UserDep, service: AgentService = ServiceDep) -> HistoryOut:
    """The caller's visible chat history."""
    return HistoryOut(messages=await service.history(user.thread_id))


@router.get("/chat/pending", response_model=PendingOut)
async def pending(user: UserRecord = UserDep, service: AgentService = ServiceDep) -> PendingOut:
    """The proposal waiting for the caller's decision, if any."""
    return PendingOut(pending=await service.pending(user.thread_id))


@router.get("/audit-log", response_model=AuditOut)
async def audit_log(request: Request, user: UserRecord = UserDep) -> AuditOut:
    """The append-only sandbox audit trail, oldest first."""
    return AuditOut(events=request.app.state.sandbox.read_audit())
