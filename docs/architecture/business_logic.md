# Business logic: how gaps are detected

Source of truth: `backend/app/domain/` (`schedule.py`, `detectors.py`, `fx.py`, `money.py`).
Pure Python with `Decimal`. No LLM, no I/O. Nothing is precomputed or stored: every investigation recomputes from the ledger.

## 1. Expected billing

| Rule | Detail |
|---|---|
| Term | Not in the data. Assumed 12 months from `start_date` (`DEFAULT_TERM_MONTHS`). |
| Periods per term | Monthly = 12, Quarterly = 4, Annual = 1. |
| Expected per period | `total_value ÷ periods`, rounded HALF_UP to cents. |
| Amendments | An `amends` row supersedes its base from its own `start_date`. The base is cut at that date. Periods are truncated, not prorated. |
| Period window | `[start, next start)`. An invoice belongs to the period whose window contains its `issue_date`. |

## 2. Per-period comparison

For each period that has started on or before the as-of date:

1. Convert every invoice in the window to the plan currency, on its issue date.
2. Convert the credit memos that reference those invoices, on their issue dates.
3. `net = invoices − credit memos`, and `variance = net − expected`.

| Result | Finding | Suggested action |
|---|---|---|
| No invoice in the window | `missing_period` | Make-good for the full expected amount |
| `variance < 0` | `amount_underbilled` | Make-good for `−variance` |
| `variance > 0` | `amount_overbilled` | Credit memo for `variance`, against the latest invoice in the window |
| More than one invoice in the window | `duplicate_billing` (info) | Evaluated as above as well |

## 3. Other checks

| Finding | Rule |
|---|---|
| `currency_mismatch` (info) | Invoice currency differs from the plan currency. Converted, not flagged as money. |
| `fx_rate_missing` (blocked) | No direct or inverse rate on or before the issue date. The period is not judged. No rate is guessed. |
| `plan_window_violation` | An invoice's issue date falls outside the active window of the plan it is tagged to. |
| `orphan_invoice` | `plan_id` is empty. Never attached automatically. Plans for the same customer are listed as candidates. |
| `unknown_plan` | `plan_id` is not in billing plans. |
| `credit_memo_anomaly` | A memo points at a missing invoice, a different plan, or (same currency only) more than the invoice amount. |
| `overdue_unpaid` | Status `unpaid` and due date before the as-of date. Reported as collections, not as a make-good. |
| `entitlement_not_evidenced` (low confidence) | A plan entitlement appears in none of its invoice descriptions. Keyword match only, so never counted as money. |

## 4. Proposals

- Amounts come only from the findings. A user-supplied amount can only lower a make-good or credit memo. Anything above the computed gap is refused.
- A plan amendment is validated: the effective date must be after the latest version starts, the total must be positive, and at least one term must change. Currency never changes.
- Before any write, the proposal is re-derived from the current ledger. If the amount has moved, the write is refused as stale.

## 5. Worked example (as-of 2025-12-31, real data)

| Plan | Finding | Basis |
|---|---|---|
| C-1001 ACME, Monthly 8 000 | Missing 2025-09-01, 2025-11-01, 2025-12-01 | No invoice issued in those windows |
| C-1007 Globex base, Quarterly 22 500 per period | Missing 2025-03-01. Underbilled 2 500 on 2025-06-01. | 20 000 billed for June against 22 500 expected |
| C-1007-A1 Globex amendment, Quarterly 25 000 per period | 2025-07-01 is not missing. 2025-10-01 is missing. The EUR invoice of 25 000 EUR converts to 27 000 USD, and the existing 2 000 credit memo leaves net 25 000, so there is no variance. | FX on issue date, plus credit memo |
| C-1010 Initech, Annual 120 000 | Underbilled 20 000 | 100 000 billed against 120 000 expected |
| Orphan I-9202 | Not attached. Candidates: C-1007, C-1007-A1. | `plan_id` is empty |

These results are checked by scenario tests (`backend/tests/domain/test_real_data_scenarios.py`). The code does not hardcode them.
