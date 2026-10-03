# Constraints: Issue #351 — Diagnose zero-fill 01_shadow rehearsal and improve queue selection

Branch: i351/diagnose-zero-fill-01-shadow-rehearsal-and-improve | Issue: #351

## Quality & Tests
- **Zero regressions**: `tests/test_shadow_fills.py`, `tests/test_maker_queue_bar.py`,
  `tests/test_statistics_report.py` stay green. Full-repo suite stays with GitHub CI.
- **Every changed behaviour needs a test that fails without the change.** The queue-multiple
  helper ships with helper + evidence-value tests; the report change ships with
  single-cycle vs multi-cycle fixture tests.
- **Anti-cheat**: no skipped tests, no deleted assertions, no new suppressions, no linter silencing.
- **No new external dependencies**: standard library only (`statistics`, `sqlite3` via existing
  `OrderRegistry`). No new config keys, no new env vars.

## Behaviour Boundaries
- **Shadow-only, observational.** Touch only `core_brain/shadow_fills.py` (pure helper),
  `core_brain/statistics_report.py` (report lines), optionally `core_brain/kpi.py`
  (`median_queue_multiple` next to `median_queue_ahead`), tests, and one new diagnosis doc
  under `docs/issues/`. The tape-only fill rule (`credit_fills`), the queue-bar files
  (`scoring/selector.py`, `scoring/config.py`, `scripts/filter_markets.py`), and the
  lifecycle files (`core_brain/shadow_exec.py`, `core_brain/shadow_run.py`) are OUT OF SCOPE.
- **Queue-bar enforcement is deferred** (shared ranker feeds live trading; normal ranking
  supplies no `queue_minutes_fn`). Documented as proposed-not-implemented, never enforced here.
- **`data/orders.db` is never touched.** No writes to and no deletion of any `*01_shadow*` store.
  Operator verification works on scratch copies only.
- **Language**: never describe shadow fills as venue performance. Evidence from the absent
  stores is labeled "reported by ticket; not reproduced in this checkout".

## Performance Budgets
- Helper is O(1) pure arithmetic; report adds one pass over run-attributed quotes plus one
  `cycle_intent` scan — no new network, no new polling loops.

## Out of Scope (record, do not fix)
- Tape-only fill rule changes, live quoting changes.
- `--minutes` run-duration guard (would break intended short smoke runs).
- Issue #352 (shadow resume lock) — separate issue, separate branch.
