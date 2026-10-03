# Constraints: Issue #352 — Shadow resume vs the killed loop's instance lock

Branch: i352/shadow-resume-reports-success-while-the-new-loop | Issue: #352

## Quality & Tests
- **Zero regressions**: `tests/test_instance_lock.py` stays green. Full-repo suite
  stays with GitHub CI.
- **Every changed behaviour needs a test that fails without the change.** The new
  Python helper ships with `tests/test_release_instance_lock.py`; the PowerShell
  wiring is verified by an operator resume smoke (no pytest harness runs `.ps1`).
- **Anti-cheat**: no skipped tests, no deleted assertions, no new suppressions.
- **No new external dependencies**: standard library only (`sqlite3`, `argparse`,
  `json`, `time`). No new config keys, no new env vars.

## Behaviour Boundaries
- **Resume path only.** `Resume-ShadowRun` in `scripts/spread-hunter-menu.ps1`
  plus one new helper script. The lock protocol (`instance_lock`, adopt-if-stale),
  the 300 s stale threshold, and every live-trading path are OUT OF SCOPE.
- **Fail closed, never force.** The helper releases a `fleet` row only when its
  holder PID is verified dead by the caller; a live holder, a mismatched holder,
  or an unreadable table refuses. No `--force` flag, no blind delete.
- **`data/orders.db` is never touched.** The helper refuses the production
  registry path the same way the menu already does.
- **Success line means alive.** "Rehearsal loop running" prints only after the
  health check passes; otherwise a failure line prints.

## Performance Budgets
- Resume gains at most ~20 s wall time (stop-verify + release + startup check).
- Health check: one process probe + one small-file read, no polling loops
  longer than 15 s total.

## Out of Scope (record, do not fix)
- `instance_lock` protocol or `INSTANCE_LOCK_STALE_MS` changes.
- Heartbeat-file retention/pruning and ring rotation policy.
- Issue #351 (zero-fill diagnosis) — separate issue, separate branch.
