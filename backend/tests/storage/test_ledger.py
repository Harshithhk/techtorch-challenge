"""Ledger overlay: applied sandbox records change the snapshot, and the source data is never written."""
import hashlib
from datetime import date
from pathlib import Path

import pytest

from app.domain.detectors import FindingKind, investigate
from app.settings import get_settings
from app.storage.errors import DataLoadError
from app.storage.ledger import load_ledger
from app.storage.sandbox import SandboxStore
from tests.storage.test_sandbox import make_good_record

REAL = get_settings().data_dir
pytestmark = pytest.mark.skipif(not (REAL / "invoices.json").exists(), reason="real data not present")


def _digest(directory: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.glob("*.json"))}


def test_applying_make_good_removes_the_gap_in_the_investigation(tmp_path):
    """ACME's missing September is a finding. After the sandbox make-good is applied, it is gone."""
    sandbox = tmp_path / "sandbox"
    before = investigate(load_ledger(REAL, sandbox), as_of=date(2025, 12, 31), term_months=12)
    assert any(f.kind is FindingKind.MISSING_PERIOD and f.plan_id == "C-1001"
               and f.period_start == date(2025, 9, 1) for f in before)

    record = make_good_record()  # issue_date 2025-09-01 falls inside the September window
    SandboxStore(sandbox).commit_action(action_id="ACT-1", kind="make_good_invoice", table="invoices",
                                        record=record, plan_id="C-1001", summary="make-good", user_id="alice")
    after = investigate(load_ledger(REAL, sandbox), as_of=date(2025, 12, 31), term_months=12)
    assert not any(f.kind is FindingKind.MISSING_PERIOD and f.period_start == date(2025, 9, 1) for f in after)


def test_rollback_restores_the_gap(tmp_path):
    """Rollback brings the September finding back, so approval is fully reversible."""
    sandbox = tmp_path / "sandbox"
    store = SandboxStore(sandbox)
    store.commit_action(action_id="ACT-1", kind="make_good_invoice", table="invoices",
                        record=make_good_record(), plan_id="C-1001", summary="s", user_id="alice")
    store.revert_action(action_id="ACT-1", user_id="alice")
    again = investigate(load_ledger(REAL, sandbox), as_of=date(2025, 12, 31), term_months=12)
    assert any(f.kind is FindingKind.MISSING_PERIOD and f.period_start == date(2025, 9, 1) for f in again)


def test_source_data_is_byte_identical_after_apply_and_rollback(tmp_path):
    """The writable sandbox is the only thing that changes. The source directory stays identical."""
    before = _digest(REAL)
    sandbox = tmp_path / "sandbox"
    store = SandboxStore(sandbox)
    store.commit_action(action_id="ACT-1", kind="make_good_invoice", table="invoices",
                        record=make_good_record(), plan_id="C-1001", summary="s", user_id="alice")
    load_ledger(REAL, sandbox)
    store.revert_action(action_id="ACT-1", user_id="alice")
    assert _digest(REAL) == before


def test_corrupt_sandbox_record_fails_loudly(tmp_path):
    """A sandbox row that does not match the model stops the load with a clear message."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "invoices.json").write_text('[{"invoice_id": "X"}]')
    with pytest.raises(DataLoadError, match="sandbox invoices\\[0\\] is invalid"):
        load_ledger(REAL, sandbox)
