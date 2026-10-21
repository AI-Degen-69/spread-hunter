Branch: i730/record-stop-slippage | Issue: #730

# Todo — #730 Record stop-slippage on hard-stop exits

- [ ] T1 Registry: nullable `closes.submitted_floor`/`fill_source` + append-only `hard_stop_refusals` (DDL, migration, CloseRecord, log_close, write/read methods; reset untouched)
- [ ] T2 Shadow label: `_sell_into_bids` → `(filled, avg, took_shares)`; SELL response `fill_source`; 9 test unpack sites mechanical `, _`
- [ ] T3 Exit pass-through: `_exit_fill_source` helper; one `_record_exit_close` call gains floor+label; additive return keys; hard-stop wrap → refusal/error rows, re-raise
- [ ] T4 Pure summary: `stop_slippage_summary` + always-present `build()` section; `HARD_STOP_SELL_REASONS` reused (function-level import)
- [ ] T5 KPI wiring: run-scoped refusals into `build()`; version 259→260 (kpi.py, app.js, pinned test literal); read-only `python -m core_brain.kpi --db` CLI
- [ ] T6 Dashboard card: `renderStopSlippage` + `#stop-slippage-readout` host; `figure()` unmeasured vs `0.00`
- [ ] T7 Proof grows per task in `tests/test_stop_slippage.py`; acceptance matrix green (RED→GREEN each task)
- [ ] T8 Demo seeds (at-floor, below-floor, assumed, refusal) + N3 ledger row marked addressed

Checkpoint gates: CP1 after T2 · CP2 after T4 · CP3 after T5 · CP4 after T8
