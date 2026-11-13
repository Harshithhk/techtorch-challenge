"""Langfuse wiring. Tracing is off unless both keys are set, so offline dev and tests never send data.

When enabled, the client is built with the PII mask, so nothing reaches Langfuse unscrubbed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.observability.pii import PiiScrubber, make_mask
from app.settings import Settings


@dataclass
class Observability:
    """The Langfuse client and the LangChain callback handler that uses it."""

    client: Any
    handler: Any


def build_observability(settings: Settings, scrubber: PiiScrubber) -> Observability | None:
    """Return the tracing setup, or None when Langfuse keys are not configured.

    Input: settings (keys, host), scrubber (for the mask). Output: Observability or None.
    """
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler

    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
        mask=make_mask(scrubber),
    )
    return Observability(client=client, handler=CallbackHandler())


def callbacks_for(observability: Observability | None) -> list[Any]:
    """Callbacks to pass to the graph run. Empty when tracing is off."""
    return [observability.handler] if observability else []
