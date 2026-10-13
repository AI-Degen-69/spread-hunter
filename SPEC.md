# SPEC — #514: Rescue friction analytics for stop-loss drag and taker costs

Branch: `i514/add-rescue-friction-analytics-for-stop-loss-drag` | Tier: Standard | Type: Code + UX/Copy (readout)

Supersedes the #513 `fill_path_edge` spec in this file's history (kept in git).

## Goals

1. The operator can tell how much of a rescue loss was execution friction rather than planned
   price risk, from recorded closes alone: SLED (stop-loss execution drag), its two hops
   (latency drag, execution drag), TCDR (taker cost drag ratio) and the overshoot ratio.
2. The numbers are pure and deterministic: no network, no signer, no store write, no execution
   path. Same closes in, same payload out; input rows are never mutated.
3. A value that cannot be measured reads `None` with a band of `unmeasured` — never `0`, `NaN`
   or `Infinity`.
4. The metrics reach the surface the run already reads: the `statistical_analytics` payload from
   `build()`, hence `/api/kpi`, the dashboard's Performance & Analytics tab, and the run's
   `report.json`.
5. The dashboard colours each metric's current value by its own band, using the theme's existing
   status palette — the threshold lives in the payload, not in the renderer.

## Acceptance criteria (from the ticket, restated as testable statements)

- [ ] SLED, latency drag, execution drag, TCDR and overshoot ratio computed as pure deterministic
      functions over recorded closes, on the three-price chain
      `trigger_bid` → `fresh_bid` → `dn_price`/`up_price`.
- [ ] SLED is exact and additive (`latency + execution == SLED` on the returned values), and the
      overshoot ratio divides by `planned_risk` behind a guarded zero-division branch.
- [ ] Zero-slippage, adverse-slippage-with-fees, price-improvement and empty/zero-division cases
      covered by unit tests that fail without the change.
- [ ] Each metric's readout display applies the traffic-light band to its current value.
- [ ] No network or signer side-effects; analytics-only change with no LIVE execution path touched.
- [ ] Runnable: `python -m pytest -q tests/test_rescue_friction_analytics.py`

## Edge cases

| Case | Expected |
| --- | --- |
| `trigger_bid` and `fresh_bid` both recorded, exit at the trigger (zero slippage) | timeline 0, execution 0, SLED 0, overshoot 0 |
| Adverse slippage with a large taker fee | both hops measured, SLED = their exact sum, overshoot = SLED / planned risk, TCDR = fee / \|payoff\| |
| Price improvement (exit above the trigger) | negative SLED and hops kept (not clipped), bands read good |
| `trigger_bid` absent, `fresh_bid` present | reference = `entry - 0.04`, source `assumed_offset`; latency `None`; SLED still reported against the assumed reference |
| `fresh_bid` absent | latency `None`, execution `None`, SLED still measured off the reference |
| `planned_risk <= 0` (trigger at or above entry) | overshoot `None` + status, never `ZeroDivisionError` |
| `realized_pnl` 0 or missing | TCDR `None`, never `ZeroDivisionError` |
| `fee` `NULL` | `fee_measured` false, TCDR `None` — "not measured", never 0 |
| No shares, or shares `<= 0` | every per-close metric `None` |
| No rescue closes at all | summary present with zero counts and `None` means, all bands `unmeasured` |
| Merge / venue-sync / other closes in the list | excluded from the summary population, counted as `non_rescue_count` |
| Input given as `CloseRecord` objects instead of dicts | identical output |
| Input rows mutated after the call | impossible — functions copy nothing, read only |

## Explicit out of scope

- Part 1 fill-path expectancy (#513) — merged; its `RESCUE_METHODS`/`_fill_path` classifier is
  reused as-is and not redefined.
- Stop trigger behavior, rotation ordering (#505), quoting, sizing, LIVE execution.
- Fill-model calibration and the validation ladder (#470, #471, #474).
- Telemetry Parts 3–4, and any new design document (ticket says none is required).
- Percent / bps forms of the drag metrics (the ticket shows them only "where shown").
- Recovery of fees from the venue activity feed via `venue_fees.recover_fee`.
