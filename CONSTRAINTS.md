# CONSTRAINTS.md — Issue #866: Arm-specific effective Strategy Parameters

Branch: `i866/fix-strategy-parameters-panel-shows-global-config` | Issue: #866

Locked during Station II planning. Stations III–V must hold every line below.
Tighten only with operator approval; never loosen silently.

## The one invariant this change exists to protect

**The global (no-arm) panel path is byte-identical, and an arm view never shows
a value the engine does not use.**

A failed arm resolution returns a structured error (`NO_PLAN / MALFORMED_PLAN /
UNKNOWN_ARM / AMBIGUOUS_ARM / EVAL_FAILED`) — never a silent fallback to global
values that the operator would read as the arm's config.

## Zero regressions

- Targeted suites covering the touched modules must pass cleanly:
  - `python -m pytest -q tests/test_dashboard_server.py` (existing
    `/api/parameters` tests: catalog shape, zero-DeprecationWarning, scoring
    placeholder path)
  - New focused tests for `dashboard/arm_params.py` (catalog, exact/ambiguous
    arm match, child eval, memo) — each new behavior needs a test that fails
    without it.
- Full `python -m pytest -q` is the GitHub CI merge gate on Ubuntu and Windows;
  local runs are targeted only.
- The flow-window card must assert the true engine default (`1800.0`) when no
  loader reads the recorded override — a test pinning `300` as the engine value
  is evidence of the lie, not of correctness.

## Scope fences (hard)

- **Only these areas change:**
  - `dashboard/arm_params.py` (new): plan catalog, arm resolution, child
    entry point, parent runner + memo. No cockpit imports.
  - `dashboard/server.py`: `get_parameters()` gains optional
    `?arm=&plan=&tourney=` wiring plus the three Queue Gate cards
    (`name`/`key`/`env_var`/`value` only). Global path untouched.
  - `dashboard/static/index.html` + `dashboard/static/app.js`: arm picker in
    `#params-panel` header, fetch with params, stale-response guard, identity
    + global-default labels.
  - `tests/**`: new `arm_params` tests + endpoint `?arm=` tests.
  - `tasks/plan.md`, `tasks/todo.md`, `CONSTRAINTS.md`, `SPEC.md`.
- **Must NOT be modified:**
  - `core_brain/config.py`, `scoring/config.py` (no env-map params, no new
    readers — the flow-window reader stays absent by design this round).
  - `scripts/shadow_tournament.py`, `scripts/tournament_hub.py`,
    `scripts/tournament_cockpit_server.py` (launcher + cockpit untouched).
  - `data/orders.db` (production registry, strictly read-only) and any
    `data/tournaments/*/plan.json`.
  - Engine arithmetic, merge/settlement, risk ratios (`*_pct`).

## Anti-cheat & safety rules

- **Anti-cheat:** no skipped/xfailed tests, no deleted assertions, no weakened
  bounds, no `# type: ignore`/`noqa` added to get green. Never swap
  `os.environ` in the dashboard process (multi-threaded) — isolation is the
  child process only.
- **No new dependencies.** Standard library only (`sys.executable -m`,
  `dataclasses.asdict`, timeouts, locks).
- **No live venue contact.** Planning, build, tests and review never run
  `quote`, `complete`, the Trader loop, or the dashboard START button. No
  worker launch for verification (`reconcile_tournament_stack` is out of bounds
  for this change); verify dashboard-only against an existing `plan.json`.
- **No production database in any manual check.** Any `status` run points at a
  disposable DB.
