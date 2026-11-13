"""Detectors on inline fixtures shaped like the real data. Each test isolates one rule.

All fixtures use as_of explicitly, so results never depend on the real clock.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.domain.detectors import FindingKind, investigate
from app.domain.models import ActionKind, UnknownPlanError
from tests.domain.factories import D, invoice, ledger, memo, plan, rate

TERM = 12


def kinds(findings):
    return [f.kind for f in findings]


def of_kind(findings, kind):
    return [f for f in findings if f.kind is kind]


def monthly_invoices(months, amount="8000", plan_id="P-1", customer="Acme", desc="Monthly Analytics subscription"):
    """One invoice per listed month, issued on the 5th, like ACME's real invoices."""
    return [
        invoice(
            invoice_id=f"I-{plan_id}-{m:02d}",
            plan_id=plan_id,
            customer_name=customer,
            issue_date=date(2025, m, 5),
            due_date=date(2025, m, 20),
            amount_invoiced=D(amount),
            description=desc,
        )
        for m in months
    ]


def test_missing_period_is_found_with_make_good_gap():
    """Monthly 8 000 plan with no September invoice: September is a 8 000 gap and make-good."""
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    invs = monthly_invoices([m for m in range(1, 13) if m != 9])
    result = investigate(ledger([p], invs), as_of=date(2025, 12, 31), term_months=TERM)

    missing = of_kind(result, FindingKind.MISSING_PERIOD)
    assert [f.period_start for f in missing] == [date(2025, 9, 1)]
    gap = missing[0]
    assert gap.expected.amount == Decimal("8000.00")
    assert gap.delta.amount == Decimal("-8000.00")
    assert gap.suggested_action is ActionKind.MAKE_GOOD_INVOICE
    assert gap.confidence == "high"


def test_fully_billed_plan_is_a_clean_control_case():
    """Every period billed exactly: no findings at all. The control for all other detectors."""
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    invs = monthly_invoices(range(1, 13))
    assert investigate(ledger([p], invs), as_of=date(2025, 12, 31), term_months=TERM) == []


def test_underbilled_annual_plan_gives_make_good_for_shortfall():
    """Annual 120 000 billed 100 000: short by 20 000, suggested as a make-good."""
    p = plan(plan_id="C-1010", customer_name="Initech", total_value=D(120000), cadence="Annual",
             start_date=date(2025, 1, 15), entitlements=("Analytics",))
    inv = invoice(invoice_id="I-9201", plan_id="C-1010", customer_name="Initech",
                  issue_date=date(2025, 1, 20), due_date=date(2025, 2, 15),
                  amount_invoiced=D(100000), status="paid", description="Analytics annual")
    result = investigate(ledger([p], [inv]), as_of=date(2025, 12, 31), term_months=TERM)

    under = of_kind(result, FindingKind.AMOUNT_UNDERBILLED)
    assert len(under) == 1
    assert under[0].delta.amount == Decimal("-20000.00")
    assert under[0].suggested_action is ActionKind.MAKE_GOOD_INVOICE
    assert "short by 20000.00 USD" in under[0].summary
    assert not of_kind(result, FindingKind.MISSING_PERIOD)


def test_foreign_currency_overbilling_is_credit_memo_candidate():
    """EUR 25 000 at 1.08 is USD 27 000 against a USD 25 000 period: excess 2 000, credit memo target."""
    p = plan(plan_id="A1", total_value=D(100000), cadence="Quarterly", start_date=date(2025, 7, 1),
             entitlements=("Analytics",))
    inv = invoice(invoice_id="I-EUR", plan_id="A1", issue_date=date(2025, 7, 10), due_date=date(2025, 7, 30),
                  amount_invoiced=D(25000), currency="EUR", description="Quarterly Analytics")
    result = investigate(
        ledger([p], [inv], rates=[rate(date=date(2025, 7, 10))]),
        as_of=date(2025, 7, 31), term_months=TERM,
    )
    over = of_kind(result, FindingKind.AMOUNT_OVERBILLED)
    assert len(over) == 1
    assert over[0].delta.amount == Decimal("2000.00")
    assert over[0].target_invoice_id == "I-EUR"
    assert over[0].suggested_action is ActionKind.CREDIT_MEMO
    assert any("27000.00 USD" in line for line in over[0].calculation)
    assert of_kind(result, FindingKind.CURRENCY_MISMATCH)


def test_existing_credit_memo_offsets_overbilling_to_zero_variance():
    """The same overbilling with a 2 000 credit memo already issued: net equals expected, no variance."""
    p = plan(plan_id="A1", total_value=D(100000), cadence="Quarterly", start_date=date(2025, 7, 1),
             entitlements=("Analytics",))
    inv = invoice(invoice_id="I-EUR", plan_id="A1", issue_date=date(2025, 7, 10), due_date=date(2025, 7, 30),
                  amount_invoiced=D(25000), currency="EUR", description="Quarterly Analytics")
    result = investigate(
        ledger([p], [inv], memos=[memo(memo_id="M-1", plan_id="A1", invoice_id="I-EUR", amount=D(2000),
                                       issue_date=date(2025, 8, 1))],
               rates=[rate(date=date(2025, 7, 10))]),
        as_of=date(2025, 7, 31), term_months=TERM,
    )
    assert not of_kind(result, FindingKind.AMOUNT_OVERBILLED)
    assert not of_kind(result, FindingKind.AMOUNT_UNDERBILLED)


def test_same_currency_overbilling_without_memo_is_credit_candidate():
    """USD overbilling of 500 with no memo: credit memo for 500 targeting the invoice."""
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    invs = monthly_invoices(range(1, 13))
    invs[0] = invoice(invoice_id="I-9001", plan_id="P-1", issue_date=date(2025, 1, 5), amount_invoiced=D(8500))
    result = investigate(ledger([p], invs), as_of=date(2025, 1, 31), term_months=TERM)
    over = of_kind(result, FindingKind.AMOUNT_OVERBILLED)
    assert len(over) == 1 and over[0].delta.amount == Decimal("500.00")
    assert over[0].target_invoice_id == "I-9001"


def test_duplicate_invoices_in_one_period_are_flagged_and_overbilled():
    """Two 8 000 invoices in January: duplicate flagged, and the excess 8 000 is a credit candidate."""
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    invs = monthly_invoices([1]) + [
        invoice(invoice_id="I-DUP", plan_id="P-1", issue_date=date(2025, 1, 12), amount_invoiced=D(8000))
    ]
    result = investigate(ledger([p], invs), as_of=date(2025, 1, 31), term_months=TERM)
    assert of_kind(result, FindingKind.DUPLICATE_BILLING)
    over = of_kind(result, FindingKind.AMOUNT_OVERBILLED)
    assert len(over) == 1 and over[0].delta.amount == Decimal("8000.00")


def test_orphan_invoice_is_reported_with_customer_candidates_and_not_attached():
    """plan_id = '' is never attached automatically. Candidate plans for that customer are listed."""
    p = plan(plan_id="C-1007", customer_name="Globex Ltd", total_value=D(90000), cadence="Quarterly",
             start_date=date(2025, 3, 1), entitlements=())
    orphan = invoice(invoice_id="I-ORPHAN", plan_id="", customer_name="Globex Ltd", issue_date=date(2025, 12, 10))
    result = investigate(ledger([p], [orphan]), as_of=date(2025, 3, 1), term_months=TERM)
    found = of_kind(result, FindingKind.ORPHAN_INVOICE)
    assert len(found) == 1
    assert found[0].candidate_plan_ids == ("C-1007",)
    assert found[0].plan_id is None
    assert found[0].suggested_action is None


def test_invoice_tagged_to_superseded_plan_after_amendment_violates_window():
    """Base plan C-1007 is superseded on 2025-07-01. An invoice tagged to it but issued 2025-09-12 is out of window."""
    base = plan(plan_id="C-1007", customer_name="Globex Ltd", total_value=D(90000), cadence="Quarterly",
                start_date=date(2025, 3, 1), entitlements=())
    amend = plan(plan_id="C-1007-A1", customer_name="Globex Ltd", total_value=D(100000), cadence="Quarterly",
                 start_date=date(2025, 7, 1), amends="C-1007", entitlements=())
    stray = invoice(invoice_id="I-STRAY", plan_id="C-1007", customer_name="Globex Ltd",
                    issue_date=date(2025, 9, 12), amount_invoiced=D(25000))
    result = investigate(ledger([base, amend], [stray]), as_of=date(2025, 7, 31), term_months=TERM)
    violations = of_kind(result, FindingKind.PLAN_WINDOW_VIOLATION)
    assert len(violations) == 1
    assert violations[0].invoice_ids == ("I-STRAY",)


def test_unknown_plan_reference_is_reported():
    """An invoice whose plan_id is not in billing plans is reported, never silently dropped."""
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    bad = invoice(invoice_id="I-BAD", plan_id="P-999", issue_date=date(2025, 3, 5))
    result = investigate(ledger([p], [bad]), as_of=date(2025, 1, 31), term_months=TERM)
    assert [f.invoice_ids for f in of_kind(result, FindingKind.UNKNOWN_PLAN)] == [("I-BAD",)]


def test_credit_memo_pointing_at_missing_invoice_is_anomaly():
    """A memo against an invoice that does not exist is flagged."""
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    result = investigate(ledger([p], monthly_invoices([1]), memos=[memo(invoice_id="I-NOPE")]),
                         as_of=date(2025, 1, 31), term_months=TERM)
    assert of_kind(result, FindingKind.CREDIT_MEMO_ANOMALY)


def test_credit_memo_larger_than_same_currency_invoice_is_anomaly():
    """A 9 000 memo against an 8 000 USD invoice is flagged."""
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    result = investigate(ledger([p], monthly_invoices([1]), memos=[memo(invoice_id="I-P-1-01", amount=D(9000))]),
                         as_of=date(2025, 1, 31), term_months=TERM)
    assert of_kind(result, FindingKind.CREDIT_MEMO_ANOMALY)


def test_unpaid_past_due_is_overdue_but_not_yet_due_is_not():
    """Overdue depends on the as-of date: due before it is overdue; due after it is not."""
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    past = invoice(invoice_id="I-PAST", plan_id="P-1", status="unpaid", due_date=date(2025, 2, 1), issue_date=date(2025, 1, 5))
    future = invoice(invoice_id="I-FUT", plan_id="P-1", status="unpaid", due_date=date(2025, 6, 1), issue_date=date(2025, 5, 5))
    result = investigate(ledger([p], [past, future]), as_of=date(2025, 3, 1), term_months=TERM)
    overdue = of_kind(result, FindingKind.OVERDUE_UNPAID)
    assert [f.invoice_ids for f in overdue] == [("I-PAST",)]
    assert overdue[0].suggested_action is None


def test_entitlement_without_invoice_evidence_is_low_confidence():
    """'Premium Support' never appears in invoice text: flagged as low-confidence, not a money finding."""
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1),
             entitlements=("Analytics", "Premium Support"))
    result = investigate(ledger([p], monthly_invoices([1])), as_of=date(2025, 1, 31), term_months=TERM)
    ents = of_kind(result, FindingKind.ENTITLEMENT_NOT_EVIDENCED)
    assert len(ents) == 1
    assert "Premium Support" in ents[0].summary
    assert ents[0].confidence == "low"
    assert ents[0].delta is None


def test_missing_fx_rate_blocks_judgement_for_that_period_only():
    """A EUR invoice with no EUR->USD rate cannot be compared, so the period is blocked, not guessed."""
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    eur = invoice(invoice_id="I-EUR", plan_id="P-1", issue_date=date(2025, 1, 5), amount_invoiced=D(8000), currency="EUR")
    result = investigate(ledger([p], [eur]), as_of=date(2025, 1, 31), term_months=TERM)
    blocked = of_kind(result, FindingKind.FX_RATE_MISSING)
    assert len(blocked) == 1
    assert blocked[0].confidence == "low"
    assert not of_kind(result, FindingKind.AMOUNT_OVERBILLED)
    assert not of_kind(result, FindingKind.MISSING_PERIOD)


def test_as_of_date_limits_which_periods_are_judged():
    """Periods starting after the as-of date are not judged, so only January is missing on 2025-01-31."""
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    result = investigate(ledger([p], []), as_of=date(2025, 1, 31), term_months=TERM)
    assert [f.period_start for f in of_kind(result, FindingKind.MISSING_PERIOD)] == [date(2025, 1, 1)]


def test_plan_scope_returns_only_that_family_and_unknown_plan_raises():
    """plan_id narrows results to its amendment family; an unknown plan_id is an error."""
    a = plan(plan_id="A", total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    b = plan(plan_id="B", customer_name="Other", total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    result = investigate(ledger([a, b], []), as_of=date(2025, 1, 31), term_months=TERM, plan_id="A")
    assert {f.plan_id for f in result} == {"A"}
    with pytest.raises(UnknownPlanError):
        investigate(ledger([a], []), as_of=date(2025, 1, 31), term_months=TERM, plan_id="ZZZ")


def test_investigation_is_deterministic_and_stateless():
    """Two runs on the same snapshot give identical findings and ids. Nothing is stored between calls."""
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    snapshot = ledger([p], monthly_invoices([1, 2, 4]))
    first = investigate(snapshot, as_of=date(2025, 4, 30), term_months=TERM)
    second = investigate(snapshot, as_of=date(2025, 4, 30), term_months=TERM)
    assert first == second
    assert len({f.finding_id for f in first}) == len(first)
