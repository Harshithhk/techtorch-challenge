"""Typed domain records and shared errors. Pure data, no I/O.

Field names mirror the JSON files in data/technical-case/data/ (verified against
the real files in the planning phase). Unknown keys are rejected so schema drift
fails loudly instead of being silently ignored.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CURRENCY_PATTERN = r"^[A-Z]{3}$"


class DomainError(Exception):
    """Base class for domain-level failures that the agent can explain to the user."""


class InvalidLedgerError(DomainError):
    """The ledger violates a structural rule (duplicate ids, broken amendment chain, ...)."""


class UnknownPlanError(DomainError):
    """A plan_id was requested that does not exist in the ledger."""


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Cadence(StrEnum):
    """Billing cadence as written in billing_plans.json."""

    MONTHLY = "Monthly"
    QUARTERLY = "Quarterly"
    ANNUAL = "Annual"

    @property
    def months(self) -> int:
        """Months per billing period. Output: 1, 3 or 12."""
        return {Cadence.MONTHLY: 1, Cadence.QUARTERLY: 3, Cadence.ANNUAL: 12}[self]


class ActionKind(StrEnum):
    """The three corrective actions the agent may propose."""

    MAKE_GOOD_INVOICE = "make_good_invoice"
    CREDIT_MEMO = "credit_memo"
    PLAN_AMENDMENT = "plan_amendment"


class Plan(_Record):
    """One row of billing_plans.json. `amends` is set only on amendment rows.

    Note: the data has no term length and no per-period amount. Both are derived
    in schedule.py (term from settings, per-period = total_value / periods).
    """

    plan_id: str = Field(min_length=1)
    customer_name: str
    total_value: Decimal = Field(gt=0)
    currency: str = Field(pattern=CURRENCY_PATTERN)
    cadence: Cadence
    start_date: date
    entitlements: tuple[str, ...] = ()
    notes: str = ""
    amends: str | None = None


class Invoice(_Record):
    """One row of invoices.json. plan_id may be "" (orphan invoice)."""

    invoice_id: str = Field(min_length=1)
    plan_id: str
    customer_name: str
    issue_date: date
    due_date: date
    amount_invoiced: Decimal = Field(ge=0)
    currency: str = Field(pattern=CURRENCY_PATTERN)
    status: Literal["paid", "unpaid"]
    description: str = ""


class CreditMemo(_Record):
    """One row of credit_memos.json. Reduces the net amount of `invoice_id`."""

    memo_id: str = Field(min_length=1)
    plan_id: str
    invoice_id: str
    amount: Decimal = Field(gt=0)
    currency: str = Field(pattern=CURRENCY_PATTERN)
    issue_date: date
    reason: str = ""


class FxRate(_Record):
    """One row of exchange_rates.json: units of to_currency per 1 from_currency."""

    date: date
    from_currency: str = Field(pattern=CURRENCY_PATTERN)
    to_currency: str = Field(pattern=CURRENCY_PATTERN)
    rate: Decimal = Field(gt=0)


@dataclass(frozen=True)
class Ledger:
    """A full, immutable snapshot of the data the domain reasons over.

    Storage builds this from base JSON plus sandbox overlay. Domain never reads files.
    """

    plans: tuple[Plan, ...]
    invoices: tuple[Invoice, ...]
    credit_memos: tuple[CreditMemo, ...]
    fx_rates: tuple[FxRate, ...]
