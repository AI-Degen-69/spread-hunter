# Issue #251 — Execution checklist

- [x] Task 1 — Backend: `payoff_ratio`, `kelly_fraction`, `half_kelly`, `var_95_usd`, `cvar_95_usd` in `compute_trade_analytics` (RED tests first)
- [x] Task 2 — Backend: `KPI_PAYLOAD_VERSION` + `payload_version` in `report()` envelope
- [x] Task 3 — Frontend: real values in `renderQuantRiskGrid`; `unmeasured` when NULL; no fabricated zeros
- [x] Task 4 — Frontend: `EXPECTED_PAYLOAD_VERSION` stale check in `pollStatus` + `#quant-stale-note` (html/css/js)
- [x] Task 5 — Tests: harness scraping + new scenarios + copy contracts
- [x] Task 6 — Docs: restart-flow note in `docs/agents/first-run.md`
- [ ] Review — Run targeted checks after all review fixes
- [ ] CI — Confirm full regression suite through GitHub CI before merge
