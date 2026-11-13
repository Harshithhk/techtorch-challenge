"""Sandbox store: apply, rollback, rejection logging, conflicts, atomic writes, and the audit trail."""
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.storage.errors import SandboxConflictError
from app.storage.sandbox import SandboxStore

FIXED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    return SandboxStore(tmp_path / "sandbox", clock=lambda: FIXED)


def make_good_record(invoice_id="INV-MG-1"):
    return {
        "invoice_id": invoice_id, "plan_id": "C-1001", "customer_name": "ACME Corp",
        "issue_date": "2025-09-01", "due_date": "2025-10-01", "amount_invoiced": "8000.00",
        "currency": "USD", "status": "unpaid", "description": "Make-good: missing September",
    }


def test_nothing_exists_until_first_write(tmp_path):
    """A fresh sandbox has no records, no registry and no audit events, and creates no files on read."""
    s = SandboxStore(tmp_path / "sandbox")
    assert s.read_records("invoices") == []
    assert s.read_applied() == []
    assert s.read_audit() == []
    assert not (tmp_path / "sandbox" / "invoices.json").exists()


def test_commit_then_revert_round_trip(store):
    """Applying writes the record, registers it and logs 'applied'. Reverting removes all three effects."""
    entry = store.commit_action(action_id="ACT-1", kind="make_good_invoice", table="invoices",
                                record=make_good_record(), plan_id="C-1001", summary="make-good Sep", user_id="alice")
    assert [r["invoice_id"] for r in store.read_records("invoices")] == ["INV-MG-1"]
    assert store.read_applied()[0]["action_id"] == "ACT-1"
    assert entry["applied_at"] == FIXED.isoformat()

    store.revert_action(action_id="ACT-1", user_id="alice")
    assert store.read_records("invoices") == []
    assert store.read_applied() == []
    events = [e["event"] for e in store.read_audit()]
    assert events == ["applied", "rolled_back"]


def test_audit_is_append_only_and_records_every_event(store):
    """Each applied, rolled-back and rejected event is appended in order with its own event id."""
    store.commit_action(action_id="ACT-1", kind="make_good_invoice", table="invoices",
                        record=make_good_record(), plan_id="C-1001", summary="s", user_id="alice")
    store.log_rejection(action_id="ACT-2", kind="credit_memo", plan_id="C-1007", summary="no", user_id="alice")
    store.revert_action(action_id="ACT-1", user_id="alice")
    audit = store.read_audit()
    assert [e["event"] for e in audit] == ["applied", "rejected", "rolled_back"]
    assert len({e["event_id"] for e in audit}) == 3
    assert all(e["timestamp"] == FIXED.isoformat() for e in audit)


def test_rejection_writes_no_ledger_data(store):
    """Rejecting a proposal changes the audit log and nothing else: no records, no registry entry."""
    store.log_rejection(action_id="ACT-9", kind="make_good_invoice", plan_id="C-1001", summary="rejected", user_id="bob")
    assert store.read_records("invoices") == []
    assert store.read_applied() == []
    assert [e["event"] for e in store.read_audit()] == ["rejected"]


def test_duplicate_primary_key_is_refused(store):
    """A second record with the same invoice id is refused and nothing is written."""
    store.commit_action(action_id="ACT-1", kind="make_good_invoice", table="invoices",
                        record=make_good_record(), plan_id="C-1001", summary="s", user_id="alice")
    with pytest.raises(SandboxConflictError):
        store.commit_action(action_id="ACT-2", kind="make_good_invoice", table="invoices",
                            record=make_good_record(), plan_id="C-1001", summary="s", user_id="alice")
    assert len(store.read_records("invoices")) == 1
    assert len(store.read_applied()) == 1


def test_revert_unknown_action_raises_and_changes_nothing(store):
    """Rolling back an id that was never applied is an error, with no side effects."""
    with pytest.raises(SandboxConflictError):
        store.revert_action(action_id="ACT-404", user_id="alice")
    assert store.read_audit() == []


def test_writes_are_atomic_and_leave_no_temp_files(store):
    """After writes, only the expected files remain: no temp files are left behind."""
    store.commit_action(action_id="ACT-1", kind="make_good_invoice", table="invoices",
                        record=make_good_record(), plan_id="C-1001", summary="s", user_id="alice")
    names = sorted(p.name for p in store.directory.iterdir())
    assert names == [".lock", "applied_actions.json", "audit_log.json", "invoices.json"]
    json.loads((store.directory / "invoices.json").read_text())  # still valid JSON


def test_amounts_stored_as_strings_keep_decimal_precision(store):
    """Money is written as a string, so 8000.10 survives a round trip exactly."""
    rec = make_good_record()
    rec["amount_invoiced"] = "8000.10"
    store.commit_action(action_id="ACT-1", kind="make_good_invoice", table="invoices",
                        record=rec, plan_id="C-1001", summary="s", user_id="alice")
    assert Decimal(store.read_records("invoices")[0]["amount_invoiced"]) == Decimal("8000.10")


def test_empty_audit_file_is_read_as_no_events(store):
    """A zero-byte audit file (seen in practice) holds no events, so reads and writes must still work."""
    store.directory.mkdir(parents=True, exist_ok=True)
    (store.directory / "audit_log.json").write_text("")
    assert store.read_audit() == []
    store.log_rejection(action_id="ACT-9", kind="credit_memo", plan_id="C-1", summary="s", user_id="u")
    assert [e["event"] for e in store.read_audit()] == ["rejected"]


def test_corrupt_audit_file_is_never_overwritten(store):
    """Non-empty corrupt history must raise, not be replaced by a fresh log."""
    store.directory.mkdir(parents=True, exist_ok=True)
    (store.directory / "audit_log.json").write_text("[{\"event\": ")
    with pytest.raises(json.JSONDecodeError):
        store.log_rejection(action_id="ACT-9", kind="credit_memo", plan_id="C-1", summary="s", user_id="u")
    assert (store.directory / "audit_log.json").read_text() == "[{\"event\": "
