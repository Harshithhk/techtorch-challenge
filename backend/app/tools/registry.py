"""The tools the model can see. Read and propose only.

Write operations (apply, rollback) are deliberately absent from this registry. Their functions live
in write.py and are reachable only from the approval gate. The test suite checks that the names
'apply' and 'rollback' are never bound to the model.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    """Base for model-facing argument schemas. Unknown keys are an error, never silently dropped."""

    model_config = ConfigDict(extra="forbid")

from app.tools import propose, read
from app.tools.context import ToolContext


class _PlanArg(_Strict):
    plan_id: str | None = Field(None, description="Plan id such as C-1001. Leave empty to use the plan in focus.")


class _CustomerArg(_Strict):
    customer_name: str | None = Field(None, description="Customer name or part of it, case-insensitive.")


class _QueryArgs(_Strict):
    plan_id: str | None = None
    customer_name: str | None = None
    date_from: str | None = Field(None, description="ISO date, inclusive, on issue_date.")
    date_to: str | None = Field(None, description="ISO date, inclusive, on issue_date.")
    status: str | None = Field(None, description="'paid' or 'unpaid'.")


class _FxArgs(_Strict):
    amount: str = Field(description="Amount as a decimal string, e.g. '25000'.")
    from_currency: str = Field(description="ISO code, e.g. EUR.")
    to_currency: str = Field(description="ISO code, e.g. USD.")
    on_date: str | None = Field(None, description="ISO date for the rate. Defaults to the as-of date.")


class _ScanArgs(_Strict):
    customer_name: str = Field(description="Customer name or part of it.")


class _AsOfArgs(_Strict):
    as_of: str = Field(description="ISO date (YYYY-MM-DD) to investigate as of.")


class _MakeGoodArgs(_Strict):
    plan_id: str | None = Field(None, description="Plan id. Empty = plan in focus.")
    period_start: str | None = Field(None, description="ISO start date of the gap period, if the plan has several gaps.")
    amount: str | None = Field(None, description="Optional amount to LOWER the make-good. Never above the computed gap. Leave empty for the full gap.")


class _CreditArgs(_Strict):
    invoice_id: str = Field(description="The overbilled invoice id, such as I-9123.")
    amount: str | None = Field(None, description="Optional amount to LOWER the credit. Never above the computed excess.")


class _AmendArgs(_Strict):
    plan_id: str | None = Field(None, description="Plan to amend. Empty = plan in focus.")
    effective_date: str = Field(description="ISO date the change takes effect. Must be after the plan's latest version starts.")
    new_total: str | None = Field(None, description="New total contract value, positive decimal string.")
    new_cadence: str | None = Field(None, description="'Monthly', 'Quarterly' or 'Annual'.")
    add_entitlements: list[str] | None = Field(None, description="Entitlements to add.")


class _RollbackArgs(_Strict):
    action_id: str | None = Field(None, description="Applied action id to undo. Empty = the most recent applied action.")


@dataclass(frozen=True)
class ToolSpec:
    """One model-visible tool: name, description, argument schema, and the function that runs it."""

    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[..., dict[str, Any]]


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("load_plan", "Read one plan with its amendment family, active windows and expected periods. Puts the plan in focus.", _PlanArg, read.load_plan),
    ToolSpec("list_plans", "List plans, optionally for one customer.", _CustomerArg, read.list_plans),
    ToolSpec("query_invoices", "Find invoices by plan, customer, issue-date range or status.", _QueryArgs, read.query_invoices),
    ToolSpec("fx_convert", "Convert an amount between currencies using the domain FX rule.", _FxArgs, read.fx_convert),
    ToolSpec("investigate_plan", "Run the live investigation for one plan. Returns findings with calculation trails. Puts the plan in focus.", _PlanArg, read.investigate_plan),
    ToolSpec("scan_customer", "Investigate all plans of a customer, including orphan invoices that point at them.", _ScanArgs, read.scan_customer),
    ToolSpec("set_as_of_date", "Change the investigation as-of date for this conversation.", _AsOfArgs, read.set_as_of_date),
    ToolSpec("propose_make_good_invoice", "Propose a make-good invoice for a missing or underbilled period. Writes nothing until the user approves.", _MakeGoodArgs, propose.propose_make_good_invoice),
    ToolSpec("propose_credit_memo", "Propose a credit memo for an overbilled invoice. Writes nothing until the user approves.", _CreditArgs, propose.propose_credit_memo),
    ToolSpec("propose_plan_amendment", "Propose a plan amendment (total, cadence or entitlements) effective from a date. Writes nothing until the user approves.", _AmendArgs, propose.propose_plan_amendment),
    ToolSpec("propose_rollback", "Propose undoing an applied action. Writes nothing until the user approves.", _RollbackArgs, propose.propose_rollback),
)

LLM_TOOL_NAMES: frozenset[str] = frozenset(spec.name for spec in SPECS)
FORBIDDEN_FOR_LLM: frozenset[str] = frozenset({"apply", "rollback"})


def run_tool(spec_name: str, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Run a model-visible tool by name with validated arguments.

    Input: tool name, context, raw args from the model (validated by the args model).
    Output: the tool's result dict (may contain __state__ / __pending__ for the graph to apply).
    Raises KeyError for names outside the registry, so write tools can never be reached this way.
    Raises pydantic.ValidationError for unknown or malformed arguments.
    """
    spec = next((s for s in SPECS if s.name == spec_name), None)
    if spec is None:
        raise KeyError(f"no model-visible tool named {spec_name!r}")
    parsed = spec.args_model.model_validate(args)
    return spec.handler(ctx, **parsed.model_dump(exclude_none=False))


def build_llm_tools() -> list[StructuredTool]:
    """LangChain tool schemas to bind to the chat model. Names and descriptions are what the model reads.

    The functions here are placeholders for schema generation only. Execution goes through run_tool.
    """
    tools = []
    for spec in SPECS:
        tools.append(StructuredTool.from_function(
            func=lambda **_kwargs: None,
            name=spec.name,
            description=spec.description,
            args_schema=spec.args_model,
        ))
    return tools
