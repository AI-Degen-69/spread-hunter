# Plan - #569: Run a loose baseline so every later test has something to compare against

Branch: i569/loose-baseline-reference-run | Issue: #569

## Why (issue summary)
The `baseline` tournament preset (wide, loose quoting) has never been run. This issue runs one short practice arm with it and records the numbers, giving every later tightening test a fixed "before" picture. No code changes, no setting changes, no real money.

## Size & type
- **Tier: Small** — run-only; existing launcher, existing reports, no code edits.
- **Task type: Research** — a rehearsal run producing evidence. Domain tag: `[Research]`. Verification mode: run artifacts (plan JSON, results JSON, DB, heartbeat) + existing pytest selection.

## Step 0A intake (CodeRabbit plan — adopted / rejected / resolved)
- **Adopted:** one-arm `baseline` arms file with stable naming (`--issue 569 --run-number 1`); short isolated practice run (clean env, fresh DB, preset via env only, no signer); completion proved by results file + arm exit code + DB + heartbeat; reference recorded in a `docs/runs/` note.
- **Rejected (with reason):** extending `read_arm_results` with a per-market `markets` list. The issue asks for per-market numbers *in the report*, and `scripts/pair_fill_report.py` already reports per-arm/per-market from the DB — a launcher code change is scope expansion for a reporting need existing tooling covers.
- **Resolved from code:** `read_arm_results` exists (`scripts/shadow_tournament.py`); `pair_fill_report.py` exists; `shadow_run` takes `--minutes/--db/--run-id`; `docs/runs/` exists; `/runtime/*` logs are gitignored. Still open for Station III: which universe file the arm reads when no `--markets-path` is passed, and whether `data/*.db` is gitignored (decides if the DB path is referenced or committed).

## Spec (embedded — Small, no SPEC.md ceremony)
- Goal: a finished 5-minute practice run of the baseline preset with a results report showing fills, merges, exits, and PnL overall and per market.
- Out of scope: #492's old-arm rerun, any setting-value change, any code change, live trading, `data/orders.db`.
- Edge cases: arm exits non-zero (read the log, fix the invocation, never the strategy); zero fills in 5 minutes (report it as measured, not as failure — the reference is an observation); stale universe file (use the shared filter output, copy it fixed for the run).

## Improvement proposal (adopted — simplification)
Use the existing `pair_fill_report.py` for the per-market breakdown instead of adding a `markets` list to the launcher's results reader. Evidence: `scripts/pair_fill_report.py` exists and is the established per-arm reporter (cited by #492 for the same job); the issue's acceptance asks for report numbers, not launcher features.

## Dependency graph
T1 (arms file + dry-run proof) → T2 (short run + completion proof) → T3 (per-market report + reference note + evidence).

## Tasks
- [x] **T1 [Research] (S): one-arm baseline arms file + dry-run.** Write the single-arm arms file (preset `baseline`, shared universe), print the plan with `--dry-run`, confirm fixed paths (`i569_tournament-1.json`, `control-1`-style slug, `data/i569_tournament-1_<slug>-1.db`). Depends on: none. Verify: dry-run exits clean and the plan JSON names the expected files.
- [x] **T2 [Research] (M): short isolated practice run.** Run the arm ~5 min with a clean env (`HUNTER_TOURNAMENT_PRESET=baseline`), fresh DB, no dashboards. Depends on: T1. Verify: arm exit code 0, results JSON exists, DB exists with closes/fills tables, heartbeat finished. CHECKPOINT: run works end-to-end.
- [x] **T3 [Research] (S): per-market report + reference note.** Run `pair_fill_report.py` on the DB, write the `docs/runs/` reference note (paths, preset, totals, per-market table), post the numbers as an issue comment. Depends on: T2. Verify: note exists, numbers match the results JSON, `python -m pytest -q tests/test_shadow_tournament.py` green.
