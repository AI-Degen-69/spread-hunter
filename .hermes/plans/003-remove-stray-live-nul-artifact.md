# Plan 003 — Remove the stray `live/nul` artifact

**Category:** Tech-debt / repo hygiene (untracked generated output)
**Confidence:** HIGH  **Effort:** S
**Git commit stamped:** `948ce5a59ec64f80548c27c040aa0c3089ac388e`

## Problem

`live/nul` is a 2.2 MB untracked JSON file at the repo root under `live/`.
`git status --porcelain` shows exactly one untracked entry: `?? live/nul`.

Its contents are a serialized `query_db_state()` dashboard blob:
```json
{"empty":false,"db_path":"C:\\Users\\Tiger\\...\\live\\run\\live.db","server_time_ms":...,
 "pairs":[...],"orders":[...],"fills":[...],
 "capital":{"resting_committed":91.05,"filled_committed":91.55,"total_committed":182.6},
 "last_polled_ts":...,"seconds_since_poll":...,"stale":true,"idle":false,"at_stake":true,
 "reconcile_lock":{"held":false,...}}
```
The filename `nul` (zero characters where a stem should be) suggests a hypothesis: some
writer may have built the output path from a variable that resolved to `""` and concatenated
`live/<empty>.json` → `live/nul`. However, no committed code path produces this file (verified
below). AGENTS.md's workspace-hygiene rule is explicit: "Delete temporary/scratch files
immediately" and "Zero dead weight."

## Vetting done (so the executor doesn't re-litigate)

- Grep of the entire `live/` tree for a writer of this blob: `query_db_state`
  (live_dash.py:233) is only ever returned via `JSONResponse` (HTTP handler) — it
  never writes to disk. The other dicts with `"db_path"` keys (live_dash.py:241,
  566, 990, 1005, 1032) are `get_system_status`/`reset_database` returns, also
  never persisted. `live/engine/live_exec.py` writes `live_orders.json` (atomic,
  explicit name) — not this. **No committed code path produces `live/nul`.**
- Therefore `live/nul` was produced by an ad-hoc one-off command during a live
  cycle (e.g. a REPL/`python -c` dump of `query_db_state(...)`) whose output
  filename came from an empty variable. It is pure generated output, not source,
  and not referenced anywhere.

## Fix

1. Confirm it is not referenced by anything (sanity, not strictly required):
   ```
   cd "<repo root>" && git grep -n "live/nul" || echo "no references"
   ```
   Expected: `no references`.

2. Delete the artifact:
   ```
   cd "<repo root>" && rm -f "live/nul"
   ```

3. Verify it is gone and the tree is clean of it:
   ```
   cd "<repo root>" && git status --porcelain
   ```
   Expected: no `?? live/nul` line (the only prior untracked entry).

## Defensive follow-up (only if a writer is later found)

If, contrary to the vetting above, an executor discovers a code path that writes
`query_db_state()` (or any state blob) to a file with a *derived* name, that path
must guard against an empty stem before `os.replace`/`write_text`:
```python
out_name = path_stem or "live_state"   # never allow ""
out_path = RUN / f"{out_name}.json"
```
Do NOT add this defensively now — there is no caller. Add it only if step 1's
`git grep` (run during execution) unexpectedly finds a writer.

## STOP conditions

- If `git grep -n "live/nul"` finds a reference (something imports/reads it),
  STOP and report — do not delete a file something depends on.
- If `git status` shows `live/nul` is somehow tracked (it is not, per the stamp),
  STOP and report; use `git rm --cached` semantics, never a blind `rm` of tracked
  content without the user's say-so.
- If step 1's grep instead reveals a *live code writer* of state blobs, STOP and
  report the location instead of deleting blindly — the real fix is the writer's
  filename guard (see Defensive follow-up).

## Out of scope

No production code change. No schema change. The db it points at
(`live/run/live.db`) is untouched (and is already gitignored).
