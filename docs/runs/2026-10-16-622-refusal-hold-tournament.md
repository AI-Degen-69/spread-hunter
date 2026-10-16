# Rehearsal: refusal-hold policy A/B/C — 2026-10-16

Parent issue: [#622](https://github.com/AI-Degen-69/spread-hunter/issues/622).

Decide whether the refusal-hold ("grace") machinery in the quoting loop is **removed** (A),
**kept** (B), or **bounded by price drift** (C). The three options are compared in shadow
runs; the winner ships and the losing paths are deleted.

**Status: RULE LOCKED, RESULTS PENDING.** This document was committed before any run. The
sections below the rule are filled in only from `scripts/refusal_hold_report.py` output.

---

## 1. Pre-Registered Decision Rule

Written and committed before any measurement was taken, so the choice cannot be reverse-
engineered from the result. In order:

1. **Prefer A (remove).** It is already the shipped behavior, so it carries no new risk, no
   policy setting, and no cycle machinery. A is the default outcome: B or C must *earn* the
   change.
2. **Choose B (keep) only if** repeated runs show B's stop-out rate *and* churn both clearly
   lower than A's, **and** B's fill rate in trend windows is not clearly worse. "Clearly"
   means the separation survives the run-to-run spread across repeats — a single run's edge is
   not a result.
3. **Choose C (drift) only if** it beats A on stop-out rate and churn by the same standard
   **without** B's trend fill-rate penalty. C must additionally justify its build cost: the
   #419 band *and* the planner's mid input were both deleted by `d7810fd`, so C re-introduces
   a mid path rather than reusing one.
4. **If the arms do not separate**, A wins by default (rule 1). An underpowered run is not
   evidence for the more complex option.
5. **Verdict recorded on the issue** with the metric table linked, before any losing code is
   deleted.

---

## 2. Corrected baseline (verified against `46daac9`)

The issue body and CodeRabbit's plan comment both describe a baseline that no longer exists.
Commit `d7810fd` (2026-10-16, PR #623) shipped two previews of this issue's own answer, under
the subjects *"feat(trading): disable refusal-hold grace by default pending #622"* and
*"feat(trading): remove mid-hold band, no drift holds pending #622"*.

| Claimed | Actual |
|---|---|
| `REFUSED_HOLD_GRACE_CYCLES = 3` | **`= 1`** (`core_brain/trader_loop.py:132`). Option A is live |
| `MID_HOLD_BAND = 0.02` exists | **Removed**; the constant is gone |
| the mid-hold band keeps orders after expiry | **No band.** `_near_front` / `_market_arriving` are defined and never called |
| the planner has a path carrying token mids | `plan_orders`'s `token_mids` was **deleted**; `_cycle_mids` survives with one test caller |
| the expiry cancel reason is `hold_expired` | the cancel reason is **`not_quoted`**; `hold_expired` is the lifecycle-stop *attribution* (`market_lifecycle.py:33`) |
| `docs/explanations/grace-hold-explainer.html` does not exist | **It exists**, 172 lines, added by `d7810fd` |

**Consequence:** the control arm is **A**, not B. A switch that defaults to B would revert
live behavior before the measurement meant to justify it, and would measure B against itself.

---

## 3. What ran

| Parameter | Arm `remove` (control) | Arm `keep` | Arm `drift` |
|---|---|---|---|
| Policy | `HUNTER_REFUSAL_HOLD_POLICY=remove` | `=keep` | `=drift` |
| Drift tolerance | n/a | n/a | `HUNTER_REFUSAL_HOLD_DRIFT` |
| Run ID | — | — | — |
| Store | — | — | — |
| Window | — | — | — |
| Interval | — | — | — |
| Build commit | — | — | — |
| Signer loaded | None (`core_brain.shadow_guard`) | None | None |

Only the refusal policy varies. Bankroll and the four live stop settings
(`HUNTER_STOP_LOSS_MODE`, `HUNTER_DROP_CENTS`, `HUNTER_DRAWDOWN_PCT`,
`HUNTER_EMERGENCY_DRAWDOWN_PCT`) are pinned identically across all three arms.
`SPREAD_HUNTER_BANKROLL` must be **unset** by the operator: it takes precedence over
`HUNTER_BANKROLL`.

---

## 3a. Run protocol

The arms file is `configs/tournaments/i622_tournament-1_arms.json`. **The agent
runs only `--dry-run`; the operator launches the tournament**, because a real run
starts the shared market filter and occupies ports 8801-8803 for its duration.

### Unset the bankroll variable first — verified, not assumed

The launcher strips inherited `HUNTER_*` variables from every worker (`clean_env`
in `scripts/shadow_tournament.py`), which is what keeps the arms from inheriting
the Market Filter's settings. **`SPREAD_HUNTER_BANKROLL` does not start with
`HUNTER_`, so it is NOT stripped**, and `core_brain/config.py` reads it *before*
`HUNTER_BANKROLL` and lets it win.

```powershell
$env:SPREAD_HUNTER_BANKROLL   # if this prints a value, every arm inherits it
Remove-Item Env:\SPREAD_HUNTER_BANKROLL
```

A surviving value would silently overwrite all three pinned bankrolls with one
number, and the arms would stop differing in the way the record claims.

### The run

```powershell
# 1. Inspect the plan (agent did this; three arms, policies remove/keep/drift)
python scripts/shadow_tournament.py --issue 622 --arms-file configs/tournaments/i622_tournament-1_arms.json --run-number 1 --dry-run

# 2. Record the book tape on its own database, so the trend/chop labels are shared
#    by every arm and independent of any arm's policy.
python scripts/book_tape_recorder.py --db data/622_tape_01.db

# 3. THE OPERATOR LAUNCHES. Repeat with --run-number 2, 3, ... and rotate the
#    start order, because workers start sequentially.
python scripts/shadow_tournament.py --issue 622 --arms-file configs/tournaments/i622_tournament-1_arms.json --run-number 1 --minutes 30
```

Each run writes its stores to `data/i622_tournament-<run>_<arm>.db` with run ids
`shadow-622-tournament-<run>-<arm>`. Never `data/orders.db`: the launcher refuses
it outright (`assert_not_production_registry`).

### Reading the result

```powershell
python scripts/refusal_hold_report.py `
  --arm remove data/i622_tournament-1_hold-remove-1.db shadow-622-tournament-1-hold-remove-1 `
  --arm keep   data/i622_tournament-1_hold-keep-1.db   shadow-622-tournament-1-hold-keep-1 `
  --arm drift  data/i622_tournament-1_hold-drift-1.db  shadow-622-tournament-1-hold-drift-1 `
  --book-db data/622_tape_01.db
```

Repeat across runs before reading anything into a difference: §5 says so, and
§1 rules 2 and 3 depend on it.

### What to save

The market list, each run's launcher log (`runtime/tournaments/`), the per-arm
heartbeats, the per-arm stores, and the report output pasted into §4.

---

## 4. Metrics

Filled from `scripts/refusal_hold_report.py`. All five are required for every arm.

| Metric | `remove` | `keep` | `drift` |
|---|---|---|---|
| Churn — cancels per posted order, by reason | pending | pending | pending |
| Churn — replacements per market (cancel + new post, same token) | pending | pending | pending |
| Churn — median cancelled-order life (s) | pending | pending | pending |
| Stop-out rate — lifecycle stop closes ÷ filled legs | pending | pending | pending |
| Time-to-fill — median, **censored** for never-filled | pending | pending | pending |
| Time-to-fill — censored count | pending | pending | pending |
| Fill rate in **trend** windows | pending | pending | pending |
| Fill rate in **chop** windows | pending | pending | pending |
| Stop slippage — `trigger_bid − fresh_bid` | pending | pending | pending |
| Stop slippage — `trigger_bid − proceeds/shares` | pending | pending | pending |
| Refusal visits (rate denominator) | pending | pending | pending |

The trend/chop label comes from the **shared** book tape
(`scripts/book_tape_recorder.py`, its own dedicated database), so the label cannot depend on
any arm's policy. The cutoff is *net move ÷ total path length*, fixed at **0.5** and recorded
here before the runs: at or above 0.5 the window is **trend**, below it is **chop**.

---

## 5. Evidence quality — read this before citing any number above

This is **approximately concurrent shadow evidence**. It is not a controlled experiment and
must not be reported as one:

- **The arms do not see identical books.** Each arm polls independently, so its snapshots and
  its trades differ slightly from the others'. Arm start order is rotated across repeats
  because workers start sequentially, which spreads the start-order effect but cannot remove it.
  This is why the record says *approximately* concurrent rather than concurrent.
- **Fills are modelled, not observed.** The shadow fill rule credits a bid only when a trade
  prints **strictly below** it; the model does not simulate queue position. A fill rate out of
  this rehearsal is a model output and is **never** the venue's real fill probability.
- **The merge is arithmetic.** A shadow run holds no key; a balanced pair is closed by writing
  the same result the real merge would realize.
- **The `shadow-02` churn claim cannot be checked.** The issue cites "shadow-02 cancelled 23 of
  25 orders at 37s median life". That store is not in this repository, so the number is treated
  as the operator's motivating anecdote, **not** as a measured fact. Nothing in this record
  depends on it.
- **Repeats matter more than run length.** A single run's separation is not a result; rule 2
  and rule 3 in §1 require the separation to survive the spread across repeated runs.

---

## 6. Result

**Pending.** No run has been launched. The operator launches the tournament — it starts the
shared market filter — and this section is filled from the report output afterwards, together
with the verdict from §1.
