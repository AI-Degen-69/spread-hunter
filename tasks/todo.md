# Todo — Issue #296

- [x] T1. DB slot: role-keyed `instance_lock` table + `InstanceInUse` + context manager + seam (`core_brain/order_registry.py`)
- [x] T2. Gates: `fleet` slot in `trader_loop.run()` + `poll` slot in `order_manager.poll()`, heartbeat, eviction-stop, exit-2 mapping
- [x] T3. Tests: `tests/test_instance_lock.py` (17 passed) + targeted suites green (`test_order_registry`, `test_trader_loop`, `test_trader_loop_state`: 111 passed)
