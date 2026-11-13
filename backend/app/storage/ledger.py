"""Build the Ledger the domain reasons over: source files plus the sandbox overlay.

Sandbox rows are validated with the same models as source rows, so a corrupt sandbox file fails
loudly. The source files are never written.
"""
from __future__ import annotations

from pathlib import Path

from app.domain.models import CreditMemo, Invoice, Ledger, Plan
from app.storage.loaders import load_base
from app.storage.sandbox import SandboxStore
from app.storage.errors import DataLoadError
from pydantic import ValidationError


def _overlay(store: SandboxStore, table: str, model: type) -> tuple:
    rows = []
    for index, row in enumerate(store.read_records(table)):
        try:
            rows.append(model.model_validate(row))
        except ValidationError as exc:
            raise DataLoadError(f"sandbox {table}[{index}] is invalid: {exc.errors()[0]['msg']}") from exc
    return tuple(rows)


def load_ledger(data_dir: Path, sandbox_dir: Path) -> Ledger:
    """Source data plus applied sandbox records, as one immutable snapshot.

    Input: source data directory, sandbox directory (may not exist yet).
    Output: Ledger where sandbox plans, invoices and memos are appended after the source rows.
    """
    base = load_base(data_dir)
    store = SandboxStore(sandbox_dir)
    return Ledger(
        plans=base.plans + _overlay(store, "plans", Plan),
        invoices=base.invoices + _overlay(store, "invoices", Invoice),
        credit_memos=base.credit_memos + _overlay(store, "credit_memos", CreditMemo),
        fx_rates=base.fx_rates,
    )
