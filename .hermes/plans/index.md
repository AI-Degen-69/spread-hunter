# Plans index — /improve audit (2026-08-20)

Repo: spread-hunter (sim tree at root + forked `live/` real-money tree)
Git commit stamped at plan authoring time: `948ce5a59ec64f80548c27c040aa0c3089ac388e`
Commands assume the `uv` runner (pytest is not in the default venv):
  - sim:    `cd "<repo root>" && uv run pytest -q tests/test_selection.py`
  - live:   `cd "<repo root>/live" && uv run pytest -q tests/test_live_dash.py`

## Priority order (by leverage = impact ÷ effort, weighted by confidence)

1. `001-timebomb-trial-market-fixture.md`   — HIGH confidence, S effort, blocks CI now. **Do first.**
2. `002-reset-db-test-isolation.md`         — HIGH confidence, S effort, blocks live suite on a running machine.
3. `003-remove-stray-live-nul-artifact.md` — HIGH confidence, S effort, repo hygiene (untracked 2.2 MB blob).
4. `004-gitignore-generated-run-dbs.md`     — MED confidence, S effort, prevents future commit of generated DBs.

## Dependency graph

All four are independent (no ordering constraint). 1 & 2 are pure-test fixes;
3 & 4 are repo hygiene. Any subset can be executed in any order. Each plan is
self-contained: inlined file paths, current-state excerpts, verification gates,
and STOP conditions so a model that never saw this session can execute it.

## Execution note

Per the `improve` skill, execution should run in an isolated git worktree
(`git worktree add`) so the main tree stays clean; the merge decision is Robert's.
The cheaper executor is realized by pinning `delegation.model`/`delegation.provider`
cheap (global) — revert after, or run the executor on the main model if preferred.
