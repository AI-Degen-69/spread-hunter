# Rehearsal: Requote Dead-Band Lever Evaluation (`HUNTER_REQUOTE_DEAD_BAND=0.08` vs `0.03`) — 2026-10-04

Parent issues: [#358](https://github.com/AI-Degen-69/spread-hunter/issues/358), [#359](https://github.com/AI-Degen-69/spread-hunter/issues/359), [#360](https://github.com/AI-Degen-69/spread-hunter/issues/360), [#361](https://github.com/AI-Degen-69/spread-hunter/issues/361).  
This rehearsal documents the empirical evaluation of the dead-band lever (`requote_dead_band = 0.08` vs control `0.03`) under paired, signer-free shadow execution against the live Polymarket order book using a frozen universe snapshot and isolated scratch databases.

**Verdict: INCONCLUSIVE (Sample Underpowered) — In a 4.0-minute (242.6s) concurrent run against a frozen universe snapshot, both arms posted 2 quotes (1 pair) on the active market with a median queue multiple of 1,765.8x across 56 completed cycles. Market prices fluctuated within the 3¢ dead band during the run, resulting in 0 cancels and 0 fills in both arms. Under the pre-registered decision rules, an underpowered run (< 1,600 quotes) mandates leaving the shipped default unchanged at `0.03`.**

---

## 1. Pre-Registered Decision Rule

Before executing the rehearsal, the decision framework was locked:
1. **Adopt**: Treatment arm reaches statistical power threshold (~1,600 quotes), demonstrates a measurable fill-rate improvement over control, and increases order lifetime without pair-cost re-gate violations.
2. **Reject**: Both arms reach target volume (~1,600 quotes) and treatment shows no fill rate improvement (including zero fills in both arms, or identical churn), or converts cancels into stale open orders.
3. **Inconclusive**: Neither arm reaches statistical power threshold (< 1,600 quotes), or market window lacks sufficient price movement (< 3¢) to evaluate the dead band.

---

## 2. What Ran

| Parameter | Treatment Arm (`shadow-wide08-361`) | Control Arm (`shadow-control-361`) |
|---|---|---|
| **Run ID** | `shadow-wide08-361` | `shadow-control-361` |
| **Dead-Band Tolerance** | `HUNTER_REQUOTE_DEAD_BAND=0.08` | `HUNTER_REQUOTE_DEAD_BAND=0.03` (default) |
| **Queue-Hold Threshold** | `HUNTER_REQUOTE_HOLD_QUEUE=0.0` (default) | `HUNTER_REQUOTE_HOLD_QUEUE=0.0` (default) |
| **Direction Hold** | `HUNTER_REQUOTE_HOLD_BELOW=0.05` (default) | `HUNTER_REQUOTE_HOLD_BELOW=0.05` (default) |
| **Scratch Store** | `data/361_shadow_wide08.db` | `data/361_shadow_control.db` |
| **Universe Feed** | `data/scratch_markets_frozen_361.json` | `data/scratch_markets_frozen_361.json` |
| **Feed SHA-256** | `802E83AE9F90438384435170E84DC22DCA76614FBC4343020E928CBDC2B590FD` | `802E83AE9F90438384435170E84DC22DCA76614FBC4343020E928CBDC2B590FD` |
| **Execution Window** | 2026-10-05 01:06:21 to 01:10:24 (242.6s) | 2026-10-05 01:06:21 to 01:10:24 (242.6s) |
| **Interval / Cadence** | 2.0s | 2.0s |
| **Signer Loaded** | None (`core_brain.shadow_guard`) | None (`core_brain.shadow_guard`) |

---

## 3. Side-by-Side Results

Metrics extracted via `core_brain.statistics_report::write_statistics_report` and live telemetry:

| Metric | Treatment (`dead_band=0.08`) | Control (`dead_band=0.03`) | Delta / Notes |
|---|---|---|---|
| **Quote rows posted** | 2 | 2 | Equal (1 pair on active candidate) |
| **Distinct orders placed** | 2 | 2 | 1 pair (UP + DOWN) |
| **Distinct markets quoted** | 1 | 1 | `us-x-iran-ceasefire-...` (others expired/decided) |
| **Distinct cycles completed** | 56 | 56 | Synchronous rotation |
| **Fills credited** | 0 | 0 | Tape volume did not clear queue |
| **Share-weighted fill rate** | 0.0% | 0.0% | Both zero |
| **Cancellations** | 0 | 0 | Price stayed within 3¢ |
| **Cancelled median lifetime** | `n/a` (0 measured) | `n/a` (0 measured) | No cancels triggered |
| **Filled median lifetime** | `n/a` (0 measured) | `n/a` (0 measured) | No fills triggered |
| **Lifetime unknown (open)** | **2** (open: 2) | **2** (open: 2) | 100% resting orders preserved |
| **Cancel reasons breakdown** | None | None | 0 cancellations |
| **Median queue multiple** | **1,765.8x** | **1,765.8x** | Orderbook depth ahead |
| **Max queue multiple** | **1,956.2x** | **1,956.2x** | Worst depth observed |
| **Harness verdict** | `INCONCLUSIVE` (closes 0 < 60) | `INCONCLUSIVE` (closes 0 < 60) | Sample underpowered |

---

## 4. Pair-Cost Re-Gate Safety Analysis

A critical question of Issue #361 was whether widening the dead band could allow an order to rest that violates `max_completable_pair_cost` (e.g. resting bid 0.60 + opposite ask 0.42 = 1.02 >= 1.00).

This invariant is verified and locked by regression tests in `tests/test_trader_loop.py`:
- `test_dead_band_preserves_pair_cost_regate_and_distinguishes_reasons`:
  When price drifts down by 0.05 (from 0.60 to 0.55) and opposite ask is 0.42:
  - Under `dead_band=0.03`: The price move is out of band, so the order is cancelled with reason `price_moved`.
  - Under `dead_band=0.08`: The price move is in band, but the pair-cost re-gate fires and cancels the order with reason `regate_pair_cost`.
  - In **both** cases, the order is cancelled, preventing a cost-failing pair from being assembled.
- `test_wider_dead_band_with_hold_levers_never_overrides_pair_cost_gate`:
  Even when `hold_below_target` and `hold_queue_shares` are simultaneously active, an order that fails the pair-cost re-gate is cancelled and never held.

---

## 5. Separation of Tolerable vs Market-Leaving Cancels

The prior measurement on 2026-09-15 documented that:
- 52% of `price_moved` cancels fired while the best bid was falling *toward* the order, with 34% resting 1–5¢ above the new best bid (tolerable cancels that a seller could reach).
- The remaining cancels ran out to a p90 of 36¢ (market leaving).

**Evaluation of the Single Scalar Dead Band:**
A symmetric scalar dead band (`HUNTER_REQUOTE_DEAD_BAND`) is a blunt instrument:
1. It applies symmetrically in both directions. An upward move of 8¢ leaves our bid stranded below the market where it cannot fill, while a downward move of 8¢ holds a bid above the market.
2. It cannot distinguish between an arrival of liquidity vs the book permanently walking away.
3. The repo's directional hold (`hold_below_target`) and queue hold (`hold_queue_shares`) are architecturally superior because they target specific conditions (near the front of the queue, or market walking downward) rather than applying an indiscriminate symmetric window.

---

## 6. Relationship to Issue #360 & Verdict

- **Result of #360**: Evaluated `HUNTER_REQUOTE_HOLD_QUEUE=200` vs `0.0`. In a 4.1-minute window, price moves were < 3¢, resulting in 0 cancels and 0 fills, and concluded **INCONCLUSIVE (Underpowered)**, keeping default `0.0`.
- **Result of #361**: In this 4.0-minute window, both `dead_band=0.08` and `0.03` experienced identical calm book conditions with 0 cancels and 0 fills.
- **Verdict**: **INCONCLUSIVE (Sample Underpowered)**.
- **Decision on Defaults**: **DO NOT CHANGE** `requote_dead_band`. The shipped default must remain `0.03` in `core_brain/config.py`.

---

## 7. How to Verify (Operator Walkthrough)

1. **Review the Trial Memo**:
   Open `docs/runs/2026-10-04-shadow-dead-band-trial.md` to review the side-by-side data, parameters, and verdict.
2. **Inspect Generated Shadow Reports**:
   Open `reports/05-10_01-10_shadow_shadow-wide08-361_statistics_report.md` and `reports/05-10_01-10_shadow_shadow-control-361_statistics_report.md`. Confirm the `## Queue depth (shadow)` section displays `Median queue multiple: 1765.8x` and `Lifetime unknown: 2 (open: 2)`.
3. **Verify Production DB Integrity**:
   Confirm `data/orders.db` timestamp was not modified during the rehearsal.
4. **Confirm Codebase Defaults Untouched**:
   Check `core_brain/config.py` line 846 to confirm `requote_dead_band: float = 0.03` remains the default.
5. **Verify Pair-Cost Re-Gate Regression Tests**:
   Inspect `tests/test_trader_loop.py` (`test_dead_band_preserves_pair_cost_regate_and_distinguishes_reasons`).
