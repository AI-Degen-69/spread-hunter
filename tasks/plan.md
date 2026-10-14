# Plan - #541 Consolidate every project statistic onto one page with one producer per number

Branch: i541/refactor-consolidate-every-project-statistic-onto | Issue: #541

Size: **Large** — cross-cutting (dashboard JS + `core_brain` + `scripts/` + report products),
DOM and math, one architectural decision (registry shape). Task type: **Code + Design/UI**.
Domain tags per task. Helper skills: `test-driven-development`, `incremental-implementation`
(all tasks); `frontend-ui-engineering` (T3). Verification mode: pytest focused files for
logic; harness + rendered-DOM sample + served-asset grep for the page (no-browser syntax
checks alone never count).

## Intake - CodeRabbit plan (adopted / rejected / resolved)

- Adopted: phase skeleton (freeze goldens → dedupe math → page+registry → findings),
  affected files/seams, exact test assertions, regression file list. All cited seams
  spot-checked live — line numbers drifted slightly from the issue text
  (e.g. `renderAnalyticsSurface` now app.js:5158; `report()` kpi.py:1230/2224;
  `run_analysis` audit:97) but every symbol exists; no [UNVERIFIED] remains.
- Rejected: >4 task splits — merged into 4 tasks below (one proposal: math-dedupe-first
  ordering, adopted by default — evidence: the issue's own "Natural split line… (b) can
  land first without touching the DOM").
- Operator override (2026-10-14): single convention is Student's t via `ci_critical_value`
  (`statistical_analytics.py:1076` — t while the sample is small, normal once it isn't).
  Audit adapters, the kpi inline band, and `ladder_probe.mean_ci90` all switch to it.
  Wilson (proportions) and power/planning constants stay as-is — different estimators.
  Pre-switch values frozen in T1 goldens; per-stat deltas logged in T4 + PR.
- Resolved from code: simulator button already `display:none` (index.html:440) → remove;
  `required_observations` double-render confirmed (app.js:4136 + :5171);
  `Z_ALPHA_90_ONE_SIDED == ci_z(90) == 1.645`, `Z_BETA_80_POWER == 0.8416` — stat_gate
  literal swap is value-identical; kpi names the exit tuple `SINGLE_BUY_EXIT_METHODS`
  (:815) vs artifacts' local `EXIT_METHODS` (:60) — name drift recorded, import wins;
  #520 OPEN → use kpi tuples, no restating; #542 CLOSED via merged #543 → audit
  cross-check unblocked.
- Sub-issues deferred: no per-task sub-issues created now — tracker noise stays down
  until T1 goldens prove the baseline on this branch; mapping table below is the record
  Station III works from (T1→none, T2→math, T3→page, T4→findings).

## Interface contracts (frozen before logic)

- `core_brain/statistical_analytics.py`: one public normal-interval helper with explicit
  multiplier — `mean ± mult·(std/sqrt(n))`, exact op order of today's kpi inline math.
  Mean-interval callers pass `ci_critical_value(level, n, z)` (Student's t). Callers keep
  their own empty/one-close adapters (`(mean, mean, 0)`; n<2 still yields no interval —
  t is undefined for one close).
- `core_brain/kpi.py`: one public Wilson name; `_wilson_ci` stays as alias (same signature).
  Sufficiency table (`Z_95/98/99_SAMPLE_SUFFICIENCY` = 1.95996/2.32635/2.57583) importable.
- Registry entry: `{id, producer, block, view, unit, aliasOf?}` — one id, one producer, one
  placement, one declared unit (`$`, `¢`, `%`, `bps`, or `bare` for unitless ratios like SQN).
  Report-only entries carry `{producer, reason}` and are never rendered.
- Charts are not numbers: the SVG deck and the Distributions / Monte Carlo charts stay in
  their current views, one home view each, rendering registry values (same producer, never
  recomputed in JS). No new chart type. The registry covers readout numbers; chart-vs-readout
  parity is tested, not restyled into existence.

## Tasks

- [ ] T1 (M) [Backend/Logic] Freeze today's values. Fixture: fixed shadow run in `tmp_path`
  (reuse `_seed_closes`/`_kpi_for`/`_run_closes` from `tests/test_stat_validation_artifacts.py`;
  mixed methods, fixed-timestamp fills/quotes, one missing-PnL + one zero-cost close,
  equity + account curves starting below capital). Golden JSON of `kpi.report()` stats
  sections (explicit time-field strip list read from `report()`; pre-switch record,
  regenerated once post-switch with deltas logged). Audit goldens (`run_analysis` text +
  95/98/99 values + empty/one-close edges; `mean_ci90` / `_prop_ci90_diff` pins;
  paired-depth min-2 rule; family median-5/p90-9 kept). Page↔report parity
  (`write_statistics_report` vs payload; `report.json` vs payload; `ci90_lower_pct` vs
  stat-gate/sensitivity — difference recorded, code untouched). No-second-producer ledger
  test (RED now). Files: `tests/` (+1 golden JSON, +2-3 test files).
  Helper: `test-driven-development`. Depends on: none. Verify: goldens pass on this
  branch pre-change; ledger test fails.
- [ ] T2 (L) [Backend/Logic] One copy of the math + the t-switch, no DOM change. Core helper
  (explicit multiplier); audit script becomes thin caller (delete `Z_CRITICAL`, 3 fns;
  adapters call `ci_critical_value`, keeping tuple shapes + n≤1/no-interval edges; SQL +
  printed layout untouched). kpi inline band + `ladder_probe.mean_ci90` switch to
  `ci_critical_value`. Public Wilson + `_percentile` docstring as planned. `stat_gate`
  literals → kpi constants; family helper renamed nearest-rank + divergence docstring;
  `artifacts.py` imports kpi tuples; queue-multiple single home module
  (`statistics_report._queue_multiple` + `shadow_fills` call it; boundary test green).
  Report-only computations untouched + listed for T4.
  Helper: `test-driven-development`. Depends on: T1. Verify: goldens regenerated once
  with per-stat t-deltas logged; non-CI values byte-identical; focused suites incl.
  `test_shadow_run` boundary green.
- [ ] T3 (L) [Design/UI] One statistics page on the registry, charts integrated. Rebuild tab-2
  surface in place; registry drives `renderAnalyticsSurface`, 8 readouts, quant grid; each
  tick under exactly one subnav view; dead simulator button removed; `#analytics-gates`
  into Reports group (`PAGE_LAYOUT`); single `required_observations`; home keeps live
  account state only (`page: home` registered), trade stats move to stats page;
  service-health stays; same-population → placed + `aliasOf` (`ta.sortino_ratio` under
  `capital_risk`); different-population/curve → both labeled (equity vs account drawdown,
  gate input marked). Charts: each chart renders registry values (parity-tested
  chart-vs-readout), one home view each. Card restyle (operator-ordered, trading-terminal
  minimal: value-first hierarchy, consistent type scale, spacing, alignment; new CSS scoped
  to the stats surface, same palette family). Unit audit: every card shows its unit symbol
  (`$`/`¢`/`%`/`bps`) or is registry-flagged `bare` (e.g. SQN); unit-coverage test in the
  harness fails a card with a missing or wrong unit. A11y: tablist/keyboard/empty-error
  states preserved. Files: `app.js`, `index.html`, `prototype.js`. Helpers:
  `frontend-ui-engineering`, `incremental-implementation`. Depends on: T2. Verify: ledger
  test GREEN; harness + `test_analytics_surface_mount` green; rendered-DOM sample (incl.
  chart values) + served-asset grep.
- [ ] T4 (S) [Docs] Findings + report-only registry. Findings doc: pre/post-switch t-deltas
  per stat plus remaining disagreements with both numbers (interp-vs-nearest-rank,
  equity-vs-account drawdown, ci90/sensitivity if differ). Registry covers every rendered
  id; report-only + research-only entries listed with producer + reason. PR names removed
  duplicates + aliases. Layouts unchanged. Depends on: T2, T3. Verify: registry-coverage
  test (every rendered id registered; report-only listed, never rendered).

## Checkpoints

- After T1: goldens green pre-change, ledger RED — baseline proven, safe to refactor.
- After T2: t-switch landed with logged deltas, zero DOM diff — math unified, page untouched.
- After T3: page walk (hands-on verify below) — one page, each figure once, charts agree.

## How to verify (hands-on, operator)

Launch the stack (`.\scripts\spread-hunter-menu.ps1 start`), open
`http://127.0.0.1:8799`, walk the statistics page: every project figure is there exactly
once; view buttons filter without repeating a number; chart values match the readouts
beside them; text, sizes, and spacing look uniform. Cross-check the audit script on
`data/02_shadow_prudent_13-10_03-01.db` — its printed CIs match the page for that database
(small-sample bands read slightly wider now: that's the t-switch, deltas in the PR).
