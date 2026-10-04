# SPEC: Issue #361 — Run the dead-band rehearsal (HUNTER_REQUOTE_DEAD_BAND wider vs default)

## Goal
Conduct a paired, signer-free shadow rehearsal evaluating the `HUNTER_REQUOTE_DEAD_BAND` lever (treatment at `0.08` vs control at `0.03`) against the live Polymarket order book using a frozen universe snapshot and isolated scratch databases. Test whether widening the dead band retains reachable orders or simply masks churn / delays cancels into stale orders. Prove via regression testing that the wider band strictly preserves the pair-cost re-gate invariant. Produce an evidence-backed trial memo in `docs/runs/` with an explicit verdict relative to #360, without modifying any shipped code defaults.

## Acceptance Criteria
- [ ] Regression test in `tests/test_trader_loop.py` proves:
  - An order whose resting price + hedge ask fails the pair-cost re-gate is cancelled under both `dead_band=0.03` and `dead_band=0.08`.
  - Under `0.03`, out-of-band price move assigns `cancel_reason = "price_moved"`.
  - Under `0.08`, in-band price move assigns `cancel_reason = "regate_pair_cost"`.
  - Active `hold_below_target` and `hold_queue_shares` never hold a cost-failing order.
- [ ] Two paired, signer-free shadow rehearsal arms executed against scratch databases:
  - Treatment: `HUNTER_REQUOTE_DEAD_BAND=0.08`
  - Control: `HUNTER_REQUOTE_DEAD_BAND=0.03` (or default)
- [ ] Both arms rotate against the same candidate market universe via a frozen snapshot passed to `--markets-path`.
- [ ] Extract per-arm metrics using `core_brain.statistics_report::write_statistics_report` (and KPI):
  - Order lifetime: median seconds for terminal cancelled and filled orders (separate from open/censored).
  - Cancel mix: counts and percentage breakdown of cancellation reasons (including `price_moved` and `regate_pair_cost`).
  - Queue depth: median and max queue multiple.
  - Fill rate: share-weighted fill rate.
  - Open orders: count of orders remaining `open` at shutdown.
- [ ] Author a dated run document `docs/runs/2026-10-04-shadow-dead-band-trial.md` containing:
  - Pre-registered decision rule (Adopt / Reject / Inconclusive).
  - Side-by-side comparison table across all core metrics.
  - Analysis of whether price-moved cancels killed reachable orders vs market-leaving drift, and whether a single scalar dead band can capture this distinction.
  - Pair-cost re-gate safety confirmation citing the regression test.
  - Explicit verdict relative to #360's results.
- [ ] Confirm no shipped defaults in `core_brain/config.py` are altered by this PR (`requote_dead_band = 0.03` remains unchanged).
- [ ] Ensure targeted test suites pass: `python -m pytest -q tests/test_trader_loop.py tests/test_statistics_report.py`.

## Scope
### In scope
- Regression tests for pair-cost re-gate in `tests/test_trader_loop.py`.
- Paired shadow run execution using `--markets-path` and per-run scratch databases.
- Metrics generation via `core_brain.statistics_report`.
- Trial memorandum in `docs/runs/2026-10-04-shadow-dead-band-trial.md`.

### Out of scope
- Modifying shipped defaults in `core_brain/config.py` (e.g. `requote_dead_band = 0.03` remains unchanged).
- Modifying `data/orders.db` (production database strictly read-only).
- Placing real orders or loading private keys / credentials.
- Modifying `plan_orders` logic, `shadow_exec.py`, or live order execution paths.
