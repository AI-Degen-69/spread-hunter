# Rehearsal: 6-Arm Multi-Strategy Adverse-Selection Defense Tournament — 2026-11-01

Parent issue: [#917](https://github.com/AI-Degen-69/spread-hunter/issues/917).
Predecessor baseline: [#905](https://github.com/AI-Degen-69/spread-hunter/issues/905) (`docs/runs/2026-11-01-905-top5-rematch.md`).

Evaluate five orthogonal structural defenses against live-market adverse selection alongside the `i905` control anchor (`control-baseline`) over a 1440-minute (24-hour) shadow rehearsal with an interim 360-minute (6-hour) checkpoint.

**Status: RULE LOCKED, RESULTS PENDING.** This document was committed before any run was launched. The sections below the rule are filled in only from measured output once execution concludes.

---

## 0. Empirical Baseline Context (`i905-top5-rematch`)

In `i905-top5-rematch`, all 5 passive-maker parameter-sweep arms lost money simultaneously across live sports and esports markets (`nba-*`, `atp-*`, `ucl-*`):

- **Aggregate realized PnL:** `-$58.39` across 5 arms (`100%` of arms negative).
- **Pair completion rate:** `33%–43%` across arms (well below the `~55%` break-even threshold at `$0.02–$0.03` spread capture).
- **Single-leg bail-out loss rate:** `39%–57%` of entries ended in forced single-leg exits (`-$0.50` to `-$2.10` per bail-out vs `+$0.08` to `+$0.35` per completed merge).
- **Diagnosis:** Single-parameter tweaks around passive two-sided quoting cannot survive sub-second score shocks in in-play sports markets where informed takers sweep one side and the opposite quote never fills.

---

## 1. Pre-Registered Decision Rule

Written and committed before any measurement is taken so conclusions cannot be reverse-engineered from post-hoc results.

### 1a. Orthogonal Defense Hypotheses

Each treatment arm tests a distinct mechanism against `control-baseline`:

1. **Universe Selection (`macro-non-sports`):** Excluding in-play and sports/esports markets (`BOT_MARKET_EXCLUDE_SPORTS_ON=1`) removes sub-second score-shock adverse selection and restores pair completion above `65%`.
2. **Immediate Taker Completion (`taker-snapper`):** Crossing the opposing ask immediately when `held_avg + opposing_ask < $0.99` (`BOT_RISK_STRANDED_COMPLETION_ON=1`, `BOT_RISK_STRANDED_MAX_WAIT_SEC=10.0`) locks in positive pair spread before match state shifts, lifting pair completion above `70%`.
3. **Fast Single-Leg Bail-Out (`fast-bailout-60s`):** Compressing the aged-out exit window from `900s` to `60s` (`BOT_RISK_PAIRS_EXIT_WINDOW_SEC=60.0`) and the stranded wait from `300s` to `45s` (`BOT_RISK_STRANDED_MAX_WAIT_SEC=45.0`) caps per-bailout loss before a single-point swing compounds into a multi-tick drawdown.
4. **Wide Pair-Cost Cushion (`wide-cushion-live`):** Requiring at least `$0.04` gross margin per completed pair (`BOT_POS_PRICE_MAX_PAIR_COST_USD=0.96`) on wider books (`BOT_QUOTE_BOOK_MAX_SPREAD_USD=0.12`) generates enough per-merge profit to amortize live bail-out losses.
5. **Queue-Gated Fresh Offset Hybrid (`queue-gated-hybrid`):** Combining an 8-minute queue-clear gate (`BOT_ORDER_GATE_QUEUE_CLEAR_ON=1`, `BOT_ORDER_QUEUE_MAX_CLEAR_MIN=8`), a 90-second exit window (`BOT_RISK_PAIRS_EXIT_WINDOW_SEC=90.0`), and a 30-second volatility offset freshness ceiling (`BOT_QUOTE_SPREAD_OFFSET_MAX_AGE_SEC=30.0`) filters toxic flow while bounding unhedged hold time.

### 1b. Per-Arm Promotion & Sufficiency Gates

An arm is eligible for live promotion candidacy only if it clears all five structural gates over the 1440-minute rehearsal window:

1. **Minimum Sample Size:** `pairs_completed >= 20`
2. **Pair Completion Rate:** `completion_rate_pct >= 55.0`
3. **Bail-Out Loss Rate:** `bail_out_loss_rate_pct <= 25.0`
4. **Net Realized PnL:** `realized_pnl_usd > 0.0` (strictly positive after fees and slippage)
5. **Maximum Drawdown:** `max_drawdown_pct <= 8.0` (`$8.00` on `$100.00` baseline bankroll)

Per-KPI statistical sufficiency at 95% confidence is evaluated independently per arm via `python -m scripts.reevaluate_tournament_run --run-dir <run_dir> --verdict`. Any KPI row that has not yet reached required sample size $N$ at 95% confidence is reported **INCONCLUSIVE on that KPI**.

### 1c. Rehearsal Horizon & Interim Checkpoint

- **Primary Horizon:** `1440` minutes (24 hours) so every arm traverses a full diurnal cycle of both live sports slates and macro/politics order flow.
- **Interim Checkpoint:** `360` minutes (6 hours) read-only inspection to verify feed liveness, per-arm store growth, and early bail-out rate separation without halting healthy arms.

---

## 2. Arms Under Evaluation

Configured in `configs/tournaments/i917-multistrat-defense_arms.json`:

| Arm | Defense Axis | Environment Overrides |
|---|---|---|
| `macro-non-sports` | Universe Exclusion | `BOT_MARKET_EXCLUDE_SPORTS_ON=1`, `BOT_ORDER_GATE_QUEUE_CLEAR_ON=1`, `BOT_ORDER_QUEUE_MAX_CLEAR_MIN=5` |
| `taker-snapper` | Taker Leg Completion | `BOT_RISK_STRANDED_COMPLETION_ON=1`, `BOT_RISK_STRANDED_MAX_WAIT_SEC=10.0`, `BOT_POS_PRICE_MAX_PAIR_COST_USD=0.99` |
| `fast-bailout-60s` | Hold-Time Compression | `BOT_RISK_PAIRS_EXIT_WINDOW_SEC=60.0`, `BOT_RISK_STRANDED_MAX_WAIT_SEC=45.0` |
| `wide-cushion-live` | Margin Cushion | `BOT_POS_PRICE_MAX_PAIR_COST_USD=0.96`, `BOT_QUOTE_BOOK_MAX_SPREAD_USD=0.12` |
| `queue-gated-hybrid` | Queue + Offset Freshness + Hold | `BOT_ORDER_GATE_QUEUE_CLEAR_ON=1`, `BOT_ORDER_QUEUE_MAX_CLEAR_MIN=8`, `BOT_RISK_PAIRS_EXIT_WINDOW_SEC=90.0`, `BOT_QUOTE_SPREAD_OFFSET_MAX_AGE_SEC=30.0` |
| `control-baseline` | Control Anchor (`i905` #1) | `BOT_ORDER_GATE_QUEUE_CLEAR_ON=1`, `BOT_ORDER_QUEUE_MAX_CLEAR_MIN=5` |

*All arms inherit shipped stop-loss and deadband safety rules; no arm sets retired tournament keys or weakens `bot_pos_price_max_pair_cost_usd` above `$0.99`.*

---

## 3. Execution Commands (For Operator Rehearsal)

All 6 arms run headless/heartbeat-only without separate per-arm dashboard ports; the unified dashboard on `:8799` serves the Tournament `#917` cockpit directly as a page at `http://127.0.0.1:8799/cockpit/917`.

Validate the 6-arm tournament plan first (spends nothing, writes nothing):

```powershell
python -m scripts.shadow_tournament --issue 917 --arms-file configs/tournaments/i917-multistrat-defense_arms.json --minutes 1440 --stamp multistrat-defense --dry-run
```

Launch the 6-arm 1440-minute shadow rehearsal:

```powershell
python -m scripts.shadow_tournament --issue 917 --arms-file configs/tournaments/i917-multistrat-defense_arms.json --minutes 1440 --stamp multistrat-defense
```

Inspect per-arm metrics at the 360-minute checkpoint or after completion:

```powershell
python -c "from scripts.tournament_cockpit_server import analyze_arm_database; import json; print(json.dumps(analyze_arm_database({'name': '<arm>', 'db': 'data/tournaments/i917-multistrat-defense_<stamp>/<arm>.db'}, 1440.0), indent=2))"
```

Re-derive the statistical gate verdict per arm (read-only):

```powershell
python -m scripts.reevaluate_tournament_run --run-dir data/tournaments/i917-multistrat-defense_<stamp> --verdict
```

---

## 4. Rehearsal Results (Pending Execution)

*This section will be populated once the rehearsal run completes.*

| Metric | `macro-non-sports` | `taker-snapper` | `fast-bailout-60s` | `wide-cushion-live` | `queue-gated-hybrid` | `control-baseline` |
|---|---|---|---|---|---|---|
| Run Status | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |
| Closed Trades ($N$) | — | — | — | — | — | — |
| Completed Pairs | — | — | — | — | — | — |
| Completion Rate (%) | — | — | — | — | — | — |
| Bail-Out Loss Rate (%) | — | — | — | — | — | — |
| Realized PnL (\$) | — | — | — | — | — | — |
| Sharpe Proxy | — | — | — | — | — | — |
| Profit Factor | — | — | — | — | — | — |
| Max Drawdown (\$) | — | — | — | — | — | — |
| Stat Gate Verdict | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |

---

## 5. Official Findings & Verdict (Pending Execution)

- **360-Minute Checkpoint:** Pending measured data.
- **1440-Minute Final Verdict:** Pending measured data.
- **Champion Recommendation:** Pending measured data.
