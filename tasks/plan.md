# Plan - #624 [DEFECT] Strip un-set parent HUNTER_* env vars when spawning shadow tournament workers

Branch: i624/strip-parent-hunter-env-shadow-workers | Issue: #624
Size: Small (1 launch site in shadow_tournament.py, 1 companion test in test_shadow_tournament.py, 1 noticed doc update) | Type: Code/Debug
Labels: ready-for-agent (no `quick-fix` label → Step 0C gate skipped, no lane offered)
No `needs-answers` label and no Open-questions section → no operator questions.

## CodeRabbit plan intake (issue comment, read once — echo ignored)

- **Adopted:** 2-task implementation structure (TDD test with fake Popen + worker env filter fix); specific verification assertions (sparse arm gets clean defaults, explicit arm gets its override, unrelated parent vars like FOO retained, SPREAD_HUNTER_DB overridden); marking N1 in `docs/issues/608-noticed-but-not-touching.md` resolved.
- **Rejected:** Over-abstracting the env filtering into a shared helper; touching dashboard hub or Market Filter launch sites.
- **Resolved from code:** `scripts/shadow_tournament.py:516` constructs `shadow_env = {**os.environ, **arm.env, "SPREAD_HUNTER_DB": str(arm.db_path)}`; Market Filter (:480-482) and Dashboard (:500-503) strip `HUNTER_*` via `{k: v for k, v in os.environ.items() if not k.startswith("HUNTER_")}`; `_FakeProc` is already defined in `tests/test_shadow_tournament.py:514-532` and can be reused directly; signal handling in `launch_tournament` installs process-wide handlers on `SIGINT`/`SIGTERM`, so monkeypatching `signal.signal` prevents mutating pytest process state.
- **Still [UNVERIFIED] at build:** None.

## Spec (embedded)

In `scripts/shadow_tournament.py:launch_tournament()`, when constructing `shadow_env` for each arm:
1. Strip all parent environment variables starting with `"HUNTER_"` from `os.environ`.
2. Apply `arm.env` on top of the stripped environment (so arm-specific `HUNTER_*` settings are applied).
3. Set `"SPREAD_HUNTER_DB": str(arm.db_path)` ensuring it pins the arm's dedicated database.
4. Pass the sanitized environment to `subprocess.Popen(arm.shadow_argv, env=shadow_env)`.

## Interface contract (locked before logic)

In `scripts/shadow_tournament.py`:
```python
clean_env = {
    k: v for k, v in os.environ.items()
    if not k.startswith("HUNTER_")
}
shadow_env = {**clean_env, **arm.env, "SPREAD_HUNTER_DB": str(arm.db_path)}
```
No external function signature changes; contract is internal to `launch_tournament`.

## Improvement proposal (adopted by default — edge-case hardening)

In `test_worker_environment_strips_parent_hunter_env`, monkeypatch `signal.signal` to a no-op during `launch_tournament()` execution to prevent registering real SIGINT/SIGTERM handlers that persist across the pytest run.
Evidence: `scripts/shadow_tournament.py:465-469` unconditionally calls `signal.signal(sig, lambda _s, _f: _cleanup())`. Mocking `signal.signal` ensures complete test isolation without altering host signals.

## Tasks (risk-first; each independently reviewable with own tests)

Depends on: T1 → T2 → T3 (T2 solves the failing regression in T1; T3 syncs documentation after code green).

- [x] T1 [Debug/Test] S — Add regression test for shadow worker env isolation (TDD RED).
  Files: `tests/test_shadow_tournament.py`.
  Description: Add `test_worker_environment_strips_parent_hunter_env(tmp_path, monkeypatch)` reusing `_FakeProc`. Set parent ambient `HUNTER_DUMMY_LEAK`, `HUNTER_PRICE_RISK_WIDEN=0.03`, `HUNTER_TOURNAMENT_PRESET=aggressive`, `FOO=bar`, and `SPREAD_HUNTER_DB=data/wrong.db`. Build 2 arms: sparse (`env={}`) and explicit (`env={"HUNTER_PRICE_RISK_WIDEN": "0.02"}`). Run `launch_tournament(plan)`. Match worker argv (`core_brain.shadow_run` / `--db`). Assert sparse arm has no `HUNTER_*` keys; explicit arm has only `HUNTER_PRICE_RISK_WIDEN="0.02"`; both retain `FOO="bar"` and correct arm db.
  Verify: `pytest -q tests/test_shadow_tournament.py -k test_worker_environment_strips_parent_hunter_env` FAILS (RED).
- [x] T2 [Code/Fix] S — Strip inherited `HUNTER_*` env vars in worker launch (TDD GREEN).
  Files: `scripts/shadow_tournament.py:516`.
  Description: Update `launch_tournament()` to filter `os.environ` keys starting with `HUNTER_` before merging `arm.env` and `SPREAD_HUNTER_DB`. Add explanatory comment per conventions.
  Verify: `pytest -q tests/test_shadow_tournament.py` (33 passed) + `pytest -q tests/test_tournament_run_number_naming.py` (9 passed) (GREEN).
- [x] T3 [Docs] XS — Update N1 status in `docs/issues/608-noticed-but-not-touching.md` and record out-of-scope in `docs/issues/624-noticed-but-not-touching.md`.
  Files: `docs/issues/608-noticed-but-not-touching.md`, `docs/issues/624-noticed-but-not-touching.md`.
  Description: Mark N1 row as `resolved` with a note explaining shadow workers now strip parent `HUNTER_*` before applying arm overrides. Create `624-noticed-but-not-touching.md` documenting out-of-scope refactoring rows.
  Verify: Read-back inspection and `git status`.

Checkpoints: after T1 (RED proven) → after T2 (focused suites GREEN) → after T3 (doc sync). Mode A: one-line progress per checkpoint.

## Untouched files (do not open for edits)

`scripts/tournament_dashboard_hub.py`, `core_brain/config.py`, `core_brain/shadow_run.py`, `configs/tournaments/*`, `data/orders.db`.

## Run-environment caveats (owner: planner, when: 2026-10-16)

- Ambient test env vars: tests use monkeypatch to scrub or set vars in-process, preventing host shell leakage (Class 1).
- Hands-on verification uses `--minutes 1 --interval 1 --base-dir <tmp> --stamp isolation-check-01` without `--dashboards`; never run live commands or touch `data/orders.db`.

## Base freshness (proven, not assumed)

`main` matches `origin/main` (0 behind, 0 ahead, clean tree after `git fetch origin` on 2026-10-16).
Branch `i624/strip-parent-hunter-env-shadow-workers` created from clean `main`.
Stale worktrees (`.worktrees/show-i504`, `.worktrees/spread-hunter-i504`, `.worktrees/testing-pony-tail-with-issue-525`) not referenced or reused.
