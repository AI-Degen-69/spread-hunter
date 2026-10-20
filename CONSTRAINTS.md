Branch: i718/settle-bare-rebate-type | Issue: #718

# Constraints - #718 settle the bare REBATE activity type in the PnL cross-check

`CREDIT_TYPES` counts a bare `REBATE` as a venue credit but `ACTIVITY_TYPES`
never requests one, so the two lists disagree about a type the feed may or may
not send. One of them has to give.

## The bar

- The two lists agree about bare `REBATE`, and the reason is written in the
  comment above them, citing venue evidence (the documented `/activity` type
  enum), not code.
- Bare `REBATE` is removed from `CREDIT_TYPES`; `ACTIVITY_TYPES` is unchanged.
  Remaining credit order stays: `MAKER_REBATE, REWARD, REFERRAL_REWARD,
  CONVERSION`.
- A test pins the decision: a bare `REBATE` row is not a type the replay knows
  (unreadable, `UNJUDGED`, never silently dropped).
- `python -m pytest -q tests/test_pnl_crosscheck.py tests/test_venue_fees.py`
  passes.
- Total-preserving invariant holds: credits stay apart from trading cashflow;
  `pnl_inclusive` adds them back. No change to how a credit is counted.

## Deliberate non-changes (a later reviewer must not "fix" these back)

- `ACTIVITY_TYPES`, `ASC_TYPES`, `ACTIVITY_PAGE_SIZE`, the row cap: untouched.
- `_cash_delta()`, `replay_rows()`, `_walk_type()`, `_get_json()`,
  `replay_cashflow()`, `Replay`: no logic change.
- `main()` CLI, `--inclusive` behaviour, the `maker_rebates` adjustment: untouched.
- KPI report (`core_brain/kpi.py`), fee math (`core_brain/venue_fees.py`),
  `venue.py`, `statistics_report.py`, validation artifacts: untouched.
- `tests/test_venue_fees.py`: untouched, must still pass.
- `docs/issues/708-noticed-but-not-touching.md` (incl. N4): untouched ledger.
- No other activity type added or removed (`DEPOSIT`, `WITHDRAWAL`, `YIELD`,
  `TAKER_REBATE` stay out of scope even though the docs list them).

## Process

- No live command, no venue call, no write to `data/orders.db`. No
  `python -m core_brain.pnl_crosscheck` CLI run (it makes network reads and can
  create/migrate the registry DB); verification is replay-level only.
- No direct push to `main`; the change ships through a PR.
- Targeted suites locally only; GitHub CI on Ubuntu and Windows is the merge gate.
- No `pytest` / `gh` / `git` step inside the operator's "How to verify".
