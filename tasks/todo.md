# Issue #252 — Execution checklist

- [x] Task 1 — Backend: DB-anchored `starting_capital` + `starting_capital_ts` + version bump (RED tests first)
- [x] Task 2 — Backend: `equity_series` stacks on the anchor (pinning test green)
- [x] Task 3 — Frontend: headline/pill/tile read the DB anchor (EXPECTED_PAYLOAD_VERSION 252)
- [x] Task 4 — Frontend: chart START point shows anchor ISO ts, Start fallback, no NaN
- [x] Task 5 — Tests: version pin 252 + sweep (72 passed)
- [ ] Review — Run targeted checks after all review fixes
- [ ] CI — Confirm full regression suite through GitHub CI before merge
