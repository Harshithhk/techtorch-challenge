"""Schedule rules: month arithmetic, amendment families, truncation and per-period amounts."""
from datetime import date
from decimal import Decimal

import pytest

from app.domain.models import InvalidLedgerError
from app.domain.schedule import (
    add_months,
    build_families,
    build_periods,
    expected_per_period,
    plan_segments,
    total_expected,
)
from tests.domain.factories import D, plan


def test_add_months_clamps_day_to_month_end():
    """Jan 31 + 1 month is Feb 28 (or 29 in a leap year), never an invalid date."""
    assert add_months(date(2025, 1, 31), 1) == date(2025, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2025, 11, 15), 3) == date(2026, 2, 15)


def test_monthly_plan_twelve_periods_of_per_period_amount():
    """96 000 monthly over a 12-month term: 12 periods of 8 000 each."""
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    periods = build_periods([p], term_months=12)
    assert len(periods) == 12
    assert all(per.expected.amount == Decimal("8000.00") for per in periods)
    assert periods[-1].start == date(2025, 12, 1)
    assert periods[-1].end == date(2026, 1, 1)
    assert total_expected(periods, "USD").amount == Decimal("96000.00")


def test_annual_plan_is_one_period_of_full_value():
    """Annual cadence over 12 months is a single period of total_value."""
    p = plan(total_value=D(120000), cadence="Annual", start_date=date(2025, 1, 15))
    periods = build_periods([p], term_months=12)
    assert len(periods) == 1
    assert periods[0].expected.amount == Decimal("120000.00")
    assert periods[0].end == date(2026, 1, 15)


def test_amendment_truncates_base_and_starts_new_schedule():
    """Base quarterly plan 90 000 from 2025-03-01 is cut at the amendment (2025-07-01).

    Base keeps Mar and Jun at 22 500. Amendment 100 000 from 2025-07-01 gives four periods of 25 000.
    """
    base = plan(plan_id="B", total_value=D(90000), cadence="Quarterly", start_date=date(2025, 3, 1))
    amend = plan(
        plan_id="B-A1",
        total_value=D(100000),
        cadence="Quarterly",
        start_date=date(2025, 7, 1),
        amends="B",
    )
    families = build_families([base, amend])
    family = families["B"]
    assert [p.plan_id for p in family] == ["B", "B-A1"]

    segments = {s.plan_id: s for s in plan_segments(family, 12)}
    assert segments["B"].end == date(2025, 7, 1)
    assert segments["B-A1"].end == date(2026, 7, 1)

    periods = build_periods(family, 12)
    base_periods = [per for per in periods if per.plan_id == "B"]
    amend_periods = [per for per in periods if per.plan_id == "B-A1"]
    assert [per.start for per in base_periods] == [date(2025, 3, 1), date(2025, 6, 1)]
    assert all(per.expected.amount == Decimal("22500.00") for per in base_periods)
    assert [per.start for per in amend_periods] == [
        date(2025, 7, 1),
        date(2025, 10, 1),
        date(2026, 1, 1),
        date(2026, 4, 1),
    ]
    assert all(per.expected.amount == Decimal("25000.00") for per in amend_periods)
    assert all(per.family_root == "B" for per in periods)


def test_expected_per_period_rejects_term_not_divisible_by_cadence():
    """A 12-month term cannot be split into quarters of 5 months; this is a configuration error."""
    p = plan(cadence="Quarterly")
    with pytest.raises(ValueError):
        expected_per_period(p, term_months=5)


def test_families_reject_structural_errors():
    """Duplicate ids, unknown amends, cycles, currency change and bad start order are refused."""
    with pytest.raises(InvalidLedgerError):
        build_families([plan(plan_id="X"), plan(plan_id="X")])
    with pytest.raises(InvalidLedgerError):
        build_families([plan(plan_id="A", amends="MISSING", start_date=date(2025, 2, 1))])
    with pytest.raises(InvalidLedgerError):
        build_families(
            [
                plan(plan_id="A", amends="B"),
                plan(plan_id="B", amends="A"),
            ]
        )
    with pytest.raises(InvalidLedgerError):
        build_families(
            [
                plan(plan_id="B", start_date=date(2025, 1, 1)),
                plan(plan_id="B-A1", amends="B", currency="EUR", start_date=date(2025, 7, 1)),
            ]
        )
    with pytest.raises(InvalidLedgerError):
        build_families(
            [
                plan(plan_id="B", start_date=date(2025, 1, 1)),
                plan(plan_id="B-A1", amends="B", start_date=date(2025, 1, 1)),
            ]
        )
