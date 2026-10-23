# CONSTRAINTS.md — Issue #781: Remove mislabeled "Strategy Pricing Band" gate row

Locked during Station II planning. Stations III–V must hold every line below.
Tighten only with operator approval; never loosen silently.

## Zero regressions

- `tests/test_analysis_v2_binding.py` (focused suite for the touched surface) must be
  green after the change. It drives the real `analysis_v2.js` through
  `tests/js/analysis_v2_harness.cjs` — no mock renderer.
- `tests/test_decision_gates.py` covers the older `decisionGatesRows` table in
  `app.js`, which this issue does NOT touch: it must stay green untouched.
- Every changed behaviour needs a test that fails without the change
  (e.g. run the new assertions against the pre-fix code via `git stash` and watch
  them fail, then unstash).

## Anti-cheat

- Never skip or delete a test to get green; never weaken an assertion
  (e.g. do not lower the `hookCount >= 45` floor just to dodge a count change —
  removing one `data-bind` keeps the count above the floor, so no floor change
  is needed or allowed).
- Never suppress a linter/typecheck warning introduced by the change.
- The empty-payload render must blank the removed cells, not paint a new
  placeholder: the pre-fix bug paints `0.0% in sweet spot` + `Active Rule` on `{}`.
  The fix removes the row, so there is nothing to paint — no replacement text.

## Performance / architecture ceilings

- `analysis_v2.js` stays fetch-free: no `fetch(`, `XMLHttpRequest`, or
  `EventSource(`. The existing `test_analysis_v2_module_never_fetches` test guards this.
- `renderAnalysisV2(root, kpi)` keeps its 2-argument signature. No third
  (context/quoting-band) argument, no new repaint hook, no new poll work.
- No backend change: `probability_bell` keeps returning `sweet_spot_pct`,
  `samples_count`, `bins` (no `sweet_spot_low/high` added); quoting-band config
  values and enforcement in `quotes.py` / `risk.py` are untouched.

## Dependencies

- No new external dependencies, no new Node/Python packages, no new harness
  files unless the existing `analysis_v2_harness.cjs` provably cannot expose a
  needed assertion (it already exposes `painted`, `bands`, and `gates`).

## Safety (repo rules)

- Read-only planning and build: never touch `data/orders.db` (production
  registry); manual checks use the demo store only and never press START.
- No venue-reaching commands. Opening commands (`quote`, `complete`, Trader
  loop, dashboard START) are the operator's to run — propose, don't run.
