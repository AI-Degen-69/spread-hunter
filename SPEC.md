# SPEC: Issue #360 — Run the queue-hold rehearsal (HUNTER_REQUOTE_HOLD_QUEUE on vs off)

## Goal
Conduct a controlled, signer-free shadow rehearsal evaluating the `HUNTER_REQUOTE_HOLD_QUEUE` lever (treatment at 200 shares vs control at 0 shares) against the live Polymarket order book using a frozen universe snapshot and scratch databases. Surface order lifetime, cancel-reason mix (specifically `price_moved` share), median queue multiple, and fill rates using the #359 reporting extensions. Produce an evidence-backed run memorandum in `docs/runs/` with pre-registered decision criteria, without modifying any shipped code defaults.

## Acceptance Criteria
- [ ] Run two paired, signer-free shadow rehearsal arms against scratch databases:
  - Treatment: `HUNTER_REQUOTE_HOLD_QUEUE=200`
  - Control: `HUNTER_REQUOTE_HOLD_QUEUE=0`
- [ ] Both arms rotate against the same candidate market universe via a frozen snapshot of `runtime/markets.json` passed to `--markets-path`.
- [ ] Monitor both runs and record reached quote counts, distinct orders, and timestamps.
- [ ] Extract per-arm metrics using `core_brain.statistics_report::write_statistics_report` (and KPI):
  - Order lifetime: median seconds for terminal cancelled and filled orders (separate from open/censored).
  - Cancel mix: counts and percentage breakdown of cancellation reasons (including `price_moved` share).
  - Queue depth: median and max queue multiple.
  - Fill rate: share-weighted fill rate and queue-bucket fill rates.
- [ ] Write a dated run document `docs/runs/2026-10-04-queue-hold-200-rehearsal.md` following the repo's trial memo standard (e.g., `2026-09-30-run08-aged-out-rescue.md`) containing:
  - Pre-registered decision rule (Adopt / Reject / Inconclusive).
  - Side-by-side comparison table across all core metrics.
  - Comparability audit (market counts, median queue ahead).
  - Explicit verdict and recommendation on whether to change default `requote_hold_queue_shares`.
- [ ] Confirm no shipped defaults in `core_brain/config.py` or business logic are altered by this PR.
- [ ] Ensure targeted test suites pass: `python -m pytest -q tests/test_trader_loop.py tests/test_completable_pair_gate.py tests/test_statistics_report.py`.

## Scope
### In scope
- Paired shadow run execution using `--markets-path` and per-run scratch databases.
- Metrics generation via `core_brain.statistics_report` and `core_brain.kpi`.
- Run memorandum in `docs/runs/2026-10-04-queue-hold-200-rehearsal.md`.

### Out of scope
- Modifying shipped defaults in `core_brain/config.py` (e.g. `requote_hold_queue_shares = 0.0` remains unchanged).
- Requote dead band lever changes (`HUNTER_REQUOTE_DEAD_BAND` / Issue #361).
- Modifying `data/orders.db` (production database strictly read-only).
- Placing real orders or loading private keys / credentials.
