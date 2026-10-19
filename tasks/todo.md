# Todo — Issue #699 (`i699/trigger-orphan-leg-stop-loss-and-hedge-completion-`)

- [x] **T1 [S] [Backend/Logic]:** Add `MarketBookCache.best_prices(tokens)` and `MarketStream(..., price_listener=None)` callback outside locks with error isolation + unit tests in `tests/test_market_stream.py`.
- [x] **T2 [M] [Backend/Logic]:** Create `core_brain/orphan_trigger.py` (`OrphanTriggerBoard`, `Watch`) and wire `build_watches` + `trigger_board` into `core_brain/risk_guardian.py` + unit tests in `tests/test_risk_guardian.py`.
- [x] **T3 [M] [Backend/Logic]:** Wire `guardian_wake` and `trigger_board` through `start_risk_guardian`, `_guardian_fill_tick`, `_make_subscribe_fn`, `run`, and `main` in `core_brain/trader_loop.py` + integration/concurrency tests in `tests/test_trader_loop.py` and `tests/test_lifecycle_single_executor.py`.
