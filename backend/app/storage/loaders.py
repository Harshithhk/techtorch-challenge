"""Read-only loaders for the four source JSON files. Never writes to the source directory.

Numbers are parsed with parse_float=Decimal, so no float ever enters the domain.
Each row is validated against its domain model (extra keys are rejected).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.domain.models import CreditMemo, FxRate, Invoice, Ledger, Plan
from app.storage.errors import DataLoadError

BASE_FILES: dict[str, type] = {
    "billing_plans.json": Plan,
    "invoices.json": Invoice,
    "credit_memos.json": CreditMemo,
    "exchange_rates.json": FxRate,
}


def read_rows(path: Path, model: type) -> tuple[Any, ...]:
    """Read one JSON array file and validate each row against `model`.

    Input: path to a JSON file whose top level is an array, the pydantic model for each row.
    Output: tuple of validated model instances, in file order.
    Raises DataLoadError (with file name and row index) for missing files, bad JSON, non-array
    top levels, or rows that fail validation.
    """
    if not path.exists():
        raise DataLoadError(f"missing data file: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), parse_float=_decimal_from_float)
    except json.JSONDecodeError as exc:
        raise DataLoadError(f"{path.name}: invalid JSON at line {exc.lineno}: {exc.msg}") from exc
    if not isinstance(raw, list):
        raise DataLoadError(f"{path.name}: top level must be a JSON array")

    rows = []
    for index, row in enumerate(raw):
        try:
            rows.append(model.model_validate(row))
        except ValidationError as exc:
            first = exc.errors()[0]
            location = ".".join(str(part) for part in first["loc"]) or "<row>"
            raise DataLoadError(f"{path.name}[{index}] field '{location}': {first['msg']}") from exc
    return tuple(rows)


def _decimal_from_float(text: str):
    """json parse_float hook: keep numbers exact by reading them as Decimal."""
    from decimal import Decimal

    return Decimal(text)


def load_base(data_dir: Path) -> Ledger:
    """Load the four read-only source files into a Ledger (no sandbox overlay).

    Input: directory containing billing_plans.json, invoices.json, credit_memos.json, exchange_rates.json.
    Output: Ledger. Raises DataLoadError on any missing or invalid file.
    """
    return Ledger(
        plans=read_rows(data_dir / "billing_plans.json", Plan),
        invoices=read_rows(data_dir / "invoices.json", Invoice),
        credit_memos=read_rows(data_dir / "credit_memos.json", CreditMemo),
        fx_rates=read_rows(data_dir / "exchange_rates.json", FxRate),
    )
