"""Conversation service: the one place the API talks to the graph. Keeps routes free of graph details.

It also enforces the rule that a waiting proposal blocks new questions: a message sent while
a decision is pending is refused without calling the model.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from app.agent.prompts import PENDING_WAITING
from app.agent.text import message_text


@dataclass(frozen=True)
class TurnResult:
    """What one step of the conversation produced.

    status: answered | awaiting_approval | blocked | busy | decided | no_pending
    reply: text to show in the chat. pending: the proposal awaiting a decision, if any.
    """

    status: str
    reply: str
    pending: dict | None = None


def _draft_text(pending: dict[str, Any]) -> str:
    """Deterministic one-liner for a new proposal. The model's explanation follows after approval."""
    return f"I've drafted a proposal: {pending['summary_title']}. Review the amount and evidence, then approve or reject it."


def _last_assistant_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls and message_text(message.content):
            return message_text(message.content)
    return ""


def _pending_from(snapshot: Any) -> dict[str, Any] | None:
    """The proposal stored in a paused graph's interrupt, if there is one."""
    for task in getattr(snapshot, "tasks", ()) or ():
        for interrupt in getattr(task, "interrupts", ()) or ():
            value = getattr(interrupt, "value", None)
            if isinstance(value, dict) and "proposal" in value:
                return value["proposal"]
    return None


class AgentService:
    """Send messages, decide proposals, and read history. Input: compiled graph, default as-of date, optional callbacks."""

    def __init__(self, graph: Any, default_as_of: date, callbacks: list[Any] | None = None) -> None:
        self._graph = graph
        self._default_as_of = default_as_of
        self._callbacks = list(callbacks or [])

    def _config(self, thread_id: str) -> dict[str, Any]:
        """Run config: which thread, plus any tracing callbacks (empty when Langfuse is off)."""
        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        if self._callbacks:
            config["callbacks"] = self._callbacks
        return config

    async def send(self, thread_id: str, user_id: str, text: str) -> TurnResult:
        """Process one user message in a thread. Refused while a proposal is waiting."""
        config = self._config(thread_id)
        snapshot = await self._graph.aget_state(config)
        if snapshot.next:
            return TurnResult("busy", PENDING_WAITING, _pending_from(snapshot))
        values = snapshot.values or {}
        payload: dict[str, Any] = {
            "messages": [HumanMessage(content=text, additional_kwargs={"kind": "user"})],
            "user_id": user_id,
        }
        if "as_of" not in values:
            payload["as_of"] = self._default_as_of.isoformat()
        result = await self._graph.ainvoke(payload, config)
        return self._to_result(result)

    async def decide(self, thread_id: str, decision: str) -> TurnResult:
        """Approve or reject the waiting proposal. Anything other than 'approve' is treated as a rejection."""
        config = self._config(thread_id)
        snapshot = await self._graph.aget_state(config)
        if not snapshot.next:
            return TurnResult("no_pending", "There is no proposal waiting for approval.")
        normalised = "approve" if decision == "approve" else "reject"
        result = await self._graph.ainvoke(Command(resume={"decision": normalised}), config)
        return self._to_result(result)

    async def pending(self, thread_id: str) -> dict[str, Any] | None:
        """The proposal waiting for a decision in this thread, or None."""
        return _pending_from(await self._graph.aget_state(self._config(thread_id)))

    async def history(self, thread_id: str) -> list[dict[str, str]]:
        """Visible chat history: user messages and assistant answers. Tool traffic and gate notes are hidden."""
        values = (await self._graph.aget_state(self._config(thread_id))).values or {}
        out: list[dict[str, str]] = []
        for message in values.get("messages", []):
            if isinstance(message, HumanMessage) and (message.additional_kwargs or {}).get("kind") == "user":
                out.append({"role": "user", "content": message_text(message.content)})
            elif isinstance(message, AIMessage) and not message.tool_calls and message_text(message.content):
                out.append({"role": "assistant", "content": message_text(message.content)})
        return out

    def _to_result(self, result: dict[str, Any]) -> TurnResult:
        if result.get("__interrupt__"):
            pending = result["__interrupt__"][0].value["proposal"]
            return TurnResult("awaiting_approval", _draft_text(pending), pending)
        status = "blocked" if result.get("guard_blocked") else "answered"
        return TurnResult(status, _last_assistant_text(result.get("messages", [])))
