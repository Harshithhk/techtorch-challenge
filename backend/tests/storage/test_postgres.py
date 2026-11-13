"""Postgres users and checkpointer. Skipped automatically when the compose database is not reachable."""
import psycopg
import pytest

from app.settings import get_settings
from app.storage.postgres.checkpointer import open_checkpointer
from app.storage.postgres.users import UserRepository, thread_id_for

URL = get_settings().database_url


def _db_up() -> bool:
    try:
        with psycopg.connect(URL, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not _db_up(), reason="postgres not reachable; run docker compose up -d"),
]


async def test_login_is_idempotent_and_thread_id_is_stable():
    """Logging in twice returns the same thread id, which matches the deterministic function."""
    repo = UserRepository(URL)
    await repo.ensure_schema()
    first = await repo.login("test-user-stable")
    second = await repo.login("test-user-stable")
    assert first == second
    assert first.thread_id == thread_id_for("test-user-stable")


async def test_unknown_user_returns_none():
    """A user who never logged in has no record."""
    repo = UserRepository(URL)
    await repo.ensure_schema()
    assert await repo.get("never-seen-user-xyz") is None


async def test_checkpointer_creates_its_tables():
    """The LangGraph saver opens and its setup() runs without error."""
    async with open_checkpointer(URL) as saver:
        assert saver is not None
