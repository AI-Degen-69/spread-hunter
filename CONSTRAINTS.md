# CONSTRAINTS — #558 (locked by Station II, enforced through Station V)

Governs the `spread-hunter_i558-561_taxonomi` branch only. The #564 contract
this file replaces is branch-scoped by its own header line and stays readable
in git history.

## Scope & File Boundaries

- New files (exactly two, plus this planning set):
  - `core_brain/close_methods.py` — the canonical taxonomy; stdlib only,
    `from __future__ import annotations`, no `core_brain` imports.
  - `tests/test_close_method_taxonomy.py` — the guard; imports only
    `core_brain.close_methods`.
  - `tasks/plan.md`, `tasks/todo.md`.
- Files that must NOT be modified:
  - Every consumer — `core_brain/kpi.py`, `statistical_analytics.py`,
    `registry_state.py`, `ladder.py`, `market_resolution.py`,
    `order_registry.py`, `paired_shadow.py`, `shadow_exec.py`,
    `single_buy_saver.py`, `run_scorer.py`, all of
    `statistical_validation_run/`, every `scripts/` file, and
    `dashboard/` — those moves are #559/#560's scope. The guard FAILS on
    them by design; do not "fix" them here.
  - `data/orders.db` and every other store — read-only throughout.
  - Every existing test file.

## The guard is RED at T1 — by design

- Issue #558's verify line: "RED before the consumers move, GREEN after".
  Operator decision (this session): the scan asserts ZERO stray method
  lists. It is red at T1 on the current sites and clears only at #560.
- This is not a regression, not a bug, and not something to pin, allowlist,
  xfail, or scope-expand away. Station IV stops before merge and hands the
  red PR to the owner; T1 and #559 merge by owner override; main is red
  until #560 lands.
- Everything else in the new test file (pins, scanner self-checks) must be
  GREEN at T1.

## Quality bar

- Zero regressions: the module is new (no importers yet); no existing test
  is touched. The focused run `python -m pytest -q
  tests/test_close_method_taxonomy.py` is expected RED (scan only); pins
  and self-checks GREEN.
- The test file cannot import without the module — RED first by
  construction.
- Anti-cheat: no skipped or disabled tests, no deleted assertions, no
  linter suppression, no baseline pin of current violations, no new
  external dependencies.
- Naming/typing (docs/agents/python-conventions.md): SCREAMING_SNAKE_CASE
  constants, PascalCase record, type hints and docstrings on the module's
  public surface, absolute repo-rooted imports, `from __future__ import
  annotations`.
