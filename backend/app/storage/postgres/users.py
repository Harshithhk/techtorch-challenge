"""Dummy login: user_id -> stable thread_id, stored in Postgres.

The thread id is uuid5 of the user id, so logging in again always resumes the same conversation,
even if the row were lost. The row is still stored so the mapping is explicit and queryable.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import psycopg

_NAMESPACE = uuid.UUID("6f1c2a2e-7b3d-4c5e-9a10-52f1e0a1b2c3")  # fixed namespace for thread ids
USER_ID_PATTERN = r"^[A-Za-z0-9_.@-]{1,64}$"

DDL = """
CREATE TABLE IF NOT EXISTS users (
    user_id    TEXT PRIMARY KEY,
    thread_id  TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True)
class UserRecord:
    user_id: str
    thread_id: str


def thread_id_for(user_id: str) -> str:
    """Deterministic thread id for a user. Input: user id. Output: UUID string (same input, same output)."""
    return str(uuid.uuid5(_NAMESPACE, f"user:{user_id}"))


class UserRepository:
    """Async repository for the users table. One short-lived connection per call keeps it simple."""

    def __init__(self, conninfo: str) -> None:
        self._conninfo = conninfo

    async def ensure_schema(self) -> None:
        """Create the users table if it does not exist. Safe to call on every startup."""
        async with await psycopg.AsyncConnection.connect(self._conninfo, autocommit=True) as conn:
            await conn.execute(DDL)

    async def login(self, user_id: str) -> UserRecord:
        """Create the user if new, then return the record. Idempotent.

        Input: user id (already validated by the API layer). Output: UserRecord with its thread id.
        """
        thread_id = thread_id_for(user_id)
        async with await psycopg.AsyncConnection.connect(self._conninfo, autocommit=True) as conn:
            await conn.execute(
                "INSERT INTO users (user_id, thread_id) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
                (user_id, thread_id),
            )
            row = await (await conn.execute(
                "SELECT user_id, thread_id FROM users WHERE user_id = %s", (user_id,)
            )).fetchone()
        return UserRecord(user_id=row[0], thread_id=row[1])

    async def get(self, user_id: str) -> UserRecord | None:
        """Return the user record, or None if the user has never logged in."""
        async with await psycopg.AsyncConnection.connect(self._conninfo, autocommit=True) as conn:
            row = await (await conn.execute(
                "SELECT user_id, thread_id FROM users WHERE user_id = %s", (user_id,)
            )).fetchone()
        return UserRecord(user_id=row[0], thread_id=row[1]) if row else None
