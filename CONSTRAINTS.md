# CONSTRAINTS — #589 (locked by Station II, enforced through Station V)

Branch: i589/remove-short-window-ladder | Issue: #589

## Scope & File Boundaries

- Delete: `core_brain/ladder.py`, `scripts/ladder_probe.py`,
  `scripts/ladder_live_books_trial.py`, `scripts/ladder_shadow_rehearsal.py`,
  `scripts/ladder_tape_collect.py`, the nine `tests/test_ladder_*.py` suites.
- Edit: `core_brain/config.py`, `core_brain/quotes.py`, `core_brain/markets.py`,
  `core_brain/single_buy_saver.py`, `core_brain/close_methods.py` (writer note only),
  `tests/test_541_audit_goldens.py` (helper inlining), the five rename-only test files +
  `tests/js/live_state_harness.cjs`, comment-only edits in `shadow_exec.py`,
  `shadow_run.py`, `trader_loop.py`, `statistical_analytics.py`, plus
  `docs/agents/first-run.md`, `docs/data_inventory.md`,
  `dashboard/static/data_analysis.js`, `docs/explanations/btc-eth-ladder-strategy.html`
  (retirement notice only).

## Must NOT change (verified survivors)

- The `ladder_exit` close-method string, `LADDER_EXIT`, and `RESCUE_METHODS` membership in
  `close_methods.py` / `kpi.py` / analytics buckets — owned by the #558-560 taxonomy chain;
  old databases still contain these rows.
- Order-book depth ladder code: `snapshot_ladder` (`shadow_exec.py:1075-1099` and callers).
- Risk-sizing ladder code and prose: `config.py:127,840`, `risk.py:112`.
- `_route_pair` as a function — only its ladder selection goes (refill tests depend on it).
- `docs/ideas/short-window-ladder.md`, issue #325, `docs/runs/*`, `tasks/*` history.

## Quality bar

- Zero regressions: every targeted suite listed per task in `tasks/plan.md` must be green
  after its task. The full `pytest` suite is the CI merge gate (Ubuntu + Windows) — not run
  locally during review stations.
- The inlined `mean_ci90` / `_prop_ci90_diff` keep their exact numeric behavior (the
  `statistical_analytics.py` equivalents give different results — do not substitute).
- Renames must preserve every existing assertion; comment-only files must not change
  behavior.

## Anti-cheat

- No skipping, disabling, or deleting assertions to go green.
- No test file may be emptied to avoid a repair — inline or migrate, never drop.
- No new external dependencies.

## Merge discipline

- Land behind the #558-560 taxonomy chain; the PR body states the merge order.
- On `close_methods.py` conflict: keep the taxonomy chain's version, then re-apply the
  legacy-writer note.
- Never touch `data/orders.db`; no venue-reaching commands in this branch.
