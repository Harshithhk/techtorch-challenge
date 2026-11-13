"""LangGraph Postgres checkpointer, so a conversation survives a restart.

open_checkpointer() is an async context manager. It yields a ready-to-use saver with its
tables created, and the connection is closed on exit.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


@asynccontextmanager
async def open_checkpointer(conninfo: str) -> AsyncIterator[AsyncPostgresSaver]:
    """Yield an AsyncPostgresSaver whose LangGraph tables already exist.

    Input: Postgres connection string. Output: the saver, used inside the `async with` block.
    """
    async with AsyncPostgresSaver.from_conn_string(conninfo) as saver:
        await saver.setup()
        yield saver
