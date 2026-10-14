# #541 findings — producer disagreements, t-switch deltas, and the visible remainder

Branch: `i541/refactor-consolidate-every-project-statistic-onto`. T4 record: every place
two former producers disagreed (both numbers, no silent coin flip), every deliberate value
move, and every statistic that stays out of the page — so the remainder is visible.

## 1. Operator-ordered Student's-t switch (the only deliberate value moves)

Pre-switch values frozen in `tests/test_541_golden_payload.json` + `test_541_audit_goldens.py`;
goldens regenerated exactly once. Fixture: 8 closes (7 measured), return_pcts n=6.

| Stat | Before (normal z) | After (Student's t) |
|---|---|---|
| `trade_analytics.ci90_lower_pct` | -1.6525 | -6.7851 (t90, df=5) |
| `trade_analytics.ci95_return_pct` | (-6.0221, 48.3555) | (-14.4978, 56.8312) |
| audit 95% mean CI (margin) | (-0.2170, 1.7920, ±1.0045) | (-0.4246, 1.9996, ±1.2121) (t95, df=7) |
| `ladder_probe.mean_ci90([1,3])` | (2.0, 0.355, 3.645) | (2.0, -4.314, 8.314) (t90, df=1) |
| Wilson `(5,8)` 98% lower | 0.26292309097269173 | 0.2629230909726917 (1-ulp op-order realignment from the shared function; printed 2dp output identical) |

Unchanged: 98%/99% mean CIs, all planner Ns, Wilson otherwise, every non-CI golden
byte-identical. Small-sample bands read wider now — that is the correction working
(the old z-bands were overconfident at n=6..8), not a regression.

## 2. Remaining disagreements (recorded, not settled)

- **Percentile convention.** `[1,3]` p50 = 2 under interpolated
  `statistical_analytics._percentile` vs 1 under nearest-rank
  `family_probe_report._nearest_rank_percentile`. (1..10: median 5, p90 9 under both.)
- **Drawdown curves.** `ta.max_drawdown_pct` walks the trade-equity curve;
  `recovery.deepest_*` walks the venue account-value curve. Different populations,
  different statistics — both placed, each labeled with its curve.
- **ci90 populations.** `trade_analytics.ci90_lower_pct` (-1.6525 pre-switch) measures
  over measured returns; the gate/sensitivity base (-1.7740) over inclusive closes.
- **Audit vs payload means.** The audit averages 0.7875 over N=8 (unmeasured close as 0,
  its own long-standing convention, untouched); the payload expectancy is 0.9 over the
  7 measured closes (unmeasured excluded since T1 — `kpi.report` crashed on it before).

## 3. Rules the new tab runs on

- **Cards once; sentences are context.** Hints, sub-lines, banner detail lines, chart
  footers, and gate-threshold rows may restate a number with its context; stat cards
  appear once. Gate rows are registered as `refOf` the canonical card.
- **Live account state is not a statistic.** Net value, starting capital, realized /
  unrealized PnL stay on home and the legacy grid under the same exemption as
  service-health readings; the registry marks them `live-state`.
- **98%/99% stay normal (follow-up).** `CI_T_BY_DF` tabulates 90/95 only, so
  `ci_critical_value` falls back to z at 98/99. Extending the table is a follow-up —
  it needs 60 hand-checked constants, which is exactly the transcription risk this
  issue removes elsewhere.
- **Legacy tab-2 is frozen with two known warts** (removal is the follow-up):
  the capital-risk hints say "equity curve" while the walk reads the account curve
  (true label on the new tab), and `core_brain/ladder.py:19` carries a third copy of
  the exit-method tuple (#520 owns classification).

## 4. Report-only remainder (see also STAT_REPORT_ONLY in data_analysis.js)

Lifetimes, cancel reasons, queue sections (`statistics_report.py`); rescue rates,
run counters, validation verdict (`statistical_validation_run/`); tape/probe sims,
two-arm comparisons, sensitivity simulator (research-only). Layouts untouched.

## 5. PR checklist (for Station IV)

Name the removed duplicates (audit Z table + 3 fns, ladder 1.645, stat-gate literals,
paired-depth power copy, family percentile name, artifacts tuples, queue formula) and
the aliases (`_wilson_ci`, `ta.sortino_ratio` under capital-risk, gate-row refs).
Point at this file for the numbers above.
