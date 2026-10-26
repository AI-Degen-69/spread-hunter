# Implementation Plan — Issue #839: Live migration approval audit and deployment verdict

Branch: `i839/live-migration-approval-audit` | Issue: #839

> Supersedes the #850 plan this file previously carried (that work merged in PR #851; its
> plan, spec and constraints remain in git history at `fb2949aa`).

## Summary

Produce one audit — `docs/audits/live-migration-799.md` — that decides whether any arm of
Tournament #799 is approved for live real-money execution, using the tournament's own stores
and the repository's own gate machinery, and driven by a single repeatable command.

Right-sizing: **Standard** (three files: one CLI, its test, one audit doc; one decision — that
the verdict comes from the repo's gate, not from the issue's draft). Task type: **Docs**
(primary) + **Research** (evidence gathering), with a small **Code** seam.

The plan is re-grounded because the issue was written mid-run. Measured, the issue's two
load-bearing premises do not hold: `balanced-mid` is **not** the top-PnL arm (`loose-baseline`
leads, +$23.97 vs +$23.46 — a gap this sample cannot resolve), and it is **not** the
risk-shape leader (it fails all three pre-registered gates; `queue-clear-5m` is the only arm
that clears them). The repo's own gate returns **NO-GO for every arm measured so far**,
because the primary gate is the 90% CI lower bound on inclusive mean return ≥ 1.0% — not the
60-close count the issue treats as the cleared gate. `core_brain/config.py:268`
(`bot_eval_stat_gate_target_closes = 60`) is a precondition for a verdict;
`core_brain/config.py:266` (`bot_eval_stat_gate_threshold_pct = 1.0`) is the gate.

## Code-review intake (CodeRabbit plan comment on #839)

One reading. **Adopted:** the audit lives in `docs/audits/`; default verdict "not approved —
inconclusive" until the operator decides; payload documented in two layers (measured arm vs
untested synthesis); no live-capital command; no cockpit or risk-engine change; a strict-loader
test for the payload; tie GO to the real gate. **Rejected:** its Phase 1 file
(`tests/test_prudent_hybrid_payload.py`) as the prerequisite — the payload under audit is no
longer the prudent-hybrid synthesis, because the arm the measured record supports runs no
preset at all (only the two queue-gate keys), so a nine-value payload test would prove the
wrong configuration. Also rejected: its two factual premises — "no run folder exists in the
checkout" (the run finished 2026-10-26 ~22:01 and its stores are readable) and "the ticket's
own 33 closes" (the stores hold 87–172 closes per arm). **[UNVERIFIED] resolved:** the config
field names it flagged for the risk ratios and pair cap (it cited `core_brain/config.py`
lines 43–56, 188–192, 442–455 and 741–793) — not needed: this audit cites only constants it
read directly (`:266`, `:268`, `:574`/`:582`).

## Line numbers the issue cites, verified against the live code

| Cited | Actual |
|---|---|
| `core_brain/config.py` lines 564–570 (`TOURNAMENT_PRESETS["balanced"]`) | dict at `:574`, `"balanced"` at `:582` |
| `core_brain/config.py:1060` (from #471, `stat_gate_target_closes`) | `:268` (`bot_eval_stat_gate_target_closes`) |
| `configs/tournaments/i799-tournament-test_arms.json` lines 22–32 (`balanced-mid`) | `"name": "balanced-mid"` at `:33` |
| `scripts/tournament_cockpit_server.py` lines ~1856–2180 (blueprint tab) | `id="tab-hybrid-config"` at `:2293`; `.env` block `:2453`; champion synthesis `:2612` |
| `reports/tournament_cockpit.html` lines ~420–550 | generated artifact; `reports/` is gitignored and absent in a clean checkout — never a source seam |

## Resolved open questions (from code + measurements, not from opinion)

1. **Which gate is the GO?** `statistical_validation_run/artifacts.py:257 resolve_verdict`:
   underpowered (< 60 closes) → `INCONCLUSIVE`; primary gate strictly above
   `bot_eval_stat_gate_threshold_pct` → `GO`; else `NO-GO`. `#471` names the same gate,
   inclusive of every close method.
2. **Which arm is the champion?** Measured, not asserted — see the appendix. Risk-shape rule:
   gates cleared, then Sharpe, then PnL.
3. **Where is the run?** `data/tournaments/i799-tournament-1m_2026-10-26T00-12` in the operator's
   main checkout, ignored by `.gitignore:60`. Ten arm stores (one per arm), `plan.json`,
   `logs/`, and a launch-time `results.json` that is **not** a result.
4. **Is the run complete?** No: closes span 00:14 → 21:57 on 2026-10-26, 1308.5 measured minutes
   of a 1440-minute plan, and the arms stopped ~22:01. Coverage is reported, never rounded up.

## Interface contracts (frozen before the build)

1. **Measurement CLI** — extends the tool already drafted this session
   (`scripts/reevaluate_tournament_run.py`, currently untracked in this worktree; green tests
   beside it):
   ```
   python -m scripts.reevaluate_tournament_run --run-dir <run dir> [--json] [--verdict]
   ```
   `--verdict` calls `core_brain.statistics_report.write_statistics_report(db, run_id, "shadow")`
   per arm and adds `{"verdict": ..., "primary_gate_value": ...}` to each arm record.
   Read-only: every store opens with `mode=ro`. Exit `1` when no plan record or no arms.
2. **Existing seam, not modified** —
   `core_brain.statistics_report.write_statistics_report(db_path, run_id, mode, out_dir=None) -> dict`
   returning `verdict` ∈ {`GO`, `NO-GO`, `INCONCLUSIVE`} and `gate_rows` (each
   `{gate, value, passed}`), with the primary row named `Primary — total PnL (inclusive)`.
3. **Audit doc** — `docs/audits/live-migration-799.md`, shaped like
   `docs/audits/edge-floor-decision.md`: verdict up front, "What was asked", "Where the data
   came from (and what it is not)", the measured tables, the discrepancy table, the locked
   rule, reopen conditions, and a "How to verify" block written for the operator.
4. **Files that must not change**: `scripts/tournament_cockpit_server.py` (its blueprint pane is
   pinned by `tests/test_tournament_cockpit_arms.py`, `CHAMPION_ENV` — nine values asserted
   across three places), `configs/tournaments/i799-tournament-test_arms.json`,
   `core_brain/config.py`.

## Dependency graph

```
T1 (measurement + verdict CLI)  ──►  T2 (audit doc)
                                        │
                                        └──►  T3 (backlog cross-reference)
```

## Tasks

### T1 — One command that reads the run and reports each arm's shape and the repo's verdict
- **Size:** M  **Domain:** `[Backend/Logic]`  **Depends on:** —
- **Files:** `scripts/reevaluate_tournament_run.py` (bring onto this branch, add `--verdict`),
  `tests/test_reevaluate_tournament_run.py` (already written; extend for the verdict wiring).
- **What it does:** loads a run's `plan.json`, calls
  `scripts/tournament_cockpit_server.analyze_arm_database(cfg, target_minutes)` per arm for
  shape (closes, merges, rescue exits, merge rate, PnL, σ, Sharpe proxy, profit factor, max
  drawdown, VaR95, MC P5, score), computes the required N for a $0.02 effect with
  `core_brain.kpi.required_sample_size`, evaluates the three pre-registered risk-shape gates
  and the power flag, ranks by (gates cleared, Sharpe, PnL), and — with `--verdict` — attaches
  the repo's verdict per arm. Text and `--json` output.
- **Verification:** `python -m pytest -q tests/test_reevaluate_tournament_run.py` — a synthetic
  two-arm run directory where only the gated arm clears the risk-shape gates, the settlement
  counting convention is pinned, an unpowered arm is flagged, a missing plan exits `1`, and the
  `--verdict` path is asserted against a stubbed `write_statistics_report`. Plus the live read
  of the real run folder as the acceptance evidence.

### T2 — The audit document and its verdict
- **Size:** M  **Domain:** `[Docs]` + `[Research]`  **Depends on:** T1
- **Files:** `docs/audits/live-migration-799.md` (new).
- **What it does:** records the run's identity and coverage; the ten-arm measured table with the
  command behind it; the issue-claim vs measured discrepancy table with the interim-snapshot
  explanation; the risk-shape ruling that makes `queue-clear-5m` the candidate and the
  half-split evidence (first half +$0.2356/trade Sharpe 5.94 → second half +$0.1588 Sharpe
  4.85, against `balanced-mid` +$0.1993 Sharpe 4.43 → **−$0.0087** Sharpe −0.16) plus
  concentration (37 markets, largest 15.3% of PnL, vs 74.5% for `prudent-hybrid`); the queue
  mechanism evidence (`python -m scripts.queue_report --db <queue-clear-5m>.db`: 15,884 deltas,
  182 levels, 45 markets, 89% of queue movement is cancels, most trade-based clear times
  infinite — the books the gate refuses); the per-arm verdicts; and the reopen conditions.
- **Verification:** every table's command is re-run and its output matches the doc; the two
  named suites stay green. The audit is read by the operator, so the "How to verify" block
  points at the run table, the cockpit, and the report file — not at the test suite.

### T3 — Cross-reference the two backlog rows this run closes out
- **Size:** XS  **Domain:** `[Docs]`  **Depends on:** T2
- **Files:** `docs/backlog/i837-cockpit-catalyst-columns.md` (row N4),
  `docs/backlog/i838-cockpit-hybrid-blueprint.md` (row N3).
- **What it does:** both rows say the #799 figures "cannot be recomputed from this worktree" and
  are owed "after the run's 24 h window closes". The window has closed. Each row gains a
  pointer to the audit, the measured figures, and the note that the 6.4 h snapshot values in
  `configs/tournaments/i799-tournament-test_arms.json` are superseded. Ownership of those
  issues does not change.
- **Verification:** markdown-only; the two rows read correctly and name the audit path. Drop
  this task without a ripple if the operator prefers the backlog untouched.

## Checkpoints

- **After T1:** the tool prints the ten-arm table for the real run folder and its tests pass —
  show the table.
- **After T2:** the audit is on disk with its verdict; show the verdict line and the reopen
  conditions.
- **After T3:** plan complete, targeted suites green, branch ready for Station IV.

## Sub-issues

Deliberately **not** created, recorded here rather than silently skipped: this is a
single-session, three-task change (two of them documentation) landing as one PR, so three
tracker sub-issues with `blocked-by` edges would mirror a plan that never spans sessions. If
the operator wants tracker mirroring anyway, create them from T1–T3 in one batch.

## One improvement proposal (adopted by default)

**Evidence, verbatim from the issue:** *"Balanced Mid strikes the empirical sweet spot… This
configuration maximizes timely two-sided fills while preventing catastrophic adverse single-leg
runs"*, and acceptance criterion 1: *"Audit report documents Balanced Mid's superiority across
PnL (+$28.37), Sharpe (2.72), Profit Factor (2.85), and Drawdown ($3.37) against all 9 competing
arms."* The stores hold `+$23.46 / 1.66 / 1.80 / $9.64` for that arm, so the criterion as
written can only be satisfied by restating a claim the data contradicts.

**Proposal (simplification, adopted):** make the audit's verdict **and** its candidate follow
the pre-registered ranking (gates → Sharpe → PnL) and the repo's own gate, so the doc cannot be
written backwards from a named winner. The audit still reports `balanced-mid` in full — it
simply stops being the conclusion.

**Rejected proposal (recorded so it does not resurface):** relabelling the cockpit's
"Live Production" blueprint and its `READY FOR LIVE` badge in this issue. It is a separate code
change with its own pinned tests; the audit records it as a follow-up instead. (This matches
CodeRabbit's Design Choice 4.)

## Appendix — measured record (commands behind it, run 2026-10-26 ~23:5x)

`python -m scripts.reevaluate_tournament_run --run-dir data/tournaments/i799-tournament-1m_2026-10-26T00-12`
plus `write_statistics_report(<arm>.db, <run_id>, "shadow")` for the arms marked with a verdict.
"Closes" follows the cockpit's convention (every non-bookkeeping close, `shadow_settlement`
included). Nreq = closes required for a $0.02 effect at that arm's σ.

| Arm | Env keys | Closes | Merges | Rescue exits | Merge % | PnL | Sharpe | PF | MDD | σ | Nreq | Gates | Repo verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| loose-baseline | (shipped) | 171 | 99 | 66 | 57.9 | +$23.97 | 1.59 | 1.74 | $7.51 | 0.880 | 11,977 | 0/3 | NO-GO (−4.219%) |
| tight-touch | `preset=aggressive` | 144 | 81 | 57 | 56.2 | +$16.95 | 1.24 | 1.56 | $9.25 | 0.948 | 13,898 | 0/3 | — |
| balanced-mid | `preset=balanced` | 166 | 99 | 63 | 59.6 | +$23.46 | 1.66 | 1.80 | $9.64 | 0.849 | 11,148 | 0/3 | NO-GO (−4.740%) |
| wide-shield | `preset=conservative` + 8¢ stop | 151 | 68 | 78 | 45.0 | +$6.27 | 0.44 | 1.15 | $11.87 | 0.947 | 13,860 | 0/3 | — |
| prudent-hybrid | `preset=prudent` + max 6¢ | 170 | 98 | 64 | 57.6 | +$19.66 | 1.49 | 1.60 | $11.29 | 0.774 | 9,261 | 0/3 | NO-GO (−5.318%) |
| static-floor-2c | dynamic offset off | 169 | 100 | 66 | 59.2 | +$17.69 | 1.25 | 1.57 | $7.33 | 0.835 | 10,778 | 0/3 | — |
| low-churn-deadband | offset off + 0.10 deadband | 172 | 99 | 68 | 57.6 | +$10.33 | 0.92 | 1.30 | $10.77 | 0.652 | 6,566 | 0/3 | — |
| hair-trigger-2c | 2¢ absolute stop | 141 | 58 | 81 | 41.1 | +$8.51 | 1.07 | 1.33 | $9.29 | 0.562 | 4,885 | 0/3 | — |
| patient-stop-9c | 9¢ absolute stop | 129 | 63 | 59 | 48.8 | +$5.73 | 0.46 | 1.17 | $11.09 | 0.969 | 14,521 | 0/3 | — |
| **queue-clear-5m** | **gate on, 5 min** | **87** | **56** | **29** | **64.4** | **+$17.74** | **4.74** | **3.28** | **$2.60** | **0.431** | **2,865** | **3/3** | **NO-GO (−2.501%)** |

Gates = pre-registered Sharpe ≥ 2.70 / PF ≥ 2.50 / MDD < $4.00. No arm clears the power gate:
`queue-clear-5m` would need 2,865 closes and has 87 (3% of the required sample), so nominal PnL
is not a winner signal here and the ranking is risk shape.

Report files produced by the verdict run land in the audit's evidence folder, never in the
repository's `reports/` (gitignored) and never committed.
