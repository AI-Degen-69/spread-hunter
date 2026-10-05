# Constraints & Quality Guardrails — Issue #371

Branch: `i371/multi-arm-tournament-ui-port-isolation` | Issue: #371

## Quality & Tests
- **Zero regressions**: Targeted suites `tests/test_live_dash.py`, `tests/test_shadow_tournament.py`, `tests/test_dashboard_run_switcher.py`, `tests/test_scan_state_shadow.py`, `tests/test_per_run_shadow_ring.py`, and `tests/test_live_state_language.py` must pass cleanly.
- **Anti-cheat**: No skipped tests, no deleted assertions, no suppressions or linter silencing.
- **No new external dependencies**: Use Python standard library, existing FastAPI/starlette stack, and existing vanilla JS only.

## Safety & Invariants
- **No production DB write**: `data/orders.db` must never be touched, modified, or targeted by the tournament launcher or shadow processes.
- **Port isolation**: Tournament launcher must probe and verify that assigned ports (8801+) are free before spawning dashboard servers; live default port 8799 must never be hijacked.
- **State isolation**: Each arm runs in its own process with its own designated SQLite scratch file and per-run ring/heartbeat artifacts.
- **Fail-safe fallback**: Non-tournament runs continue to display and switch by their base file names without breaking changes.
