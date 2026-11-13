"""Money: Decimal-only, cent rounding, and refusing cross-currency arithmetic."""
from decimal import Decimal

import pytest

from app.domain.money import CurrencyMismatchError, Money, total, zero


def test_rejects_float_amounts():
    """Floats are never accepted: they would introduce binary rounding error."""
    with pytest.raises(TypeError):
        Money(8000.0, "USD")  # type: ignore[arg-type]


def test_quantizes_half_up_to_cents():
    """Ties round away from zero, at 2 decimal places."""
    assert Money(Decimal("0.005"), "USD").amount == Decimal("0.01")
    assert Money(Decimal("2.345"), "USD").amount == Decimal("2.35")
    assert Money(Decimal("-0.005"), "USD").amount == Decimal("-0.01")
    assert str(Money(Decimal("8000"), "USD")) == "8000.00 USD"


def test_add_and_subtract_same_currency():
    """Same-currency arithmetic returns a new Money with the expected amount."""
    a = Money(Decimal("25000"), "USD")
    b = Money(Decimal("2000"), "USD")
    assert (a - b).amount == Decimal("23000.00")
    assert (a + b).amount == Decimal("27000.00")
    assert (-b).amount == Decimal("-2000.00")


def test_cross_currency_arithmetic_raises():
    """Combining USD and EUR without an explicit conversion is refused."""
    with pytest.raises(CurrencyMismatchError):
        _ = Money(Decimal("1"), "USD") + Money(Decimal("1"), "EUR")


def test_invalid_currency_code_rejected():
    """Currency must be three uppercase letters."""
    with pytest.raises(ValueError):
        Money(Decimal("1"), "usd")
    with pytest.raises(ValueError):
        Money(Decimal("1"), "US")


def test_total_and_zero():
    """total() sums same-currency items; an empty sum is zero in the given currency."""
    items = [Money(Decimal("0.10"), "USD"), Money(Decimal("0.20"), "USD")]
    assert total(items, "USD").amount == Decimal("0.30")
    assert total([], "EUR") == zero("EUR")
