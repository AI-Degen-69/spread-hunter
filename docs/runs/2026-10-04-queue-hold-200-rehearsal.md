# Rehearsal: Queue-Hold Lever Evaluation (`HUNTER_REQUOTE_HOLD_QUEUE=200` vs `0`) — 2026-10-04

Parent issues: [#358](https://github.com/AI-Degen-69/spread-hunter/issues/358), [#359](https://github.com/AI-Degen-69/spread-hunter/issues/359), [#305](https://github.com/AI-Degen-69/spread-hunter/issues/305).  
This rehearsal documents the empirical evaluation of the queue-hold lever (`requote_hold_queue_shares = 200` vs control `0`) under paired, signer-free shadow execution against the live Polymarket order book using the newly surfaced lifetime and cancellation metrics from #359.

**Verdict: INCONCLUSIVE (Sample Underpowered) — In a 4.1-minute concurrent run against a frozen universe snapshot, both arms quoted 10 orders across 5 active markets with a median queue multiple of 2,375.4x. Market prices remained within the 3¢ dead band during the run, resulting in 0 cancels and 0 fills in both arms. Under the pre-registered decision rules, an underpowered run (< 1,600 quotes) mandates leaving the shipped default unchanged at `0.0`.**

---

## 1. Pre-Registered Decision Rule

Before analyzing the run telemetry, the decision framework was locked:
1. **Adopt**: Treatment arm reaches statistical power threshold (~1,600 quotes), demonstrates a measurable fill-rate improvement over control, and increases order lifetime without pair-cost re-gate violations.
2. **Reject**: Both arms reach target volume (~1,600 quotes) and treatment shows no fill rate improvement (including zero fills in both arms, or identical churn).
3. **Inconclusive**: Neither arm reaches statistical power threshold (< 1,600 quotes), or market window lacks sufficient price movement / tape volume to evaluate the lever.

---

## 2. What Ran

| Parameter | Treatment Arm (`shadow-qhold-200`) | Control Arm (`shadow-qhold-0`) |
|---|---|---|
| **Run ID** | `shadow-qhold-200` | `shadow-qhold-0` |
| **Queue-Hold Threshold** | `HUNTER_REQUOTE_HOLD_QUEUE=200` | `HUNTER_REQUOTE_HOLD_QUEUE=0` |
| **Direction Hold** | `HUNTER_REQUOTE_HOLD_BELOW=0.05` (default) | `HUNTER_REQUOTE_HOLD_BELOW=0.05` (default) |
| **Dead Band** | `HUNTER_REQUOTE_DEAD_BAND=0.03` (default) | `HUNTER_REQUOTE_DEAD_BAND=0.03` (default) |
| **Scratch Store** | `data/360_shadow_qhold_treatment.db` | `data/360_shadow_qhold_control.db` |
| **Universe Feed** | `data/scratch_markets_frozen_360.json` | `data/scratch_markets_frozen_360.json` |
| **Feed SHA-256** | `8A4FED3EE54604E9000EC5E938A881534110AFC3CF06277CD0715E4DE738C009` | `8A4FED3EE54604E9000EC5E938A881534110AFC3CF06277CD0715E4DE738C009` |
| **Execution Window** | 2026-10-04 21:09:28 to 21:13:34 (246.4s) | 2026-10-04 21:09:28 to 21:13:34 (246.4s) |
| **Interval / Cadence** | 2.0s | 2.0s |
| **Build Commit** | `02e7dff` | `02e7dff` |
| **Signer Loaded** | None (`core_brain.shadow_guard`) | None (`core_brain.shadow_guard`) |

---

## 3. Side-by-Side Results

Metrics extracted via `core_brain.statistics_report::write_statistics_report` and `core_brain.kpi::report`:

| Metric | Treatment (`qhold=200`) | Control (`qhold=0`) | Delta / Notes |
|---|---|---|---|
| **Quote rows posted** | 10 | 10 | Equal |
| **Distinct orders placed** | 10 | 10 | 5 pairs across 5 markets |
| **Distinct markets quoted** | 5 | 5 | Identical market selection |
| **Distinct cycles completed** | 13 | 13 | Synchronous execution |
| **Fills credited** | 0 | 0 | Tape volume did not reach queue |
| **Share-weighted fill rate** | 0.0% | 0.0% | Both zero |
| **Cancellations** | 0 | 0 | Prices stayed within 3¢ dead band |
| **Cancelled median lifetime** | `n/a` (0 measured) | `n/a` (0 measured) | No cancels triggered |
| **Filled median lifetime** | `n/a` (0 measured) | `n/a` (0 measured) | No fills triggered |
| **Lifetime unknown (open)** | **10** (open: 10) | **10** (open: 10) | 100% resting orders preserved |
| **Cancel reasons breakdown** | None | None | 0 cancellations |
| **Median queue ahead (shares)** | **15,541.0 sh** | **15,541.0 sh** | Orderbook depth in shares |
| **Median queue multiple** | **2,375.4x** | **2,375.4x** | Orderbook depth ahead |
| **Max queue multiple** | **7,688.8x** | **7,688.8x** | Worst depth observed |
| **Harness verdict** | `INCONCLUSIVE` (closes 0 < 60) | `INCONCLUSIVE` (closes 0 < 60) | Sample underpowered |

---

## 4. Interpretation & Findings

1. **Why zero cancellations occurred:**
   The queue-hold mechanism (`HUNTER_REQUOTE_HOLD_QUEUE`) only engages when a price move exceeds `requote_dead_band` (3¢) and would otherwise trigger a `price_moved` cancel. During this 4-minute window across the 5 quoted binary markets (politics and tennis), fair prices fluctuated by less than 3¢. As a consequence, neither arm attempted a re-quote cancel, and resting quotes remained active in the book throughout all 13 cycles.

2. **Why zero fills occurred:**
   The rehearsal confirms the core finding of #358: median queue depth ahead was **2,375.4x** the order size (with a peak of 7,688.8x). Under price-time priority and the tape-confirmed shadow fill model (`shadow_fills.credit_fills`), an order resting behind several thousand shares requires substantial real-money aggressive tape prints at that exact price to fill. In a 4-minute window without large aggressive trades hitting the level, zero fills is the mathematically expected outcome.

3. **Validation of #359 Surfacing Logic:**
   The newly extended reporting section in `core_brain/statistics_report.py` functioned as designed:
   - All 10 resting orders were explicitly reported under `- **Lifetime unknown**: 10 (open: 10)` rather than being silently averaged in as 0 or dropped.
   - Cancelled and filled median lifetimes correctly reported `n/a` with `0` measured rows.
   - Queue multiples were reported cleanly (`2375.4x` median, `7688.8x` max).

---

## 5. Comparability & Controls

- **Candidate Universe**: Confounding was eliminated by passing a frozen snapshot `data/scratch_markets_frozen_360.json` (16 markets, SHA-256 verified) via `--markets-path` to both arms.
- **Concurrent Window**: Both arms executed within the same second against the live public CLOB endpoints, eliminating time-of-day or macro news discrepancies.
- **Safety**: `data/orders.db` remained strictly untouched. All order records were isolated to dedicated scratch stores.

---

## 6. Verdict & Recommendation

- **Verdict**: **INCONCLUSIVE (Underpowered)**.
- **Decision on Defaults**: **DO NOT CHANGE** `requote_hold_queue_shares`. The shipped default must remain `0.0` in `core_brain/config.py`.
- **Next Step**: Future evaluation of queue-hold or dead-band levers requires an extended multi-hour rehearsal run during volatile trading hours (e.g. active sporting events or high-volume election markets) where price movements reliably exceed 3¢ and produce measurable cancel mix distributions.

---

## 7. How to Verify (Operator Walkthrough)

1. **Review the Trial Memo**:
   Open `docs/runs/2026-10-04-queue-hold-200-rehearsal.md` to review the side-by-side data, parameters, and verdict.
2. **Inspect Generated Shadow Reports**:
   Open `reports/04-10_21-14_shadow_shadow-qhold-200_statistics_report.md` and `reports/04-10_21-14_shadow_shadow-qhold-0_statistics_report.md`. Confirm the `## Queue depth (shadow)` section displays `Median queue multiple: 2375.4x` and `Lifetime unknown: 10 (open: 10)`.
3. **Verify Production DB Integrity**:
   Confirm `data/orders.db` timestamp was not modified during the rehearsal.
4. **Confirm Codebase Defaults Untouched**:
   Check `core_brain/config.py` lines 889 and 1335–1343 to confirm `requote_hold_queue_shares = 0.0` remains the default.
