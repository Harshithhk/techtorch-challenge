"""FX lookup: direct, prior-date, inverse, identity, and refusal. Never looks ahead."""
from datetime import date
from decimal import Decimal

import pytest

from app.domain.fx import NoFxRateError, convert
from app.domain.money import Money
from tests.domain.factories import rate


def eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def test_identity_when_currencies_match():
    """Same currency is returned unchanged with method identity."""
    result = convert(Money(Decimal("100"), "USD"), "USD", date(2025, 1, 1), [])
    assert result.method == "identity"
    assert result.money.amount == Decimal("100.00")


def test_direct_rate_on_exact_date_matches_pdf_example():
    """EUR 25 000 at 1.08 on 2025-09-12 is USD 27 000 (the PDF's overbilling example)."""
    result = convert(eur("25000"), "USD", date(2025, 9, 12), [rate()])
    assert result.method == "direct"
    assert result.money.amount == Decimal("27000.00")
    assert result.rate_date == date(2025, 9, 12)


def test_uses_latest_rate_on_or_before_date_never_later():
    """With several rates, the most recent one on or before the date wins; later rates are ignored."""
    rates = [
        rate(date=date(2025, 9, 1), rate=Decimal("1.05")),
        rate(date=date(2025, 9, 10), rate=Decimal("1.07")),
        rate(date=date(2025, 9, 20), rate=Decimal("1.10")),
    ]
    result = convert(eur("100"), "USD", date(2025, 9, 15), rates)
    assert result.rate == Decimal("1.07")
    assert result.rate_date == date(2025, 9, 10)


def test_no_look_ahead_when_only_future_rate_exists():
    """A rate dated after the transaction must not be used."""
    with pytest.raises(NoFxRateError):
        convert(eur("100"), "USD", date(2025, 1, 1), [rate(date=date(2025, 9, 12))])


def test_inverse_pair_used_when_only_reverse_exists():
    """If only USD->EUR is stored, EUR->USD uses 1/rate."""
    rates = [rate(from_currency="USD", to_currency="EUR", rate=Decimal("0.5"))]
    result = convert(eur("100"), "USD", date(2025, 9, 12), rates)
    assert result.method == "inverse"
    assert result.rate == Decimal("2")
    assert result.money.amount == Decimal("200.00")


def test_missing_pair_raises_and_never_guesses():
    """No rate for the pair means an explicit error, not a silent default."""
    with pytest.raises(NoFxRateError):
        convert(eur("100"), "GBP", date(2025, 9, 12), [rate()])


def test_describe_is_human_readable_evidence():
    """describe() gives the calculation line used as finding evidence."""
    result = convert(eur("25000"), "USD", date(2025, 9, 12), [rate()])
    text = result.describe(eur("25000"))
    assert "25000.00 EUR -> 27000.00 USD" in text
    assert "2025-09-12" in text
