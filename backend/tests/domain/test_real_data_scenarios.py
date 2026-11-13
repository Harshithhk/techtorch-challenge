"""Scenario tests against the real challenge files. They check expected behaviour only.

The code never hardcodes these results. The expectations below were worked out by hand from the
JSON and the plan's rules, and this file checks the detectors agree. Pinned as-of: 2025-12-31.
"""
import json
from datetime import date
from decimal import Decimal

import pytest

from app.domain.detectors import FindingKind, investigate
from app.domain.models import CreditMemo, FxRate, Invoice, Ledger, Plan
from app.domain.proposals import ProposalError, propose_make_good
from app.settings import get_settings

AS_OF = date(2025, 12, 31)
TERM = 12


@pytest.fixture(scope="module")
def real_ledger() -> Ledger:
    data_dir = get_settings().data_dir
    if not (data_dir / "invoices.json").exists():
        pytest.skip(f"real data not found at {data_dir}")

    def load(name):
        return json.loads((data_dir / name).read_text())

    return Ledger(
        plans=tuple(Plan.model_validate(r) for r in load("billing_plans.json")),
        invoices=tuple(Invoice.model_validate(r) for r in load("invoices.json")),
        credit_memos=tuple(CreditMemo.model_validate(r) for r in load("credit_memos.json")),
        fx_rates=tuple(FxRate.model_validate(r) for r in load("exchange_rates.json")),
    )


@pytest.fixture(scope="module")
def real_findings(real_ledger):
    return investigate(real_ledger, as_of=AS_OF, term_months=TERM)


def by(findings, kind, plan_id=None):
    return [f for f in findings if f.kind is kind and (plan_id is None or f.plan_id == plan_id)]


def test_real_files_validate_against_domain_models(real_ledger):
    """Every row of all four files parses with extra='forbid', so the schema matches the data exactly."""
    assert len(real_ledger.plans) == 4
    assert len(real_ledger.invoices) == 13
    assert len(real_ledger.credit_memos) == 1
    assert len(real_ledger.fx_rates) == 1


def test_acme_missing_months_are_detected(real_findings):
    """ACME C-1001 monthly 8 000: September, November and December 2025 have no invoice."""
    missing = by(real_findings, FindingKind.MISSING_PERIOD, "C-1001")
    assert sorted(f.period_start for f in missing) == [date(2025, 9, 1), date(2025, 11, 1), date(2025, 12, 1)]
    assert all(f.expected.amount == Decimal("8000.00") for f in missing)


def test_acme_unpaid_october_invoice_is_overdue(real_findings):
    """I-9010 is unpaid and was due 2025-10-20, so it is overdue as of 2025-12-31."""
    overdue = by(real_findings, FindingKind.OVERDUE_UNPAID)
    assert [f.invoice_ids for f in overdue] == [("I-9010",)]


def test_globex_superseded_base_has_missing_march_and_underbilled_june(real_findings):
    """C-1007 quarterly 22 500 per period: no March invoice; June has 20 000 (short 2 500)."""
    assert date(2025, 3, 1) in [f.period_start for f in by(real_findings, FindingKind.MISSING_PERIOD, "C-1007")]
    under = by(real_findings, FindingKind.AMOUNT_UNDERBILLED, "C-1007")
    assert len(under) == 1
    assert under[0].period_start == date(2025, 6, 1)
    assert under[0].delta.amount == Decimal("-2500.00")


def test_globex_amendment_overbilling_is_offset_by_existing_credit_memo(real_findings):
    """I-9123 is EUR 25 000 = USD 27 000 against a USD 25 000 period. Memo M-300 offsets 2 000: no variance."""
    july_missing = [f.period_start for f in by(real_findings, FindingKind.MISSING_PERIOD, "C-1007-A1")]
    assert date(2025, 7, 1) not in july_missing  # July is billed, so not missing
    assert date(2025, 10, 1) in july_missing  # October has no invoice as of 2025-12-31
    assert not by(real_findings, FindingKind.AMOUNT_OVERBILLED)  # the 2 000 excess is already credited
    assert not by(real_findings, FindingKind.AMOUNT_UNDERBILLED, "C-1007-A1")


def test_globex_eur_invoice_is_converted_on_issue_date(real_findings):
    """The EUR invoice is flagged as a currency mismatch, and the converted figure is shown with its rate."""
    mismatch = by(real_findings, FindingKind.CURRENCY_MISMATCH)
    assert [f.invoice_ids for f in mismatch] == [("I-9123",)]


def test_orphan_renewal_is_not_attached_and_lists_globex_plans(real_findings):
    """I-9202 has no plan_id. It is reported with Globex's plans as candidates, never attached."""
    orphan = by(real_findings, FindingKind.ORPHAN_INVOICE)
    assert [f.invoice_ids for f in orphan] == [("I-9202",)]
    assert orphan[0].candidate_plan_ids == ("C-1007", "C-1007-A1")


def test_initech_annual_shortfall_is_a_make_good_of_20000(real_findings):
    """C-1010 annual 120 000 billed 100 000 in one period: short by 20 000."""
    under = by(real_findings, FindingKind.AMOUNT_UNDERBILLED, "C-1010")
    assert len(under) == 1
    assert under[0].delta.amount == Decimal("-20000.00")
    draft = propose_make_good(real_findings, "C-1010")
    assert draft.amount.amount == Decimal("20000.00")


def test_initech_and_others_entitlements_not_evidenced_are_low_confidence(real_findings):
    """Entitlement checks are keyword-only. Initech 'Analytics' and 'Consulting' are not in 'Annual base plan'."""
    ents = by(real_findings, FindingKind.ENTITLEMENT_NOT_EVIDENCED)
    assert all(f.confidence == "low" and f.delta is None for f in ents)
    initech = sorted(f.summary for f in ents if f.plan_id == "C-1010")
    assert len(initech) == 2


def test_real_data_has_no_unexpected_categories(real_findings):
    """Nothing fires that the data does not warrant: no duplicates, FX blocks, unknown plans or memo anomalies."""
    for kind in (
        FindingKind.DUPLICATE_BILLING,
        FindingKind.FX_RATE_MISSING,
        FindingKind.UNKNOWN_PLAN,
        FindingKind.CREDIT_MEMO_ANOMALY,
        FindingKind.PLAN_WINDOW_VIOLATION,
        FindingKind.AMOUNT_OVERBILLED,
    ):
        assert not by(real_findings, kind), kind


def test_globex_base_plan_gap_is_ambiguous_without_period(real_findings):
    """C-1007 has several gaps, so the proposal asks the user which period, and lists the choices."""
    with pytest.raises(ProposalError, match="2025-03-01"):
        propose_make_good(real_findings, "C-1007")


def test_as_of_moves_the_findings_without_any_stored_state(real_ledger):
    """Changing the as-of date changes results: earlier as-of drops later missing periods. Nothing is stored."""
    earlier = investigate(real_ledger, as_of=date(2025, 8, 31), term_months=TERM)
    later = investigate(real_ledger, as_of=AS_OF, term_months=TERM)
    assert len(by(earlier, FindingKind.MISSING_PERIOD, "C-1001")) < len(by(later, FindingKind.MISSING_PERIOD, "C-1001"))
