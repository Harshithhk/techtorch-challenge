"""Build a service over the real data, with a temporary sandbox and an in-memory checkpointer."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import build_graph
from app.agent.nodes import AgentDeps
from app.agent.service import AgentService
from app.settings import get_settings
from app.storage.ledger import load_ledger
from app.storage.sandbox import SandboxStore
from tests.agent.fakes import ScriptedChatModel

AS_OF = date(2025, 12, 31)
REAL = get_settings().data_dir


def build(tmp_path: Path, script: list, classifier=None):
    """Return (service, deps, llm, sandbox_store) wired to real data and a temp sandbox."""
    sandbox_dir = tmp_path / "sandbox"
    store = SandboxStore(sandbox_dir)
    llm = ScriptedChatModel(script=list(script))
    deps = AgentDeps(
        llm=llm,
        load_ledger=lambda: load_ledger(REAL, sandbox_dir),
        sandbox=store,
        term_months=12,
        default_as_of=AS_OF,
        classifier=classifier,
    )
    service = AgentService(build_graph(deps, InMemorySaver()), AS_OF)
    return service, deps, llm, store
