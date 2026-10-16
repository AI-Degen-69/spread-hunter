# Constraints - #624 (strip un-set parent HUNTER_* env vars when spawning shadow tournament workers)

Branch: i624/strip-parent-hunter-env-shadow-workers | Issue: #624

- **Zero regressions:** `tests/test_shadow_tournament.py` +
  `tests/test_tournament_run_number_naming.py` must pass.
  The new worker isolation test must fail before the change (TDD RED first).
  Full suite stays with CI on push; never run it locally during build.
- **Anti-cheat:** never skip/disable tests, delete assertions, or suppress
  linters to reach green.
- **No new dependencies.** Standard library `os.environ` filtering and `subprocess` only.
- **Scope discipline:** touch only `scripts/shadow_tournament.py` (worker launch site around line 516),
  `tests/test_shadow_tournament.py`, and `docs/issues/608-noticed-but-not-touching.md`.
  Do not modify `scripts/tournament_dashboard_hub.py`, `core_brain/config.py`, or tournament configs.
- **Production safety:** never write to or delete `data/orders.db`. No live
  trading commands at any station (`quote`, `complete`, Trader loop, etc.).
- **Job Guards:**
  - Do not reference or reuse stale worktrees (`.worktrees/show-i504`, `.worktrees/spread-hunter-i504`, `.worktrees/testing-pony-tail-with-issue-525`).
  - No destructive git operations without explicit operator confirmation.
  - Recording homes: code out-of-scope rows in `docs/issues/624-noticed-but-not-touching.md`; run-environment caveats in `tasks/plan.md`.
  - Ambient test env vars: mock or unset in place (Class 1).
