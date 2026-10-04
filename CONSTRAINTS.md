# Constraints & Quality Guardrails — Issue #356

## Quality Boundaries
- **Zero regressions**: All existing tests in `tests/test_cycle_stream.py`, `tests/test_run_attribution.py`, and `tests/test_trader_loop.py` must pass.
- **Targeted scope**: Only touch `core_brain/cycle_stream.py`, `core_brain/trader_loop.py`, `core_brain/shadow_exec.py`, `tests/test_cycle_stream.py`, `tests/test_run_attribution.py`, and documentation files in `docs/issues/`.
- **Untouched paths**:
  - Do NOT modify production `data/orders.db`.
  - Do NOT touch `CYCLE_INTENT_KEEP_ROWS` or retention mechanism.
  - Do NOT write test events to `live/runtime/cycle_events.jsonl`.
  - Do NOT change exception types or signatures of public APIs.
- **Anti-cheat**: No disabling tests, skipping assertions, or mocking out validation.
- **Dependencies**: Zero new dependencies.
- **Telemetry safety**: Telemetry operations remain fire-and-forget; warnings go to `sys.stderr`.
