# Plan 002 — Make reset-db endpoint tests hermetic

**Category:** Correctness / test isolation (suite-blocking on a live machine)
**Confidence:** HIGH  **Effort:** S
**Git commit stamped:** `948ce5a59ec64f80548c27c040aa0c3089ac388e`

## Problem

In `live/`, two tests currently FAIL on the operator's machine:
- `tests/test_live_dash.py::test_system_reset_db_endpoint`
- `tests/test_live_dash.py::test_reset_db_refuses_to_destroy_an_archived_run`

Failure: `reset_database()` returns
`{"ok": False, "message": "Refusing to reset while the bot stack is running. Stop the bot first."}`
instead of archiving/initializing the DB.

Root cause: `reset_database()` (live/dash/live_dash.py:972) gates on the
**global** process file `LIVE_ROOT / "run" / "live_procs.json"` to decide if the
bot is `RUNNING`:
```python
if get_system_status()["bot_state"] == "RUNNING":   # live_dash.py:985
    return {"ok": False, ...}
```
`get_system_status()` reads `LIVE_ROOT/run/live_procs.json` (live_dash.py:760).
On the operator's machine the live bot is running, so that file records real PIDs
and `bot_state == "RUNNING"`. The two tests do NOT monkeypatch `LIVE_ROOT` to a
temp dir, so they consult the *real* procs file and the gate fires.

This is correct production behavior (you must not reset a live DB while the bot
runs), but the tests are not isolated. Every other state-changing test in the file
isolates by doing `monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)`
(see live_dash.py test references at lines 990, 1011, 1041, 1067, 1087, 1100,
1131). These two simply missed it.

## Current state (excerpt)

`live/tests/test_live_dash.py`:
```python
def test_system_reset_db_endpoint(client, temp_db):     # line 802
    ...
    res = client.post("/api/system/reset-db", headers=_control(client))
    assert res.status_code == 200
    data = res.json()
    assert data.get("ok") is True                        # <-- fails: ok is False

def test_reset_db_refuses_to_destroy_an_archived_run(tmp_path):  # line 706
    from dash.live_dash import reset_database
    ...
    result = reset_database(archived)                    # <-- returns ok=False
    assert result["ok"] is False
    assert "archived run" in result["message"]
```

Note `test_system_reset_db_endpoint` uses the `client` fixture (which sets
`set_db_override(temp_db)` — so the *DB target* is already isolated; only the
*bot-running gate* reads the global procs file).

## Fix

Add `monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)` to both tests so the
global procs file is absent → `bot_state == "STOPPED"` → the gate is bypassed and
reset proceeds against the test's own DB.

For `test_system_reset_db_endpoint` (add near the top of the test body, after the
`client`/`temp_db` fixtures are in scope — `dash_mod` must be imported):
```python
import dash.live_dash as dash_mod
monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)
```
`tmp_path` is already available (the test signature is `(client, temp_db)`; add
`monkeypatch` to the signature: `def test_system_reset_db_endpoint(client, temp_db, monkeypatch):`).

For `test_reset_db_refuses_to_destroy_an_archived_run` (signature is
`(tmp_path)`; add `monkeypatch`):
```python
import dash.live_dash as dash_mod
monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)
```
Place it after `from dash.live_dash import reset_database`.

## Verification gates

```
cd "<repo root>/live"
uv run pytest -q "tests/test_live_dash.py::test_system_reset_db_endpoint" "tests/test_live_dash.py::test_reset_db_refuses_to_destroy_an_archived_run" -v 2>&1 | grep -E "PASSED|FAILED|passed|failed"
```
Expected: both `PASSED`, summary `2 passed`.

Regression gate (the rest of the file must stay green):
```
cd "<repo root>/live"
uv run pytest -q tests/test_live_dash.py
```
Expected: 0 failures (was 2 failed, 391 passed, 1 skipped).

Positive check — confirm the gate is what was fixed, not a coincidence:
```
cd "<repo root>/live"
uv run pytest -q tests/test_live_dash.py -k "reset_db" -v 2>&1 | grep -E "PASSED|FAILED"
```
Expected: 2 `PASSED`.

## STOP conditions

- If `reset_database` no longer consults `get_system_status()["bot_state"]` at
  line 985 (logic refactored since the stamp), STOP and report — the isolation
  patch may be insufficient; re-read the new guard.
- Do NOT "fix" this by weakening the production gate (e.g. removing the
  RUNNING check). The gate is a safety control; only the test isolation changes.
- If adding `monkeypatch` to a test signature breaks another fixture usage in
  that test, reorder the signature to `(*existing, monkeypatch)` — do not drop
  existing fixtures.

## Out of scope

No change to `live/dash/live_dash.py` production code. No change to other suites.
