# SPEC.md — Issue #839: Live migration approval audit for the #799 champion arm

Branch: `i839/live-migration-approval-audit` | Issue: #839

## Goal

Deliver one official audit — `docs/audits/live-migration-799.md` — that answers a single
operator question: **is any arm of Tournament #799 approved for live real-money
execution?** The answer must be derived from the tournament's own stores with the
repository's own gate machinery, and it must separate three things the issue's draft
verdict runs together: what was measured, what is recommended, and what is authorized.

This spec re-grounds the issue in the measured record, because the issue was written
while the run was still in progress and its figures no longer describe the finished run.

## What the run actually is

| Item | Value |
|---|---|
| Run directory | `data/tournaments/i799-tournament-1m_2026-10-26T00-12` (gitignored — `.gitignore:60` `/data/tournaments/`) |
| Arms | 10, ports 8801–8810, one store per arm (`<arm>.db`) |
| Planned window | 1440 min (`plan.json`) |
| Measured window | 1308.5 min (`scripts/queue_report.py`); closes run 2026-10-26 00:14 → 21:57 |
| Process state | arms stopped 2026-10-26 ~22:01; not running now |
| `results.json` in that folder | a launch-time artifact (all arms `exit_code: 1`, 0 merges) — **not** a result |

## What the issue claims vs what the stores hold

Measured with `python -m scripts.reevaluate_tournament_run --run-dir <run dir>` for shape
and `core_brain.statistics_report.write_statistics_report(db, run_id, "shadow")` for the
verdict. All ten arms are in `tasks/plan.md`; the three the issue names:

| Metric | `balanced-mid` claimed | `balanced-mid` measured | `loose-baseline` claimed | `loose-baseline` measured | `prudent-hybrid` claimed | `prudent-hybrid` measured |
|---|---|---|---|---|---|---|
| Closes | 121 | **166** | 131 | **171** | 125 | **170** |
| Realized PnL | +$28.37 | **+$23.46** | +$27.50 | **+$23.97** | +$24.24 | **+$19.66** |
| Sharpe proxy | 2.72 | **1.66** | 2.22 | **1.59** | 2.54 | **1.49** |
| Profit factor | 2.85 | **1.80** | 2.19 | **1.74** | 2.31 | **1.60** |
| Max drawdown | $3.37 | **$9.64** | $7.51 | **$7.51** | $4.95 | **$11.29** |
| Merge rate | 65.3% | **59.6%** | — | 57.9% | — | 57.6% |
| MC P5 (100 trades) | +$11.56 | **+$0.81** | — | +$1.54 | — | −$0.35 |
| EV / trade | +$0.235 | **+$0.141** | — | +$0.140 | — | +$0.116 |
| Progress | 935.6 min (65%) | **1308.5 min** | — | — | — | — |

Two consequences the audit must state plainly:

1. **`balanced-mid` is not the top-PnL arm.** Measured PnL ranks `loose-baseline` first
   (+$23.97) with `balanced-mid` second (+$23.46) — a $0.51 gap that this sample cannot
   resolve. The issue's "+$28.37, #1 rank" does not reproduce.
2. **`balanced-mid` is not the risk-shape leader.** By the pre-registered risk-shape
   gates (Sharpe ≥ 2.70, profit factor ≥ 2.50, max drawdown < $4.00) it fails all three,
   and it fails the half-split: first half mean +$0.1993/trade (Sharpe 4.43), second half
   **−$0.0087** (Sharpe −0.16). The only arm clearing all three gates in both halves is
   `queue-clear-5m`.

## Locked verdict rule

Not invented for this audit: taken from the repository's own gate machinery, so it cannot
be shaped to fit these figures.

1. **Verdict** (`statistical_validation_run/artifacts.py:257 resolve_verdict`, driven by
   `core_brain/statistics_report.py:208 write_statistics_report`): `INCONCLUSIVE` when the
   sample is underpowered (closes < `bot_eval_stat_gate_target_closes`, `core_brain/config.py:268`
   = 60) or no confidence interval can be computed; `GO` when the primary gate passes
   strictly above `bot_eval_stat_gate_threshold_pct` (`core_brain/config.py:266` = 1.0);
   otherwise `NO-GO`. The 60-close threshold is a **precondition for a verdict**, never
   the GO itself. `#471` names the same gate, inclusive of every close method.
2. **Candidate ranking**, only if no arm earns a GO: cleared risk-shape gates first, then
   Sharpe, then realized PnL. Underpowered samples are judged on risk shape, not nominal
   PnL (`docs/runs/2026-10-26-843-balanced-queue5-pair.md` §1b).
3. **Authorization** is the operator's, not the audit's. The audit records a
   recommendation; it never runs an opening command.

## Acceptance criteria

The issue's criteria, re-grounded:

- [ ] The audit documents every one of the ten arms of #799 — closes, merges, rescue exits,
      merge rate, PnL, Sharpe proxy, profit factor, max drawdown, VaR95, MC P5, and
      required N for a $0.02 effect — each figure produced by a recorded command.
- [ ] The audit records the measured verdict for each arm from the repo's own gate, and
      states why the 60-close threshold the issue leans on is not the GO.
- [ ] The discrepancy between the issue's figures and the stores is tabulated, with the
      interim-snapshot explanation, and the two named false premises corrected.
- [ ] The deployment payload is documented in two layers: the **measured** arm payload, and
      any **untested** synthesis, each labelled as such. No payload is presented as approved.
- [ ] Reopen conditions are listed: exactly what measurement would move the verdict to GO.
- [ ] No live-capital command is run and no risk-engine, cockpit, or arms-JSON file changes
      (`tests/test_tournament_cockpit_arms.py` pins nine champion env values across three
      places and must pass unchanged).
- [ ] Targeted suites pass: the new tool's tests plus
      `tests/test_tournament_cockpit_arms.py` and `tests/test_tournament_cockpit_funnel.py`.

## Edge cases the audit must handle

- A store whose `σ` is 0 (no closes) — `required_sample_size` returns `None`; the table must
  print "not computable", never a fabricated N.
- `shadow_settlement` rows: the cockpit's analyzer counts them as trades; plain `settlement`
  rows it does not. The audit states which convention each table uses.
- A run folder that does not exist in a fresh checkout (gitignored): the audit says the
  figures are not re-derivable in CI, rather than implying a green reproducible test.
- The arms stopped 131 minutes short of the plan: coverage is reported, not rounded up to 24 h.

## Out of scope

- Running `quote`, `complete`, the Trader loop, or the dashboard START button.
- Changing the cockpit's blueprint pane, its `READY FOR LIVE` labels, or the arm notes in
  `configs/tournaments/i799-tournament-test_arms.json`.
- Changing any risk-engine formula or cap in `core_brain/config.py`.
- Committing the run stores (gitignored) or a 771 MB artifact tree.
