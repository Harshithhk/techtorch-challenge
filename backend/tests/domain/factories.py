"""Builders for inline domain fixtures. They mirror the real JSON shapes without reading files."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.domain.models import CreditMemo, FxRate, Invoice, Ledger, Plan


def D(value: str | int) -> Decimal:
    """Build a Decimal from a string or int. Floats are deliberately not accepted."""
    return Decimal(str(value))


def plan(**overrides: Any) -> Plan:
    """A base monthly USD plan with sensible defaults. Override any field by keyword."""
    base: dict[str, Any] = dict(
        plan_id="P-1",
        customer_name="Acme",
        total_value=D(96000),
        currency="USD",
        cadence="Monthly",
        start_date=date(2025, 1, 1),
        entitlements=("Analytics",),
        notes="",
    )
    base.update(overrides)
    return Plan.model_validate(base)


def invoice(**overrides: Any) -> Invoice:
    """A paid USD invoice with defaults. Override any field by keyword."""
    base: dict[str, Any] = dict(
        invoice_id="I-1",
        plan_id="P-1",
        customer_name="Acme",
        issue_date=date(2025, 1, 5),
        due_date=date(2025, 1, 20),
        amount_invoiced=D(8000),
        currency="USD",
        status="paid",
        description="Monthly Analytics subscription",
    )
    base.update(overrides)
    return Invoice.model_validate(base)


def memo(**overrides: Any) -> CreditMemo:
    """A USD credit memo with defaults. Override any field by keyword."""
    base: dict[str, Any] = dict(
        memo_id="M-1",
        plan_id="P-1",
        invoice_id="I-1",
        amount=D(100),
        currency="USD",
        issue_date=date(2025, 2, 1),
        reason="test",
    )
    base.update(overrides)
    return CreditMemo.model_validate(base)


def rate(**overrides: Any) -> FxRate:
    """An FX rate row. Defaults to EUR->USD 1.08 on 2025-09-12."""
    base: dict[str, Any] = dict(
        date=date(2025, 9, 12),
        from_currency="EUR",
        to_currency="USD",
        rate=D("1.08"),
    )
    base.update(overrides)
    return FxRate.model_validate(base)


def ledger(
    plans: list[Plan] | None = None,
    invoices: list[Invoice] | None = None,
    memos: list[CreditMemo] | None = None,
    rates: list[FxRate] | None = None,
) -> Ledger:
    """Assemble a Ledger snapshot from lists. Missing lists default to empty."""
    return Ledger(
        plans=tuple(plans or []),
        invoices=tuple(invoices or []),
        credit_memos=tuple(memos or []),
        fx_rates=tuple(rates or []),
    )
