"""Tools: reads return domain data, proposals write nothing, apply re-derives first, the registry hides writes."""
import hashlib
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.models import Ledger
from app.domain.proposals import ProposalError
from app.settings import get_settings
from app.storage.ledger import load_ledger
from app.storage.sandbox import SandboxStore
from app.tools import read, propose, write
from app.tools.context import ToolContext
from app.tools.registry import FORBIDDEN_FOR_LLM, LLM_TOOL_NAMES, run_tool
from tests.domain.factories import D, invoice, ledger, plan, rate

REAL = get_settings().data_dir
needs_real = pytest.mark.skipif(not (REAL / "invoices.json").exists(), reason="real data not present")


def _digest(directory: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.glob("*.json"))}


def real_ctx(tmp_path, active=None) -> ToolContext:
    """Context over the real data plus a fresh temporary sandbox. The source data is never written."""
    sandbox = tmp_path / "sandbox"
    return ToolContext(user_id="alice", as_of=date(2025, 12, 31), term_months=12,
                       load_ledger=lambda: load_ledger(REAL, sandbox), sandbox=SandboxStore(sandbox),
                       active_plan_id=active)


def inline_ctx(tmp_path, snapshot: Ledger, active=None) -> ToolContext:
    sandbox = tmp_path / "sandbox"
    return ToolContext(user_id="alice", as_of=date(2025, 7, 31), term_months=12,
                       load_ledger=lambda: snapshot, sandbox=SandboxStore(sandbox), active_plan_id=active)


@needs_real
def test_load_plan_returns_family_periods_and_sets_focus(tmp_path):
    """Loading C-1007 shows its amendment family and expected periods, and puts it in focus."""
    result = read.load_plan(real_ctx(tmp_path), plan_id="C-1007")
    assert result["family"] == ["C-1007", "C-1007-A1"]
    assert result["plan"]["currency"] == "USD"
    assert result["__state__"] == {"active_plan_id": "C-1007"}
    assert {p["plan_id"] for p in result["expected_periods"]} == {"C-1007", "C-1007-A1"}


@needs_real
def test_follow_up_without_plan_id_uses_plan_in_focus(tmp_path):
    """'What currency is that plan in?' resolves against the plan in focus (C-1010, USD)."""
    result = read.load_plan(real_ctx(tmp_path, active="C-1010"))
    assert result["plan"]["plan_id"] == "C-1010"
    assert result["plan"]["currency"] == "USD"


def test_no_plan_and_no_focus_asks_the_user(tmp_path):
    """With nothing in focus, the tool returns an error that tells the model to ask."""
    result = read.load_plan(inline_ctx(tmp_path, ledger([plan()])))
    assert "error" in result and "which plan" in result["error"]


def test_unknown_plan_is_an_error_result_not_an_exception(tmp_path):
    """Errors come back as data so the model can recover."""
    result = read.investigate_plan(inline_ctx(tmp_path, ledger([plan()])), plan_id="NOPE")
    assert "not found" in result["error"]


@needs_real
def test_investigate_plan_uses_domain_numbers_as_strings(tmp_path):
    """ACME's September gap is returned as the domain amount, '8000.00 USD', never a float."""
    result = read.investigate_plan(real_ctx(tmp_path), plan_id="C-1001")
    gaps = [f for f in result["findings"] if f["kind"] == "missing_period" and f["period_start"] == "2025-09-01"]
    assert gaps and gaps[0]["expected"] == {"amount": "8000.00", "currency": "USD"}


@needs_real
def test_query_invoices_filters(tmp_path):
    """Status filter finds the single unpaid ACME invoice."""
    result = read.query_invoices(real_ctx(tmp_path), status="unpaid")
    assert [i["invoice_id"] for i in result["invoices"]] == ["I-9010"]


@needs_real
def test_fx_convert_matches_pdf_example(tmp_path):
    """EUR 25 000 on 2025-09-12 is USD 27 000.00 at the stored rate, with evidence text."""
    result = read.fx_convert(real_ctx(tmp_path), amount="25000", from_currency="EUR", to_currency="USD", on_date="2025-09-12")
    assert result["converted"] == {"amount": "27000.00", "currency": "USD"}
    assert result["method"] == "direct"


def test_fx_convert_rejects_non_numeric_amount(tmp_path):
    """A non-numeric amount from the model is an error result, not a crash."""
    result = read.fx_convert(inline_ctx(tmp_path, ledger(rates=[rate()])), amount="lots", from_currency="EUR", to_currency="USD")
    assert "must be a number" in result["error"]


@needs_real
def test_scan_customer_includes_orphan_for_globex(tmp_path):
    """Scanning Globex pulls in its plans, and the orphan renewal I-9202 that points at them."""
    result = read.scan_customer(real_ctx(tmp_path), customer_name="globex")
    kinds = {(f["kind"], tuple(f["invoice_ids"])) for f in result["findings"]}
    assert ("orphan_invoice", ("I-9202",)) in kinds


@needs_real
def test_propose_make_good_writes_nothing_and_drafts_domain_gap(tmp_path):
    """The proposal is 8 000 for September and changes neither the source data nor the sandbox."""
    ctx = real_ctx(tmp_path)
    before = _digest(REAL)
    result = propose.propose_make_good_invoice(ctx, plan_id="C-1001", period_start="2025-09-01")
    assert result["__pending__"]["amount"] == {"amount": "8000.00", "currency": "USD"}
    assert result["__pending__"]["action_kind"] == "make_good_invoice"
    assert _digest(REAL) == before
    assert not (tmp_path / "sandbox").exists()


@needs_real
def test_llm_cannot_inflate_make_good_but_may_lower_it(tmp_path):
    """A requested 9 000 is refused (gap is 8 000). A requested 5 000 is accepted and labelled."""
    ctx = real_ctx(tmp_path)
    too_big = propose.propose_make_good_invoice(ctx, plan_id="C-1001", period_start="2025-09-01", amount="9000")
    assert "exceeds" in too_big["error"]
    lowered = propose.propose_make_good_invoice(ctx, plan_id="C-1001", period_start="2025-09-01", amount="5000")
    assert lowered["__pending__"]["amount"]["amount"] == "5000.00"
    assert lowered["__pending__"]["warnings"]


@needs_real
def test_second_proposal_refused_while_one_is_pending(tmp_path):
    """Only one proposal may wait for approval at a time."""
    ctx = real_ctx(tmp_path)
    first = propose.propose_make_good_invoice(ctx, plan_id="C-1001", period_start="2025-09-01")
    ctx.pending = first["__pending__"]
    second = propose.propose_make_good_invoice(ctx, plan_id="C-1010")
    assert "already waiting for approval" in second["error"]


def test_credit_memo_proposal_uses_domain_excess(tmp_path):
    """EUR 25 000 at 1.08 against a USD 25 000 period: the credit memo is 2 000 USD."""
    p = plan(plan_id="A1", total_value=D(100000), cadence="Quarterly", start_date=date(2025, 7, 1))
    inv = invoice(invoice_id="I-EUR", plan_id="A1", issue_date=date(2025, 7, 10), amount_invoiced=D(25000),
                  currency="EUR", description="Quarterly Analytics")
    ctx = inline_ctx(tmp_path, ledger([p], [inv], rates=[rate(date=date(2025, 7, 10))]))
    result = propose.propose_credit_memo(ctx, invoice_id="I-EUR")
    assert result["__pending__"]["amount"] == {"amount": "2000.00", "currency": "USD"}
    assert result["__pending__"]["target_invoice_id"] == "I-EUR"


def test_amendment_effective_date_must_follow_latest_version(tmp_path):
    """An amendment cannot start on or before the plan it amends."""
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    result = propose.propose_plan_amendment(inline_ctx(tmp_path, ledger([p])), plan_id="P-1",
                                            effective_date="2025-01-01", new_total="100000")
    assert "must be after" in result["error"]


def test_registry_exposes_no_write_tools(tmp_path):
    """The model's toolset has no apply or rollback, and running them by name is impossible."""
    assert not (LLM_TOOL_NAMES & FORBIDDEN_FOR_LLM)
    with pytest.raises(KeyError):
        run_tool("apply", inline_ctx(tmp_path, ledger([plan()])), {})


def test_run_tool_validates_arguments(tmp_path):
    """Unknown argument names fail validation rather than being silently passed through."""
    with pytest.raises(ValidationError):
        run_tool("list_plans", inline_ctx(tmp_path, ledger([plan()])), {"surprise": 1})


@needs_real
def test_apply_make_good_writes_one_record_and_one_audit_event(tmp_path):
    """Approval applies exactly one invoice to the sandbox, logs it, and leaves the source files alone."""
    ctx = real_ctx(tmp_path)
    before = _digest(REAL)
    pending = propose.propose_make_good_invoice(ctx, plan_id="C-1001", period_start="2025-09-01")["__pending__"]
    result = write.apply_pending(ctx, pending)
    assert result["status"] == "applied" and result["action_kind"] == "make_good_invoice"
    records = ctx.sandbox.read_records("invoices")
    assert len(records) == 1 and records[0]["amount_invoiced"] == "8000.00" and records[0]["issue_date"] == "2025-09-01"
    assert [e["event"] for e in ctx.sandbox.read_audit()] == ["applied"]
    assert _digest(REAL) == before


@needs_real
def test_apply_refuses_when_ledger_changed_since_draft(tmp_path):
    """If the gap closed after drafting (say, another make-good landed), apply refuses and writes nothing."""
    ctx = real_ctx(tmp_path)
    pending = propose.propose_make_good_invoice(ctx, plan_id="C-1001", period_start="2025-09-01")["__pending__"]
    ctx.sandbox.commit_action(action_id="ACT-OTHER", kind="make_good_invoice", table="invoices",
                              record={"invoice_id": "INV-OTHER", "plan_id": "C-1001", "customer_name": "ACME Corp",
                                      "issue_date": "2025-09-03", "due_date": "2025-10-03", "amount_invoiced": "8000.00",
                                      "currency": "USD", "status": "unpaid", "description": "other"},
                              plan_id="C-1001", summary="other", user_id="bob")
    with pytest.raises(ProposalError, match="ledger has changed"):
        write.apply_pending(ctx, pending)
    assert len(ctx.sandbox.read_records("invoices")) == 1


@needs_real
def test_rollback_proposal_then_apply_removes_the_record(tmp_path):
    """Rollback is itself proposed, approved, and then removes the applied record and logs it."""
    ctx = real_ctx(tmp_path)
    applied = write.apply_pending(ctx, propose.propose_make_good_invoice(
        ctx, plan_id="C-1001", period_start="2025-09-01")["__pending__"])
    rb = propose.propose_rollback(ctx)["__pending__"]
    assert rb["action_kind"] == "rollback" and rb["target_action_id"] == applied["action_id"]
    write.apply_pending(ctx, rb)
    assert ctx.sandbox.read_records("invoices") == []
    assert [e["event"] for e in ctx.sandbox.read_audit()] == ["applied", "rolled_back"]


@needs_real
def test_reject_writes_no_ledger_data_only_audit(tmp_path):
    """A rejected proposal leaves records and registry empty and adds a single 'rejected' event."""
    ctx = real_ctx(tmp_path)
    pending = propose.propose_make_good_invoice(ctx, plan_id="C-1001", period_start="2025-09-01")["__pending__"]
    write.reject_pending(ctx, pending)
    assert ctx.sandbox.read_records("invoices") == []
    assert ctx.sandbox.read_applied() == []
    assert [e["event"] for e in ctx.sandbox.read_audit()] == ["rejected"]


@needs_real
def test_apply_amendment_adds_a_superseding_plan_version(tmp_path):
    """Approved amendment of C-1001 creates C-1001-A1 that amends it, starting on the effective date."""
    ctx = real_ctx(tmp_path)
    pending = propose.propose_plan_amendment(ctx, plan_id="C-1001", effective_date="2025-07-01",
                                             new_total="100000")["__pending__"]
    write.apply_pending(ctx, pending)
    stored = ctx.sandbox.read_records("plans")
    assert len(stored) == 1
    assert stored[0]["plan_id"] == "C-1001-A1"
    assert stored[0]["amends"] == "C-1001"
    assert stored[0]["start_date"] == "2025-07-01"
    assert ctx.load_ledger().plans  # the overlay loads
