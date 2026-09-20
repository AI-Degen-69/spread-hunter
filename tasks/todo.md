# Issue #252 — Execution checklist

- [ ] Task 1 — Backend: DB-anchored `starting_capital` + `starting_capital_ts` + version bump (RED tests first)
- [ ] Task 2 — Backend: `equity_series` starts at the anchor
- [ ] Task 3 — Frontend: headline/pill/tile read the DB anchor (EXPECTED_PAYLOAD_VERSION 252)
- [ ] Task 4 — Frontend: chart START point + baseline + x-label at anchor (ISO ts, Start fallback)
- [ ] Task 5 — Tests: version pin + sweep (`tests/test_analytics_api.py:165` etc.)
- [ ] Review — Run targeted checks after all review fixes
- [ ] CI — Confirm full regression suite through GitHub CI before merge
