"""Money value type. Pure domain, no I/O.

Rules:
- Amounts are Decimal, never float. Floats are rejected at construction.
- Amounts are quantized to cents with ROUND_HALF_UP (ties away from zero).
- Arithmetic across currencies raises. Conversion belongs to fx.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import reduce
from collections.abc import Iterable

CENT = Decimal("0.01")


class CurrencyMismatchError(ValueError):
    """Two Money values in different currencies were combined without conversion."""


def quantize_cents(value: Decimal) -> Decimal:
    """Round a Decimal to 2 decimal places, ties away from zero.

    Input: Decimal (floats are rejected with TypeError).
    Output: Decimal with exponent -2.
    """
    if not isinstance(value, Decimal):
        raise TypeError(f"money must be Decimal, got {type(value).__name__}")
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Money:
    """An amount in one 3-letter currency code (for example USD, EUR).

    Input: amount (Decimal), currency (uppercase 3-letter str).
    Output: immutable value with the amount already quantized to cents.
    """

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError(f"money must be Decimal, got {type(self.amount).__name__}")
        if len(self.currency) != 3 or not self.currency.isalpha() or not self.currency.isupper():
            raise ValueError(f"invalid currency code: {self.currency!r}")
        object.__setattr__(self, "amount", quantize_cents(self.amount))

    def _same_currency(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise TypeError(f"cannot combine Money with {type(other).__name__}")
        if other.currency != self.currency:
            raise CurrencyMismatchError(f"cannot combine {self.currency} with {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"


def zero(currency: str) -> Money:
    """Zero amount in the given currency. Input: currency code. Output: Money(0.00, currency)."""
    return Money(Decimal("0"), currency)


def total(items: Iterable[Money], currency: str) -> Money:
    """Sum Money values that are all in `currency`.

    Input: iterable of Money, expected currency. Output: Money total (zero if empty).
    Raises CurrencyMismatchError if any item is in another currency.
    """
    return reduce(lambda acc, item: acc + item, items, zero(currency))
