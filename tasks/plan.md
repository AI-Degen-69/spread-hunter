# Plan — #457 Single state-driven start/stop button with graceful stack lifecycle

Branch: i457/single-state-driven-start-stop-button-with-graceful-stack | Issue: #457
Size: Standard | Type: Code (Backend/Logic + Frontend/UI + Debug)
Stack: Python (dashboard/server.py), pytest focused files; vanilla JS (dashboard/static/app.js,
index.html) + node stub-DOM harness.
Labels: bug, ready-for-agent (no quick-fix label: no gate, straight to planning)

## CodeRabbit plan intake

- **Adopted:** the 3-task vertical split (STOP lifecycle / partial START / single toggle), the
  shared private stop helper, `start_new_session=True` on the three master Popen calls only
  (Linux/macOS), process-group detection via `os.getpgid(pid) == pid`, the deadline budget
  (<30s to stay inside the ops-lock window), and the full test-case list.
- **Rejected:** the "own-group guard" (`os.getpgid(pid) == os.getpgrp()`) — a group launched by
  this dashboard on a POSIX host always differs from its own group in practice, and the check
  adds surface without changing the default outcome; the standalone "legacy masking" STOP test
  is folded into the normal registry test instead. Per-service launches, `_start_stack_commands`,
  SYNC, RESET, watchdog, and lock design stay untouched.
- **[UNVERIFIED] resolved from code:** `renderServiceHeader`/`pollStatus` are NOT in
  `module.exports` → the toggle harness drives state changes through the click handler + a
  fetch-mocked status poll, not by calling those functions directly. `cancel_all` patch target
  resolved: `core_brain.order_manager.cancel_all`. `prototype.js` / test references to old button
  IDs: none exist (grep clean) — but `tests/test_dashboard_server.py:1448-1449` asserts both old
  IDs are in `top_meta`; that assertion is updated to expect `#btn-master-toggle`.
  **Confirmed real:** the `saved_procs` use-before-assignment — read at server.py:1794/1810/1824
  (partial-stack `else` branches) while assigned at server.py:1835.

## Locked constraints (CONSTRAINTS.md)

- Zero regressions: `tests/test_dashboard_server.py`, `tests/test_live_dash.py`,
  `tests/test_service_toggles.py`, `tests/test_runtime_paths.py`, `tests/test_pid_recycling.py`
  stay green.
- Anti-cheat: no skipping/disabling tests, no deleting assertions, no suppressing linters.
- Dependencies: none new. Node already a test dependency (guarded by `NODE is None` skip).
- Tests never start real processes, send signals, sleep real time, or contact the venue.
- Protected (do not modify): SYNC VENUE, RESET, per-service start/stop endpoints, `stop_bot`
  wrapper, `_start_stack_commands`, `core_brain/runtime_paths.py`, `scripts/global_stop_loss.py`,
  ops-lock design, quoting/sizing/strategy, `data/orders.db`.
- Total STOP time stays < 30s (ops-lock takeover window).

## Dependency graph

```
T1 (stop helper + start_new_session) ─┬─> T2 (partial START rollback uses helper)
T3 (frontend toggle) ─ independent ──> all → done
```
T1 first (riskiest: signal/reap/verification logic). T2 depends on the T1 helper for rollback.
T3 is independent of the backend and runs in parallel.

## Tasks

- [x] **T1** [M] [Backend/Logic] — Graceful, verified, honest STOP. ✅ a22d9fd
  - Files: `dashboard/server.py` (3 master Popen calls gain `start_new_session=True` on POSIX;
    new private `_stop_services` helper; rewrite `_stop_bot_locked`), `tests/test_dashboard_server.py`,
    `tests/test_runtime_paths.py`.
  - Helper contract: input `(name, pid, started_at)` items + one deadline; outcomes
    `not_running|stopped|forced|still_running` + `detail`; SIGTERM→poll (reap via `os.waitpid`,
    `WNOHANG`, catch `ChildProcessError`)→SIGKILL escalation; POSIX group-signal when
    `os.getpgid(pid)==pid`, else single process; Windows `taskkill /T /PID` then `/F /T /PID`,
    both with timeout, return code captured; identity via `_is_pid_alive`; clock/sleep via `time`
    module (patchable). Deadline budget: 10s polite + 5s force + 2×5s taskkill = 25s.
  - `_stop_bot_locked`: keep unreadable-registry refusal and missing-registry success
    ("Stack already stopped"); pass every dict entry with a `pid` (legacy screener/engine/fleet
    mapped via existing `service_entry` naming, reported under current names); REWRITE the
    registry instead of unlinking — keep `still_running` entries + all non-service metadata
    (`starting_account_value`, `reset_at`); add `services` map; `ok` true only when nothing is
    `still_running`; messages: "Stack stopped", "Stack stopped (decide force-killed)",
    "Stack already stopped", "STOP incomplete: decide still running (PID 5001). Registry kept
    for retry."; code comments for every acceptance gap.
  - Tests (all mock; no real signals/sleeps): graceful group exit (no SIGKILL), escalation +
    reap, verified-down failure (registry kept, capital kept), signal-error detail in `detail`,
    legacy `fleet` entry under `decide`, Windows return-code 128 + timeout in detail, already
    stopped / missing registry, deadline sum < 30, RESET 409 without `cancel_all` (patch
    `core_brain.order_manager.cancel_all`). Update `test_stop_clears_a_readable_registry`
    (file retained, no service entries). Keep lock + unreadable-registry tests unchanged.
  - Verify: `python -m pytest -q tests/test_dashboard_server.py tests/test_runtime_paths.py tests/test_live_dash.py`
  - Depends on: none

- [ ] **T2** [M] [Backend/Logic] — Partial START fills only the missing services.
  - Files: `dashboard/server.py` (`start_bot`), `tests/test_dashboard_server.py`.
  - Load `saved_procs` inside the lock BEFORE any alive check (fixes the NameError at
    1794/1810/1824). Keep refusals: non-production DB, UNKNOWN status, unreadable registry,
    held lock, and "Stack already running" when all three run or RUNNING-with-no-per-service-state.
    Launch only missing services (with the T1 `start_new_session` setting); keep running
    services' entries unchanged; capital rule unchanged for full fresh START, partial START keeps
    existing `starting_account_value` and skips `_capture_starting_capital`; add `launched`,
    `reused`, `status` to response. Rollback: on a later launch or registry-write failure, stop
    ONLY the services this call launched via the T1 helper; never stop reused services; never
    write rolled-back entries; lock release stays in `finally`.
  - Tests: partial start (registry has running `query`; expect no NameError, 2 Popen calls with
    `scripts.filter_loop` + `core_brain.trader_loop`, no `order_manager`, `launched==["filter",
    "decide"]`, `reused==["query"]`, query entry unchanged, capital untouched, `start_new_session`
    on POSIX); full-stack refusal (no Popen); rollback on 3rd-launch OSError (helper gets only
    pids 6001/6002, no registry entries, no `.bot_start.lock`); rollback with a reused service
    (helper gets only the launched one, query entry remains). Existing START tests unchanged.
  - Verify: `python -m pytest -q tests/test_dashboard_server.py tests/test_service_toggles.py`
  - Depends on: T1 (rollback uses the helper)

- [x] **T3** [M] [Frontend/UI] — One state-driven toggle with visible feedback.
  - Files: `dashboard/static/index.html`, `dashboard/static/app.js`,
    `tests/js/master_toggle_harness.cjs` (new, follows `start_toggle_harness.cjs` pattern),
    `tests/test_dashboard_server.py` (harness runner + `top_meta` ID assertion update),
    `README.md` (stack paragraph + gap list).
  - Replace `#btn-master-start` + `#btn-master-stop` with one native `#btn-master-toggle`
    (`btn-start-run`, aria "Start bot execution stack"). `renderServiceHeader`: RUNNING (incl.
    partial) → `btn-stop-run`/"STOP RUN"/aria stop/`dataset.action="stop"`; STOPPED/UNKNOWN →
    start look; `isStarting`/`isStopping` override → disabled + `aria-busy="true"` + spinner +
    "STARTING…"/"STOPPING…"; skip null service entries; keep the amber "STACK STOPPING" pill.
    One handler (`dataset.wired`): ignore clicks while in-flight/disabled; set flag; paint busy
    at once; POST via `controlFetch` to `/api/system/start|stop`; append backend `message` to the
    ticker via `appendTickerEvent` (no console-only failures); list non-`stopped`/`not_running`
    outcomes for STOP; apply payload `status` to `lastStatus`; clear flag + re-render in
    `finally`. Add a lifecycle counter; `pollStatus` captures it at start and skips the master
    header render when it changed mid-flight (stale-poll guard).
  - Harness cases (drive the click handler; fetch-mocked; skip when node missing): RUNNING, partial
    RUNNING, STOPPED, UNKNOWN, null service entry, double-click → exactly one POST + busy
    attrs + pill, refused START message in ticker, partial STOP message + "decide" in ticker,
    HTTP 500 non-JSON → ticker error line, stale poll resolved after a click keeps the new look.
    Update `tests/test_dashboard_server.py:1448-1449` to expect `#btn-master-toggle`.
    `test_shadow_view_toggle_never_prompts_or_posts_a_start` stays green.
  - Verify: `python -m pytest -q tests/test_dashboard_server.py` (includes the harness runner)
  - Depends on: none (backend contract from T1/T2 responses; render tolerates absent `services`)

## One improvement proposal (evidence-based)

The issue says STOP should "report per-service outcomes honestly" but never specifies what
happens to the registry file on a partial stop. Evidence — current code does
`procs_file.unlink()` (server.py:1936), which deletes `starting_account_value` with it, and the
issue itself demands "`starting_account_value` survives a stop". **Classification: simplification /
edge-case hardening → adopt-by-default** (folded into T1): rewrite the registry keeping survivors
+ metadata instead of unlinking it.

## Checkpoints

- ✅ T1 done (a22d9fd): STOP honest end to end — 11 mock-verified cases green
  (graceful group exit, escalation+reap, still_running keeps registry, signal
  error detail, legacy fleet→decide, Windows taskkill/forced/timeout,
  already-stopped, budget<30s, RESET 409). Neighbours: 68 passed, 1 skipped.

- After T1: STOP is honest end to end (mock-verified graceful/escalation/failure paths).
- After T2: partial START no longer raises `NameError`; rollback is scoped.
- After T3: single toggle renders all states; old IDs gone; harness green.
