"""FastAPI dependencies: who is calling (dummy login) and the conversation service."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status

from app.agent.service import AgentService
from app.storage.postgres.users import UserRecord


def get_users(request: Request):
    return request.app.state.users


async def current_user(request: Request, x_user_id: str | None = Header(default=None)) -> UserRecord:
    """Resolve the X-User-Id header to a logged-in user. Raises 401 if the header is missing or unknown."""
    if not x_user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-User-Id header; log in first")
    user = await request.app.state.users.get(x_user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown user; log in first")
    return user


def get_service(request: Request) -> AgentService:
    """The conversation service. Raises 503 when no model is configured (no ANTHROPIC_API_KEY)."""
    service = request.app.state.service
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "the assistant is not configured; set ANTHROPIC_API_KEY")
    return service


UserDep = Depends(current_user)
ServiceDep = Depends(get_service)
