"""Graph wiring. Routing functions are small and tested on their own. Nodes live in nodes.py."""
from __future__ import annotations

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.agent.nodes import AgentDeps, MAX_MODEL_STEPS, make_nodes
from app.agent.state import AgentState
from app.tools.registry import build_llm_tools


def route_after_guard(state: AgentState) -> str:
    """Blocked turns end immediately. Allowed turns go to the model."""
    return "end" if state.get("guard_blocked") else "agent"


def route_after_agent(state: AgentState) -> str:
    """Tool calls go to the tool node (within the step budget). Otherwise check grounding."""
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None) and state.get("steps", 0) <= MAX_MODEL_STEPS:
        return "tools"
    return "grounding"


def route_after_tools(state: AgentState) -> str:
    """A proposal waiting means the human must decide, so the approval gate runs next."""
    return "gate" if state.get("pending") else "agent"


def route_after_grounding(state: AgentState) -> str:
    """A regeneration note sends the model back to answer again. Anything else ends the turn."""
    last = state["messages"][-1]
    if isinstance(last, HumanMessage) and (last.additional_kwargs or {}).get("kind") == "system_note":
        return "agent"
    return "end"


def build_graph(deps: AgentDeps, checkpointer: BaseCheckpointSaver):
    """Compile the agent graph. Input: dependencies and a checkpointer (memory in tests, Postgres in the app).

    Flow: guard -> agent <-> tools -> (approval_gate interrupt) -> agent -> grounding -> END.
    """
    bound = deps.llm.bind_tools(build_llm_tools())
    nodes = make_nodes(deps, bound)

    graph = StateGraph(AgentState)
    for name, fn in nodes.items():
        graph.add_node(name, fn)
    graph.add_node("gate", nodes["approval_gate"])
    graph.add_edge(START, "guard")
    graph.add_conditional_edges("guard", route_after_guard, {"agent": "agent", "end": END})
    graph.add_conditional_edges("agent", route_after_agent, {"tools": "tools", "grounding": "grounding"})
    graph.add_conditional_edges("tools", route_after_tools, {"gate": "gate", "agent": "agent"})
    graph.add_edge("gate", "agent")
    graph.add_conditional_edges("grounding", route_after_grounding, {"agent": "agent", "end": END})
    return graph.compile(checkpointer=checkpointer)
