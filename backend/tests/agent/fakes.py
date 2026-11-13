"""Deterministic stand-ins for the chat model. No API key needed, and an unexpected model call fails the test."""
from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class ScriptedChatModel(BaseChatModel):
    """Returns the scripted AIMessages in order. `seen` records what it was shown, for assertions."""

    script: list[Any] = Field(default_factory=list)
    seen: list[Any] = Field(default_factory=list)

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        return self

    def _generate(self, messages: list[Any], stop: list[str] | None = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        self.seen.append(list(messages))
        if not self.script:
            raise AssertionError("the model was called more times than the test scripted")
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])

    @property
    def _llm_type(self) -> str:
        return "scripted"


def tool_call(name: str, args: dict[str, Any], call_id: str = "call-1") -> AIMessage:
    """An assistant message that requests one tool call."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def say(text: str) -> AIMessage:
    """A plain final assistant answer."""
    return AIMessage(content=text)
