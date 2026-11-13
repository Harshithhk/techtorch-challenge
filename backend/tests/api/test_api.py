"""HTTP API end to end, with the real lifespan, a scripted model, in-memory users and checkpointer.

Covers: dummy login, auth on every chat route, approve and reject over HTTP, thread isolation,
history and pending endpoints, the audit log, and the 503 when no model is configured.
"""
from contextlib import asynccontextmanager

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.main import create_app
from app.settings import Settings, get_settings
from app.storage.postgres.users import UserRecord, thread_id_for
from tests.agent.fakes import ScriptedChatModel, say, tool_call

REAL = get_settings().data_dir
pytestmark = pytest.mark.skipif(not (REAL / "invoices.json").exists(), reason="real data not present")

MAKE_GOOD = tool_call("propose_make_good_invoice", {"plan_id": "C-1001", "period_start": "2025-09-01"}, "mg")


class InMemoryUsers:
    """Same interface as UserRepository, without Postgres."""

    def __init__(self) -> None:
        self._rows: dict[str, UserRecord] = {}

    async def login(self, user_id: str) -> UserRecord:
        record = self._rows.setdefault(user_id, UserRecord(user_id, thread_id_for(user_id)))
        return record

    async def get(self, user_id: str) -> UserRecord | None:
        return self._rows.get(user_id)


@asynccontextmanager
async def _memory_checkpointer():
    yield InMemorySaver()


def _settings(tmp_path) -> Settings:
    return Settings(_env_file=None, data_dir=REAL, sandbox_dir=tmp_path / "sandbox", anthropic_api_key="")


@asynccontextmanager
async def running_client(tmp_path, script=None, with_llm=True):
    """Start the app's lifespan and yield an HTTP client plus the scripted model (or None)."""
    llm = ScriptedChatModel(script=list(script or [])) if with_llm else None
    app = create_app(_settings(tmp_path), llm=llm, users=InMemoryUsers(), checkpointer=_memory_checkpointer)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, llm


async def test_login_validates_and_chat_requires_identity(tmp_path):
    """Login returns a thread id. Bad ids are 422. Chat without a known user is 401."""
    async with running_client(tmp_path) as (client, _):
        ok = await client.post("/auth/login", json={"user_id": "alice"})
        assert ok.status_code == 200 and ok.json()["thread_id"] == thread_id_for("alice")
        assert (await client.post("/auth/login", json={"user_id": "bad user!"})).status_code == 422
        assert (await client.post("/chat", json={"message": "hi"})).status_code == 401
        assert (await client.post("/chat", headers={"X-User-Id": "nobody"}, json={"message": "hi"})).status_code == 401


async def test_proposal_then_approve_over_http_writes_once(tmp_path):
    """Chat returns a pending proposal and writes nothing. Approve writes one record and logs it."""
    async with running_client(tmp_path, [MAKE_GOOD, say("Applied the make-good for 8000.00 USD.")]) as (client, _):
        headers = {"X-User-Id": "alice"}
        await client.post("/auth/login", json={"user_id": "alice"})

        chat = await client.post("/chat", headers=headers, json={"message": "Make good September for C-1001"})
        body = chat.json()
        assert chat.status_code == 200 and body["status"] == "awaiting_approval"
        assert body["pending"]["amount"] == {"amount": "8000.00", "currency": "USD"}
        assert (await client.get("/audit-log", headers=headers)).json()["events"] == []
        assert (await client.get("/chat/pending", headers=headers)).json()["pending"]["action_kind"] == "make_good_invoice"

        decided = await client.post("/chat/resume", headers=headers, json={"decision": "approve"})
        assert decided.json()["status"] == "answered"
        events = (await client.get("/audit-log", headers=headers)).json()["events"]
        assert [e["event"] for e in events] == ["applied"]
        assert (await client.get("/chat/pending", headers=headers)).json()["pending"] is None


async def test_reject_over_http_writes_nothing_and_logs_rejection(tmp_path):
    """Reject leaves the ledger alone and records a rejected event. Any decision other than approve or reject is 422."""
    async with running_client(tmp_path, [MAKE_GOOD, say("No change made.")]) as (client, _):
        headers = {"X-User-Id": "alice"}
        await client.post("/auth/login", json={"user_id": "alice"})
        await client.post("/chat", headers=headers, json={"message": "Make good September"})

        assert (await client.post("/chat/resume", headers=headers, json={"decision": "maybe"})).status_code == 422
        decided = await client.post("/chat/resume", headers=headers, json={"decision": "reject"})
        assert decided.status_code == 200
        events = (await client.get("/audit-log", headers=headers)).json()["events"]
        assert [e["event"] for e in events] == ["rejected"]


async def test_threads_are_isolated_between_users(tmp_path):
    """Alice's waiting proposal does not block Bob, Bob cannot see or decide it, and her proposal stays put."""
    async with running_client(tmp_path, [MAKE_GOOD, say("Hi Bob.")]) as (client, _):
        await client.post("/auth/login", json={"user_id": "alice"})
        await client.post("/auth/login", json={"user_id": "bob"})
        alice, bob = {"X-User-Id": "alice"}, {"X-User-Id": "bob"}

        await client.post("/chat", headers=alice, json={"message": "Make good September"})
        bob_turn = await client.post("/chat", headers=bob, json={"message": "hello"})
        assert bob_turn.json()["status"] == "answered" and bob_turn.json()["reply"] == "Hi Bob."

        bob_history = (await client.get("/chat/history", headers=bob)).json()["messages"]
        assert all("September" not in m["content"] for m in bob_history)
        assert (await client.post("/chat/resume", headers=bob, json={"decision": "approve"})).json()["status"] == "no_pending"
        assert (await client.get("/chat/pending", headers=alice)).json()["pending"] is not None


async def test_chat_is_refused_with_503_when_no_model_is_configured(tmp_path):
    """Without an API key the chat endpoints answer 503 with a clear message. Health still works."""
    async with running_client(tmp_path, with_llm=False) as (client, _):
        await client.post("/auth/login", json={"user_id": "alice"})
        response = await client.post("/chat", headers={"X-User-Id": "alice"}, json={"message": "hi"})
        assert response.status_code == 503
        health = (await client.get("/health")).json()
        assert health == {"status": "ok", "llm_configured": False}


async def test_history_shows_the_conversation(tmp_path):
    """History lists the user's message and the assistant's answer, in order."""
    async with running_client(tmp_path, [say("Hello from the investigator.")]) as (client, _):
        await client.post("/auth/login", json={"user_id": "alice"})
        await client.post("/chat", headers={"X-User-Id": "alice"}, json={"message": "Hi there"})
        messages = (await client.get("/chat/history", headers={"X-User-Id": "alice"})).json()["messages"]
        assert messages == [
            {"role": "user", "content": "Hi there"},
            {"role": "assistant", "content": "Hello from the investigator."},
        ]
