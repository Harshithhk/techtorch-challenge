"""Currency conversion over sparse FX rates. Pure domain, no I/O.

Lookup order (plan section 2.5):
1. Direct pair (from -> to) with the latest date on or before the transaction date.
2. Inverse pair (to -> from) with the latest date on or before it, using 1/rate.
3. Otherwise NoFxRateError. The domain never guesses a rate.

Future-dated rates are never used (no look-ahead).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from collections.abc import Sequence

from app.domain.models import DomainError, FxRate
from app.domain.money import Money


class NoFxRateError(DomainError):
    """No usable rate exists for the pair on or before the requested date."""


@dataclass(frozen=True)
class Conversion:
    """Result of converting a Money value, with the evidence needed to explain it.

    rate_date is None for an identity conversion. method is one of
    "identity", "direct" or "inverse".
    """

    money: Money
    rate: Decimal
    rate_date: date | None
    method: str

    def describe(self, source: Money) -> str:
        """Human-readable calculation line, used as evidence in findings.

        Input: the original Money. Output: e.g. "25000.00 EUR -> 27000.00 USD (rate 1.080000 from 2025-09-12, direct)".
        """
        if self.method == "identity":
            return f"{source} (already in {self.money.currency})"
        return (
            f"{source} -> {self.money} "
            f"(rate {self.rate:.6f} from {self.rate_date.isoformat()}, {self.method})"
        )


def _latest_on_or_before(
    rates: Sequence[FxRate], from_ccy: str, to_ccy: str, on_date: date
) -> FxRate | None:
    """Return the most recent rate for from->to dated on or before on_date, or None."""
    candidates = [
        r
        for r in rates
        if r.from_currency == from_ccy and r.to_currency == to_ccy and r.date <= on_date
    ]
    return max(candidates, key=lambda r: r.date, default=None)


def convert(money: Money, to_currency: str, on_date: date, rates: Sequence[FxRate]) -> Conversion:
    """Convert `money` into `to_currency` using the rate applicable on `on_date`.

    Input: Money, target currency, transaction date, all known FX rates.
    Output: Conversion (converted Money plus the rate and its date).
    Raises: NoFxRateError when neither a direct nor an inverse rate exists on or before on_date.
    """
    if money.currency == to_currency:
        return Conversion(money=money, rate=Decimal("1"), rate_date=None, method="identity")

    direct = _latest_on_or_before(rates, money.currency, to_currency, on_date)
    if direct is not None:
        converted = Money(money.amount * direct.rate, to_currency)
        return Conversion(converted, direct.rate, direct.date, "direct")

    inverse = _latest_on_or_before(rates, to_currency, money.currency, on_date)
    if inverse is not None:
        applied_rate = Decimal("1") / inverse.rate
        converted = Money(money.amount / inverse.rate, to_currency)
        return Conversion(converted, applied_rate, inverse.date, "inverse")

    raise NoFxRateError(
        f"no {money.currency}->{to_currency} rate on or before {on_date.isoformat()}"
    )
