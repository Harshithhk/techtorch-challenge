"""Amendment families, active windows and expected billing periods. Pure domain.

Business rules (assumptions, plan section 2):
- Term length is not in the data. Each plan is assumed to run `term_months` from its start_date.
- Periods per term = term_months / cadence months. Expected per period = total_value / periods, HALF_UP to cents.
- An amendment (`amends` set) supersedes its base from its own start_date. The base's active
  window is cut at that date. Periods are truncated, not prorated.
- A period belongs to the plan whose active window contains the period's start.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.models import InvalidLedgerError, Plan
from app.domain.money import Money, quantize_cents


def add_months(start: date, months: int) -> date:
    """Add whole months to a date, clamping the day to the target month's length.

    Input: start date, months (may be 0). Output: date. Example: 2025-01-31 + 1 month -> 2025-02-28.
    """
    index = start.year * 12 + (start.month - 1) + months
    year, month0 = divmod(index, 12)
    day = min(start.day, monthrange(year, month0 + 1)[1])
    return date(year, month0 + 1, day)


@dataclass(frozen=True)
class Segment:
    """The window [start, end) during which one plan in a family is the active contract."""

    plan_id: str
    start: date
    end: date


@dataclass(frozen=True)
class BillingPeriod:
    """One expected billing period of one plan. Window is [start, end).

    expected is the amount the customer should be billed for this period, in the plan currency.
    """

    plan_id: str
    family_root: str
    index: int
    start: date
    end: date
    expected: Money


def build_families(plans: Sequence[Plan]) -> dict[str, list[Plan]]:
    """Group plans into amendment families keyed by root plan_id.

    Input: all plans. Output: {root_id: [root, amendment, ...]} with members sorted by start_date.
    Raises InvalidLedgerError on duplicate ids, unknown or cyclic `amends`, a root that is not the
    earliest member, a currency change inside a family, or non-increasing start dates.
    """
    by_id: dict[str, Plan] = {}
    for plan in plans:
        if plan.plan_id in by_id:
            raise InvalidLedgerError(f"duplicate plan_id {plan.plan_id}")
        by_id[plan.plan_id] = plan

    def root_of(plan_id: str) -> str:
        seen: set[str] = set()
        current = plan_id
        while by_id[current].amends is not None:
            if current in seen:
                raise InvalidLedgerError(f"amendment cycle at {current}")
            seen.add(current)
            target = by_id[current].amends
            if target not in by_id:
                raise InvalidLedgerError(f"{current} amends unknown plan {target}")
            current = target
        return current

    families: dict[str, list[Plan]] = defaultdict(list)
    for plan in plans:
        families[root_of(plan.plan_id)].append(plan)

    result: dict[str, list[Plan]] = {}
    for root, members in families.items():
        members.sort(key=lambda p: p.start_date)
        if members[0].plan_id != root:
            raise InvalidLedgerError(f"amendment of {root} starts before the base plan")
        if len({m.currency for m in members}) > 1:
            raise InvalidLedgerError(f"currency changes inside family {root}")
        for earlier, later in zip(members, members[1:]):
            if later.start_date <= earlier.start_date:
                raise InvalidLedgerError(
                    f"{later.plan_id} must start after {earlier.plan_id} (family {root})"
                )
        result[root] = members
    return result


def _periods_per_term(plan: Plan, term_months: int) -> int:
    """Number of billing periods in the term. Raises if the term is not a whole number of periods."""
    months = plan.cadence.months
    if term_months % months != 0:
        raise ValueError(f"term of {term_months} months is not divisible by {plan.cadence.value}")
    return term_months // months


def expected_per_period(plan: Plan, term_months: int) -> Money:
    """Expected amount of one billing period of `plan`.

    Input: plan, term length in months. Output: Money = total_value / periods, rounded to cents.
    """
    return Money(quantize_cents(plan.total_value / _periods_per_term(plan, term_months)), plan.currency)


def plan_segments(family: Sequence[Plan], term_months: int) -> list[Segment]:
    """Active window of each plan in a family, cut where the next amendment starts.

    Input: family sorted by start_date (as returned by build_families), term length.
    Output: one Segment per plan, in the same order. The end is exclusive.
    """
    segments: list[Segment] = []
    for index, plan in enumerate(family):
        end = add_months(plan.start_date, term_months)
        if index + 1 < len(family):
            end = min(end, family[index + 1].start_date)
        segments.append(Segment(plan.plan_id, plan.start_date, end))
    return segments


def build_periods(family: Sequence[Plan], term_months: int) -> list[BillingPeriod]:
    """All expected billing periods of a family, truncated at amendment boundaries.

    Input: family sorted by start_date, term length in months.
    Output: BillingPeriod list in chronological order. Periods start within each plan's segment.
    Note: this covers the whole term, including future periods. Callers filter by as-of date.
    """
    if not family:
        return []
    root = family[0].plan_id
    segments = {s.plan_id: s for s in plan_segments(family, term_months)}
    periods: list[BillingPeriod] = []
    for plan in family:
        segment = segments[plan.plan_id]
        step = plan.cadence.months
        amount = expected_per_period(plan, term_months)
        for index in range(_periods_per_term(plan, term_months)):
            start = add_months(plan.start_date, index * step)
            if start >= segment.end:
                break
            end = min(add_months(plan.start_date, (index + 1) * step), segment.end)
            periods.append(BillingPeriod(plan.plan_id, root, index, start, end, amount))
    return periods


def total_expected(periods: Sequence[BillingPeriod], currency: str) -> Money:
    """Sum the expected amount of periods in one currency. Output: Money total (zero if empty)."""
    return Money(sum((p.expected.amount for p in periods), Decimal("0")), currency)
