# Issue #311 — Aged-out rescue checklist

- [x] Station II plan written & constraints locked
- [x] **T1** `aged_out_verdict` pure helper + the two new config knobs (RED tests first)
- [x] **T2** Open-market read + `end_ts` on `MarketEndState` (RED tests first)
- [x] **T3** `rescue_aged_out_legs` + wiring in the live loop and the shadow sweep (RED tests first)
- [x] **T4** Report the rescue reason + the aged-out settlement count (RED tests first)
- [x] Guard suites green: `test_single_buy_saver.py`, `test_auto_pairs.py`, `test_dual_stop_loss.py`, `test_market_resolution_settlement.py`, `test_shadow_run.py`, `test_shadow_exec.py`, `test_rescue_exit_report.py`, `test_trader_loop.py`, `test_milestone7_telemetry.py`, `test_exit_price_realism.py`
- [ ] Hands-on: rehearsal on the new build, dashboard 8804, `rescue_exit_report.py` shows zero aged-out settlements

Build notes (the two unplanned findings) are at the bottom of `tasks/plan.md`.
Previous checklist (#312) is preserved in git history at the PR #313 merge
(`20313bb2`).
