# Plan 004 — Gitignore generated `run/` DBs at any depth

**Category:** Tech-debt / DX (prevent committing generated output)
**Confidence:** MED  **Effort:** S
**Git commit stamped:** `948ce5a59ec64f80548c27c040aa0c3089ac388e`

## Problem

AGENTS.md forbids committing generated output / debug logs. The `.gitignore`
ignores `run/*.db` (root level) and `run/*.db-wal`, `run/*.db-shm`,
`run/*.db-journal`, `run/*.db.bak-*` — but a single `*` does NOT cross `/` in
gitignore globs. So **nested** generated DBs under `run/` are NOT ignored, e.g.:

- `run/bankroll_100/fleet.db` (+ `-wal`, `-shm`)
- `run/bankroll_200/fleet.db` … `run/bankroll_1000/fleet.db`
- (any future `run/<subdir>/*.db*`)

These are regenerated runtime output from the bankroll experiment harness
(`scripts/bankroll-*.ps1`, `scripts/launch_bankroll_experiments.py`). They must
never enter version control.

Note what MUST stay tracked (do not over-ignore):
- `run/live_test_*.json` — pre-registered predictions, source of truth
  (gitignored-list comment at .gitignore:13-14 explicitly keeps these).
- `run/markets.json` — fleet input, source of truth (explicitly un-ignored).
- `run/pipeline.json`, `run/near_misses.jsonl`, `run/volume_near_misses.jsonl`,
  `run/fleet_state.json`, `run/trial_depth_report.json`,
  `run/live_poll_heartbeat.json` — already ignored by explicit lines.

## Current state (excerpt)

`.gitignore` (relevant lines):
```
run/*.db            # line 15  (root-level only)
run/*.db-*          # line 16
run/*.db.bak-*      # line 17
...
run/*.db            # line 78 (duplicate, same root-only scope)
run/*.db-journal   # line 79
run/*.db-wal       # line 80
run/*.db-shm       # line 81
```

Verify the gap before editing:
```
cd "<repo root>"
git check-ignore run/bankroll_100/fleet.db && echo IGNORED || echo "NOT IGNORED  <-- gap"
git check-ignore run/fleet.db && echo IGNORED || echo "NOT IGNORED"
```
Expected at stamp time: `run/fleet.db` → `IGNORED`; `run/bankroll_100/fleet.db`
→ `NOT IGNORED`.

## Fix

Add recursive globs so any-depth DB files under `run/` are ignored, placed near
the existing `run/*.db` block (e.g. after line 81):

```
# Generated SQLite at any depth under run/ (bankroll_* subdirs, etc.).
# A bare `run/*.db` only matches the root level; `**` crosses subdirs.
run/**/*.db
run/**/*.db-*
run/**/*.db.bak-*
run/**/*.db-journal
run/**/*.db-wal
run/**/*.db-shm
```

Do NOT add `run/**/*.json` — that would swallow the intentionally-tracked
`run/live_test_*.json` and `run/markets.json`.

## Verification gates

```
cd "<repo root>"
# 1. nested DBs now ignored
git check-ignore run/bankroll_100/fleet.db run/bankroll_500/fleet.db-wal
#    Expected: both paths printed (means "ignored")
# 2. root DB still ignored (regression)
git check-ignore run/fleet.db
#    Expected: path printed
# 3. tracked sources still NOT ignored (direct test, exit code 1 = not ignored = correct)
git check-ignore --no-index --quiet run/live_test_0x14d32732.json && echo "ERROR: live_test JSON is ignored" || echo "OK: live_test JSON not ignored"
git check-ignore --no-index --quiet run/markets.json && echo "ERROR: markets.json is ignored" || echo "OK: markets.json not ignored"
#    Expected: both show "OK: ... not ignored"
```

Positive check:
```
git status --porcelain
```
Expected: no `run/bankroll_*/` entries appear as untracked (they are now ignored).
Only legitimate untracked files (if any) remain.

## STOP conditions

- If `git check-ignore run/live_test_0x14d32732.json` prints the path (meaning
  you accidentally ignored the tracked source-of-truth JSON), STOP, remove the
  over-broad rule, and re-verify.
- If `git status` suddenly shows a large batch of `run/**` deletions staged, STOP
  — you may have `git add`ed before checking; this plan only edits `.gitignore`,
  it never `git add`s or `git rm`s.
- Do not use `run/**` alone without the `*.db*` qualifier — `run/**` would ignore
  everything under run/, including the tracked JSON sources.

## Out of scope

No deletion of the on-disk `run/bankroll_*/fleet.db` files (they can stay on disk;
they simply stop being candidate commits). No change to simulation `strategy/`
output handling.
