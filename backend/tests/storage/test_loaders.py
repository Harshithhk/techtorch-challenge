"""Loaders: real files validate, bad files fail loudly with file and row, source is never written."""
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.settings import get_settings
from app.storage.errors import DataLoadError
from app.storage.loaders import load_base, read_rows
from app.domain.models import Invoice

REAL = get_settings().data_dir


def _digest(directory: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.glob("*.json"))}


@pytest.mark.skipif(not (REAL / "invoices.json").exists(), reason="real data not present")
def test_real_files_load_and_source_is_untouched():
    """All four real files validate, and loading does not change a single byte of them."""
    before = _digest(REAL)
    ledger = load_base(REAL)
    assert (len(ledger.plans), len(ledger.invoices), len(ledger.credit_memos), len(ledger.fx_rates)) == (4, 13, 1, 1)
    assert _digest(REAL) == before


@pytest.mark.skipif(not (REAL / "invoices.json").exists(), reason="real data not present")
def test_amounts_are_exact_decimals_not_floats():
    """The real invoices parse to Decimal, never float."""
    ledger = load_base(REAL)
    assert all(isinstance(inv.amount_invoiced, Decimal) for inv in ledger.invoices)


def test_float_literals_in_json_stay_exact(tmp_path):
    """A float written in JSON (8000.10) is read as Decimal('8000.10'), not as a binary float."""
    f = tmp_path / "invoices.json"
    f.write_text(
        '[{"invoice_id": "I-1", "plan_id": "", "customer_name": "x", "issue_date": "2025-01-01",'
        ' "due_date": "2025-01-10", "amount_invoiced": 8000.10, "currency": "USD", "status": "paid"}]'
    )
    rows = read_rows(f, Invoice)
    assert rows[0].amount_invoiced == Decimal("8000.10")


def test_missing_file_names_the_file(tmp_path):
    """A missing file is reported by name."""
    with pytest.raises(DataLoadError, match="missing data file"):
        read_rows(tmp_path / "nope.json", Invoice)


def test_invalid_json_reports_line(tmp_path):
    """Syntax errors report the line number."""
    f = tmp_path / "bad.json"
    f.write_text("[\n{\"invoice_id\": \n")
    with pytest.raises(DataLoadError, match="invalid JSON at line"):
        read_rows(f, Invoice)


def test_top_level_must_be_array(tmp_path):
    """An object at the top level is rejected with a clear message."""
    f = tmp_path / "obj.json"
    f.write_text('{"a": 1}')
    with pytest.raises(DataLoadError, match="top level must be a JSON array"):
        read_rows(f, Invoice)


def test_unknown_key_is_rejected_with_row_and_field(tmp_path):
    """Schema drift (an unexpected key) fails loudly and names the row."""
    f = tmp_path / "invoices.json"
    f.write_text(json.dumps([{
        "invoice_id": "I-1", "plan_id": "", "customer_name": "x", "issue_date": "2025-01-01",
        "due_date": "2025-01-10", "amount_invoiced": 1, "currency": "USD", "status": "paid",
        "surprise": True,
    }]))
    with pytest.raises(DataLoadError, match=r"invoices\.json\[0\]"):
        read_rows(f, Invoice)
