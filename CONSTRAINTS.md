# Constraints & Quality Guardrails — Issue #357

## Quality Boundaries
- **Zero regressions**: All existing tests in `tests/test_unified_universe.py` and `tests/test_cycle_stream.py` must pass.
- **Targeted scope**: Only touch `scripts/filter_markets.py` and `tests/test_unified_universe.py`.
- **Untouched paths**:
  - Do NOT touch `core_brain/quotes.py`, `core_brain/config.py`, `scoring/config.py` (quote gate `min_t_remaining_sec` is out of scope).
  - Do NOT touch `core_brain/markets.py` or `scoring/markets.py`.
  - Do NOT modify or write to production store `data/orders.db`.
- **Anti-cheat**: No disabling tests, skipping assertions, or mocking out validation.
- **Dependencies**: Zero new dependencies.
- **Performance**: Early refusal in `evaluate()` must prevent tape and order book network requests for expired markets.
