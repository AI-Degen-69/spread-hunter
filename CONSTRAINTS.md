# CONSTRAINTS.md — Quality Bar & Guardrails (Issue #699)

**Issue:** `#699`  
**Branch:** `i699/trigger-orphan-leg-stop-loss-and-hedge-completion-`

---

## 1. Zero Regressions & Targeted Verification

- All targeted test suites covering modified and adjacent modules must pass before push:
  ```bash
  python -m pytest -q tests/test_market_stream.py tests/test_single_leg_lifecycle.py tests/test_risk_guardian.py tests/test_trader_loop.py tests/test_lifecycle_single_executor.py tests/test_hard_stop_fresh_exit.py
  ```
- `tests/test_single_leg_lifecycle.py` and `tests/test_hard_stop_fresh_exit.py` must pass **unchanged** as regression gates.
- Every new behavior in `core_brain/market_stream.py`, `core_brain/orphan_trigger.py`, `core_brain/risk_guardian.py`, and `core_brain/trader_loop.py` must have deterministic unit/integration tests using events, fake clocks, and call counts (no flaky wall-clock sleep assertions).

## 2. Performance & Concurrency Thresholds

- **Zero I/O on hot path:** `OrphanTriggerBoard.observe(prices)` runs on the `market-stream` WebSocket callback thread and must execute zero SQLite reads/writes and zero HTTP/REST calls (in-memory dict lookups and float comparisons only, `< 1 ms`).
- **Lock ordering & deadlock freedom:** `MarketStream._on_message` must release both `MarketBookCache._lock` and `MarketStream._lock` before invoking `price_listener`.
- **Single-executor serialization:** All guardian passes (whether timer-driven, fill-driven, or reactive-wake-driven) must serialize through `lifecycle_mutation_guard` (`_LIFECYCLE_LOCK`) with peak concurrency `== 1`.
- **Burst coalescing:** A 2.0-second per-condition mute (`MUTE_DURATION_SEC = 2.0`) prevents consecutive rapid crossing ticks from re-setting `guardian_wake` for the same condition within the mute window.

## 3. Protected Files (Must Not Modify)

- `data/orders.db` (production registry — read-only, never write or delete in tests or code).
- `core_brain/single_buy_saver.py`
- `core_brain/single_leg_lifecycle.py`
- `core_brain/config.py`
- `core_brain/order_registry.py`
- `core_brain/order_manager.py`
- `core_brain/unhedged_stop_loss.py`
- `core_brain/shadow_run.py` and `core_brain/shadow_exec.py`
- `tests/test_single_leg_lifecycle.py`

## 4. Anti-Cheat & Dependencies

- **No test weakening:** Never skip (`@pytest.mark.skip`), xfail, delete, or weaken existing assertions.
- **No silent exception swallowing:** Listener errors in `MarketStream` must be logged and emitted as `market_stream_error`.
- **No new external dependencies:** Use only the Python standard library and existing repo packages.
