# CONSTRAINTS.md — Issue #787: decide whether to raise the entry edge floor

Branch: `i787/decide-whether-to-raise-the-ev-edge-floor` | Issue: #787

Locked during Station II planning. Stations III–V must hold every line below.
Tighten only with operator approval; never loosen silently.

This issue is **analysis and a decision record**. It changes no live behaviour.

## Zero regressions

- Focused suites for the touched files must be green after the change:
  `python -m pytest -q tests/test_edge_floor_analysis.py tests/test_fill_quality_gate.py`.
- `tests/test_fill_quality_report.py` and `tests/test_fill_quality_gate.py` must stay
  green **untouched in their existing assertions**: the analysis script imports
  `fill_quality_report` and must not alter it (no edit to `core_brain/**`).
- Every new behaviour needs a test that fails without it: the pair/position grouping,
  the negative-edge bucket, the floor sweep conservation, and the crossover
  sufficiency rule each get one.

## Scope fences (hard)

- **No changes under `core_brain/`.** Not `fill_quality_report.py`, not `config.py`,
  not `trader_loop.py`. The gate's live behaviour is out of scope by the issue's own
  acceptance line ("No live behaviour changes without operator sign-off").
- **No writes to any `.db`.** Every read opens `mode=ro`; the script never creates,
  migrates, or repairs a store. `data/orders.db` is the production registry: read only.
- **No venue-reaching command.** `shadow_run` is allowed (no signer, spends nothing);
  `quote`, `complete`, the Trader loop and dashboard START are the operator's.
- No new files under `core_brain/` or `dashboard/`. Deliverables are one script, its
  test file, one test added to an existing file, and one audit document.

## Anti-cheat

- Never skip, xfail, or delete a test to get green; never weaken an assertion.
- The decision rule is fixed **before** the numbers are read (see `tasks/plan.md`
  §Decision rule). Reading the buckets first and then choosing a rule is the exact
  failure this issue exists to prevent — if a rule changes after the numbers, the
  document must say so and why.
- Never present a rehearsal number as evidence about live performance: shadow numbers
  are labelled "rehearsal" in every table, heading and summary sentence.
- Never state a crossover, a bucket, or a floor the data does not support. A bucket
  under `DEFAULT_MIN_OBS = 30` closed positions is `insufficient`, and "keep current"
  is a valid, expected outcome.

## Performance / architecture ceilings

- The analysis reuses `fill_quality_report.load_store` / `build_report` /
  `edge_bucket_for` / `score_ab`. **No second SQLite loader, no second bucketing
  function, no second PnL attribution.** The only bookkeeping filter is a
  `dataclasses.replace` on the loaded `StoreLoad` with closes filtered by
  `close_methods.BOOKKEEPING_METHODS`.
- The script stays offline and deterministic: no network, no clock-dependent numbers
  inside the tables (the only clock use is the output folder stamp), and the same
  store + args always produce the same table.
- One store per invocation. The two shadow tapes have different presets
  (`shadow-01`, `shadow-01-prudent`); pooling them would mix configurations, so they
  are reported side by side, never summed.

## Dependencies

- No new external dependency, no new package. Standard library plus this repo only.

## Safety (repo rules)

- `data/*.db` are gitignored and must never be committed. `reports/` is gitignored too.
- The live step — setting `BOT_ENTRY_FILL_QUALITY_GATE_ON` /
  `BOT_ENTRY_FILL_QUALITY_BLOCKED_EDGE` and restarting the trader — is written as the
  operator's action in the decision record, never executed by the agent.
