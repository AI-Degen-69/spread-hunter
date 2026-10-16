# Todo - #619 [ADD] Stop-loss architecture: dynamic modes plus entry-relative emergency floor

Branch: i619/add-stop-loss-architecture-dynamic-modes-plus-entr | Issue: #619

- [x] T1.1 - Stop policy helper + config + both callers, TDD cases (0.10→0.025, $0.01 floor, higher-of ×2, 0.12/0.14 regression, refusals, endpoints) + `test_stop_loss_config.py`
- [x] T2.1 - Live-only `OrderType.FOK` in `exit_single_buy()` + `test_dual_stop_loss.py` e2e rework (boundaries, close reason, FOK accessor)
- [ ] T3.1 - Docs: README + 3 agent docs + safety walkthrough step 4 + stale-marks HTML (current-rule lines only)
- [ ] Publish: close #618 as superseded by #619 (already CLOSED — confirm at PR time)
