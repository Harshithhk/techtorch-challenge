"""App factory. Wires settings, the model, storage, observability and the graph together.

create_app() takes its collaborators as arguments, so tests can substitute fakes. main() is the
production entry point used by uvicorn: `uvicorn app.main:app`.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_core.language_models import BaseChatModel

from app.agent.graph import build_graph
from app.agent.nodes import AgentDeps
from app.agent.service import AgentService
from app.api.routes import router
from app.domain.models import DomainError
from app.guardrails.topic import make_llm_classifier
from app.observability.langfuse import build_observability, callbacks_for
from app.observability.pii import PiiScrubber
from app.settings import Settings, get_settings
from app.storage.ledger import load_ledger
from app.storage.loaders import load_base
from app.storage.postgres.checkpointer import open_checkpointer
from app.storage.postgres.users import UserRepository
from app.storage.sandbox import SandboxStore


def _default_llm(settings: Settings, model: str) -> BaseChatModel | None:
    """Claude chat model, or None when no API key is set. Input: settings, model name. Output: model or None."""
    if not settings.anthropic_api_key:
        return None
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=model, api_key=settings.anthropic_api_key)


def create_app(
    settings: Settings | None = None,
    *,
    llm: BaseChatModel | None = None,
    classifier: Callable[[str], Any] | None = None,
    users: Any = None,
    checkpointer: Callable[[], AbstractAsyncContextManager] | None = None,
) -> FastAPI:
    """Build the FastAPI app. Every collaborator can be overridden for tests.

    Input: settings (defaults to the .env-backed settings), and optional fakes for the model, the
        classifier, the user store and the checkpointer context manager.
    Output: FastAPI app. Startup (lifespan) builds the graph and the service.
    """
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        base = load_base(settings.data_dir)
        scrubber = PiiScrubber(p.customer_name for p in base.plans)
        observability = build_observability(settings, scrubber)

        repo = users if users is not None else UserRepository(settings.database_url)
        if users is None:
            await repo.ensure_schema()

        model = llm if llm is not None else _default_llm(settings, settings.agent_model)
        guard_classifier = classifier
        if guard_classifier is None and model is not None and settings.anthropic_api_key:
            guard_classifier = make_llm_classifier(_default_llm(settings, settings.guard_model))

        cp_factory = checkpointer or (lambda: open_checkpointer(settings.database_url))
        sandbox = SandboxStore(settings.sandbox_dir)
        app.state.users = repo
        app.state.sandbox = sandbox
        app.state.service = None

        async with cp_factory() as saver:
            if model is not None:
                deps = AgentDeps(
                    llm=model,
                    load_ledger=lambda: load_ledger(settings.data_dir, settings.sandbox_dir),
                    sandbox=sandbox,
                    term_months=settings.default_term_months,
                    default_as_of=settings.as_of_date or date.today(),
                    classifier=guard_classifier,
                )
                graph = build_graph(deps, saver)
                app.state.service = AgentService(
                    graph,
                    deps.default_as_of,
                    callbacks=callbacks_for(observability),
                )
            app.state.observability = observability
            yield

    app = FastAPI(title="Revenue Leakage Agent", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-User-Id"],
    )

    @app.exception_handler(DomainError)
    async def domain_error(_request: Request, exc: DomainError) -> JSONResponse:
        """Domain refusals become 400 with the message, so the UI can show them."""
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    app.include_router(router)
    return app


app = create_app()
