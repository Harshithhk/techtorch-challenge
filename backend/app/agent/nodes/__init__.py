"""Graph nodes. Each one is small and does one thing. Dependencies arrive through AgentDeps.

Order of effects matters: the approval gate calls interrupt() before any write, so a paused graph
has written nothing. On resume, LangGraph re-enters the node from the top, which is why nothing
before interrupt() has side effects.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import interrupt
from pydantic import ValidationError

from app.agent.prompts import FALLBACK_UNGROUNDED, REFUSALS, SYSTEM_PROMPT
from app.agent.state import AgentState
from app.agent.text import message_text
from app.domain.models import DomainError, Ledger
from app.guardrails.grounding import check_grounding
from app.guardrails.injection import detect_injection
from app.guardrails.topic import Classifier
from app.storage.sandbox import SandboxStore
from app.tools.context import ToolContext
from app.tools.registry import run_tool
from app.tools.write import apply_pending, reject_pending

MAX_MODEL_STEPS = 8
MAX_REGENERATIONS = 1


@dataclass
class AgentDeps:
    """Everything the graph needs from the outside world. Tests pass fakes; the app passes real ones."""

    llm: BaseChatModel
    load_ledger: Callable[[], Ledger]
    sandbox: SandboxStore
    term_months: int
    default_as_of: date
    classifier: Classifier | None = None


def _kind(message: AnyMessage) -> str | None:
    return (message.additional_kwargs or {}).get("kind")


def _text(message: AnyMessage) -> str:
    """Text of a message, ignoring thinking and other non-text blocks."""
    return message_text(message.content)


def current_turn(messages: list[AnyMessage]) -> list[AnyMessage]:
    """Messages after the last real user message. This is what the grounding check looks at."""
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage) and _kind(messages[index]) == "user":
            return messages[index + 1:]
    return messages


def _previous_assistant_text(messages: list[AnyMessage], limit: int = 600) -> str:
    """Text of the assistant message just before the current user message, truncated.

    A short user reply such as "yes" only makes sense in the context of this message, so the
    guard needs it. Tool traffic is skipped.
    """
    for message in reversed(messages[:-1]):
        if isinstance(message, AIMessage) and not getattr(message, "tool_calls", None):
            text = _text(message)
            if text:
                return text[-limit:]
    return ""


def _ctx(state: AgentState, deps: AgentDeps, pending: dict | None) -> ToolContext:
    return ToolContext(
        user_id=state["user_id"],
        as_of=date.fromisoformat(state["as_of"]),
        term_months=deps.term_months,
        load_ledger=deps.load_ledger,
        sandbox=deps.sandbox,
        active_plan_id=state.get("active_plan_id"),
        pending=pending,
    )


def make_nodes(deps: AgentDeps, bound_llm: Any) -> dict[str, Callable[[AgentState], dict]]:
    """Build the node functions bound to these dependencies. Input: deps, the LLM with tools bound."""

    def guard(state: AgentState) -> dict:
        """Refuse injections, off-topic turns and turns the classifier cannot check, before any tool or model call.

        Fails closed: if the classifier errors, the turn is refused rather than allowed through.
        """
        text = _text(state["messages"][-1])
        reason: str | None = None
        if detect_injection(text):
            reason = "injection"
        elif deps.classifier is not None:
            try:
                verdict = deps.classifier(text, _previous_assistant_text(state["messages"]))
            except Exception:  # fail closed: an unchecked message is never sent to the tools
                reason = "unavailable"
            else:
                if verdict.injection:
                    reason = "injection"
                elif not verdict.on_topic:
                    reason = "off_topic"
        if reason:
            return {"guard_blocked": True, "steps": 0, "retries": 0,
                    "messages": [AIMessage(content=REFUSALS[reason])]}
        return {"guard_blocked": False, "steps": 0, "retries": 0}

    def agent(state: AgentState) -> dict:
        """Ask the model for the next step: a tool call or a final answer. Stops after MAX_MODEL_STEPS."""
        steps = state.get("steps", 0) + 1
        if steps > MAX_MODEL_STEPS:
            return {"steps": steps, "messages": [AIMessage(content="I stopped after too many steps. Please rephrase the question.")]}
        system = SystemMessage(content=SYSTEM_PROMPT.format(
            as_of=state.get("as_of"), plan=state.get("active_plan_id") or "none"))
        response = bound_llm.invoke([system, *state["messages"]])
        return {"steps": steps, "messages": [response]}

    def tools(state: AgentState) -> dict:
        """Run each requested tool through the registry. Read and propose only; writes are impossible here.

        Tools may return __state__ (focus, as-of) and __pending__ (a proposal). The first proposal
        becomes pending. A second one in the same batch is refused, because the context is updated as we go.
        """
        pending = state.get("pending")
        updates: dict[str, Any] = {}
        messages: list[ToolMessage] = []
        for call in state["messages"][-1].tool_calls:
            ctx = _ctx({**state, **updates}, deps, pending)
            try:
                result = run_tool(call["name"], ctx, call.get("args") or {})
            except KeyError:
                result = {"error": f"unknown tool {call['name']}"}
            except ValidationError as exc:
                result = {"error": f"invalid arguments: {exc.errors()[0]['msg']}"}
            focus = result.pop("__state__", None)
            proposal = result.pop("__pending__", None)
            if focus:
                updates.update(focus)
            if proposal is not None:
                if pending is None:
                    pending = proposal
                    updates["pending"] = proposal
                else:
                    result = {"error": "a proposal is already waiting; it was not replaced"}
            messages.append(ToolMessage(content=json.dumps(result, default=str), tool_call_id=call["id"], name=call["name"]))
        return {"messages": messages, **updates}

    def approval_gate(state: AgentState) -> dict:
        """Pause for a human decision. Approve re-derives and writes once. Reject writes nothing but the audit.

        The first call is interrupt(), so a paused graph has written nothing. Resume with
        Command(resume={"decision": "approve" | "reject"}).
        """
        pending = state["pending"]
        answer = interrupt({"kind": "approval_required", "proposal": pending})
        decision = answer.get("decision") if isinstance(answer, dict) else answer
        ctx = _ctx(state, deps, None)
        # Always clear the pending proposal, even if recording the decision fails. Otherwise the thread
        # stays stuck on "waiting for approval" with no way out.
        try:
            if decision == "approve":
                result = apply_pending(ctx, pending)
            else:
                result = reject_pending(ctx, pending)
        except DomainError as exc:
            result = {"status": "refused", "reason": str(exc)}
        except Exception as exc:  # storage or I/O failure: report it, do not hang the conversation
            result = {"status": "error", "reason": f"could not record the decision ({exc.__class__.__name__})"}
        note = HumanMessage(content=f"[approval gate] {json.dumps(result, default=str)}", additional_kwargs={"kind": "gate"})
        return {"pending": None, "messages": [note]}

    def grounding(state: AgentState) -> dict:
        """Check that every figure in the draft answer came from a tool result or the user's message.

        One regeneration is allowed. If the figures are still unsupported, the draft is replaced with a safe fallback.
        """
        last = state["messages"][-1]
        # Evidence is the whole conversation, not just this turn: a follow-up such as "what was the
        # shortfall again?" may restate a figure a tool returned earlier in the same thread.
        history = state["messages"]
        tool_texts = [_text(m) for m in history if isinstance(m, ToolMessage) or _kind(m) == "gate"]
        user_texts = [_text(m) for m in history if isinstance(m, HumanMessage) and _kind(m) == "user"]
        unsupported = check_grounding(_text(last), tool_texts, user_texts)
        if not unsupported:
            return {}
        if state.get("retries", 0) < MAX_REGENERATIONS:
            note = HumanMessage(
                content=(f"[grounding] Your draft used figures no tool returned: {', '.join(unsupported)}. "
                         "Rewrite the answer using only figures from tool results. Do not compute new figures."),
                additional_kwargs={"kind": "system_note"},
            )
            return {"messages": [note], "retries": state.get("retries", 0) + 1}
        return {"messages": [AIMessage(content=FALLBACK_UNGROUNDED, id=last.id)]}

    return {"guard": guard, "agent": agent, "tools": tools, "approval_gate": approval_gate, "grounding": grounding}
