"""Proposals: amounts come only from findings; overrides may only shrink; amendments validate."""
from datetime import date
from decimal import Decimal

import pytest

from app.domain.detectors import investigate
from app.domain.models import ActionKind, Cadence
from app.domain.proposals import ProposalError, propose_credit_memo, propose_make_good, propose_plan_amendment
from tests.domain.factories import D, invoice, ledger, plan, rate
from tests.domain.test_detectors import monthly_invoices

TERM = 12


def acme_missing_september():
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    invs = monthly_invoices([m for m in range(1, 13) if m != 9])
    return investigate(ledger([p], invs), as_of=date(2025, 12, 31), term_months=TERM)


def test_make_good_amount_is_the_domain_gap():
    """The make-good for the missing September period is exactly the 8 000 expected amount."""
    draft = propose_make_good(acme_missing_september(), "P-1", period_start=date(2025, 9, 1))
    assert draft.action_kind is ActionKind.MAKE_GOOD_INVOICE
    assert draft.amount.amount == Decimal("8000.00")
    assert draft.period_start == date(2025, 9, 1)
    assert draft.warnings == ()
    assert draft.finding_id and "missing_period" in draft.finding_id


def test_make_good_without_period_is_ambiguous_when_several_gaps_exist():
    """Sep, Nov and Dec are missing, so the plan alone is not enough: the error lists the options."""
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    invs = monthly_invoices([m for m in range(1, 13) if m not in (9, 11, 12)])
    findings = investigate(ledger([p], invs), as_of=date(2025, 12, 31), term_months=TERM)
    with pytest.raises(ProposalError, match="2025-09-01, 2025-11-01, 2025-12-01"):
        propose_make_good(findings, "P-1")


def test_make_good_override_below_gap_is_allowed_and_labelled():
    """A user may ask for less than the gap; the draft says so explicitly."""
    draft = propose_make_good(acme_missing_september(), "P-1", period_start=date(2025, 9, 1), requested_amount=D(5000))
    assert draft.amount.amount == Decimal("5000.00")
    assert draft.warnings and "user override" in draft.warnings[0]


def test_make_good_override_above_gap_is_refused():
    """The LLM cannot inflate a make-good beyond the computed gap."""
    with pytest.raises(ProposalError, match="exceeds"):
        propose_make_good(acme_missing_september(), "P-1", period_start=date(2025, 9, 1), requested_amount=D(9000))


@pytest.mark.parametrize("bad", [D(0), D(-1)])
def test_make_good_non_positive_override_refused(bad):
    """Zero or negative amounts are never drafted."""
    with pytest.raises(ProposalError):
        propose_make_good(acme_missing_september(), "P-1", period_start=date(2025, 9, 1), requested_amount=bad)


def test_make_good_for_clean_plan_is_refused():
    """No gap means nothing to make good."""
    with pytest.raises(ProposalError, match="no missing or underbilled"):
        propose_make_good([], "P-1")


def test_credit_memo_amount_is_the_domain_excess():
    """EUR 25 000 at 1.08 against a USD 25 000 period: the credit memo is exactly 2 000 USD."""
    p = plan(plan_id="A1", total_value=D(100000), cadence="Quarterly", start_date=date(2025, 7, 1))
    inv = invoice(invoice_id="I-9123", plan_id="A1", issue_date=date(2025, 7, 10), amount_invoiced=D(25000),
                  currency="EUR", description="Quarterly Analytics")
    findings = investigate(ledger([p], [inv], rates=[rate(date=date(2025, 7, 10))]),
                           as_of=date(2025, 7, 31), term_months=TERM)
    draft = propose_credit_memo(findings, "I-9123")
    assert draft.action_kind is ActionKind.CREDIT_MEMO
    assert draft.amount.amount == Decimal("2000.00")
    assert draft.amount.currency == "USD"
    assert draft.target_invoice_id == "I-9123"


def test_credit_memo_for_invoice_without_overbilling_is_refused():
    """A credit memo needs an overbilling finding on that invoice."""
    with pytest.raises(ProposalError, match="no overbilling"):
        propose_credit_memo([], "I-9001")


def test_amendment_changes_total_and_reports_per_period_effect():
    """Monthly 96 000 -> 100 000 from mid-year: total change 4 000, per period 8 000 -> 8 333.33."""
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    draft = propose_plan_amendment(p, effective_date=date(2025, 7, 1), term_months=TERM, new_total=D(100000))
    assert draft.action_kind is ActionKind.PLAN_AMENDMENT
    assert draft.amount.amount == Decimal("4000.00")
    assert draft.change_set["amends"] == "P-1"
    assert draft.change_set["per_period_before"] == "8000.00"
    assert draft.change_set["per_period_after"] == "8333.33"
    assert draft.change_set["after"]["total_value"] == "100000"


def test_amendment_can_add_entitlement_and_change_cadence():
    """Add 'Premium Support' and switch to quarterly; the change set lists both."""
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1), entitlements=("Analytics",))
    draft = propose_plan_amendment(p, effective_date=date(2025, 7, 1), term_months=TERM,
                                   new_cadence=Cadence.QUARTERLY, add_entitlements=["Premium Support"])
    assert draft.change_set["after"]["cadence"] == "Quarterly"
    assert draft.change_set["after"]["entitlements"] == ["Analytics", "Premium Support"]
    assert draft.amount.amount == Decimal("0.00")


def test_amendment_that_changes_nothing_is_refused():
    """An amendment must change something."""
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    with pytest.raises(ProposalError, match="changes nothing"):
        propose_plan_amendment(p, effective_date=date(2025, 7, 1), term_months=TERM, new_total=D(96000))


def test_amendment_validation_rejects_bad_total_and_early_date():
    """Non-positive totals and effective dates before the plan starts are refused."""
    p = plan(total_value=D(96000), cadence="Monthly", start_date=date(2025, 1, 1))
    with pytest.raises(ProposalError, match="positive"):
        propose_plan_amendment(p, effective_date=date(2025, 7, 1), term_months=TERM, new_total=D(0))
    with pytest.raises(ProposalError, match="before the plan starts"):
        propose_plan_amendment(p, effective_date=date(2024, 6, 1), term_months=TERM, new_total=D(100000))
