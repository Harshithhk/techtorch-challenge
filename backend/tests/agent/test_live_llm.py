"""Opt-in smoke tests against the real Claude model. Run with RUN_LIVE_TESTS=1 and ANTHROPIC_API_KEY set.

They check plumbing only (tool calling works, a grounded answer comes back). Results are not asserted
beyond that, because model wording varies. Skipped in the normal test run.
"""
import os
from datetime import date

import pytest

from app.agent.graph import build_graph
from app.agent.nodes import AgentDeps
from app.agent.service import AgentService
from app.settings import get_settings
from app.storage.ledger import load_ledger
from app.storage.sandbox import SandboxStore
from langgraph.checkpoint.memory import InMemorySaver

settings = get_settings()
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("RUN_LIVE_TESTS") != "1" or not settings.anthropic_api_key,
                       reason="set RUN_LIVE_TESTS=1 and ANTHROPIC_API_KEY to run"),
]


async def test_live_model_investigates_and_answers(tmp_path):
    """The real model calls investigate_plan for C-1001 and returns a non-empty answer."""
    from langchain_anthropic import ChatAnthropic

    llm = ChatAnthropic(model=settings.agent_model, api_key=settings.anthropic_api_key)
    deps = AgentDeps(llm=llm, load_ledger=lambda: load_ledger(settings.data_dir, tmp_path / "sb"),
                     sandbox=SandboxStore(tmp_path / "sb"), term_months=12, default_as_of=date(2025, 12, 31))
    service = AgentService(build_graph(deps, InMemorySaver()), deps.default_as_of)
    result = await service.send("live-1", "live-user", "Check plan C-1001 for revenue leakage.")
    assert result.status in {"answered", "awaiting_approval"}
    assert result.reply
