# Plan: Issue #252 — Anchor Portfolio chart to the run's DB start

Size: **Standard** (3 files + tests, single architectural decision: DB anchor vs session snapshot).
Task type: **Code + Design/UI** (backend anchor selection + frontend chart rendering).

## Context
`dashboard/server.py:1216` writes the wallet at stack start to `runtime/processes.json`; `app.js:1316` `portfolioEquity()` prefers that session snapshot over `kpi.py`'s own `portfolio.starting_capital`. On `data/01_shadow_12-09_00-58.db` (run `shadow-01`) that makes a +$8.41 / +15.68% run render as a $2.66 decline from a $64.70 START that never belonged to the run. The run's true start is the newest `account_marks` row at or before the run's first activity (`$53.631665` @ `2026-09-11T21:58:29.910Z`).

## Spec (see SPEC.md)
Goals, acceptance, edge cases and out-of-scope copied to `SPEC.md` for the Standard size. Headline: every start figure on the Portfolio card — value and timestamp — comes from the same DB anchor, never from `/api/system/status`.

## Tasks

### Task 1 — Backend: DB-anchored starting capital + timestamp [Code] [x]
- **Files:** `core_brain/kpi.py` (`starting_capital` block 1753-1792, `list_runs()` first_ts), `tests/test_account_kpi.py`
- **Build:** newest `account_marks` row at or before the active run's `first_ts`; fallback in order run's earliest mark → store's earliest mark → `_CFG.bankroll_usd`. Expose `portfolio.starting_capital_ts` (mark's `ts` or `null`). Bump `KPI_PAYLOAD_VERSION` 251→252.
- **Skill:** test-driven-development
- **Verification:** RED test asserting `53.631665` + `2026-09-11T21:58:29.910224Z` on the shadow store; degenerate-store test asserts bankroll fallback with `null` ts.

### Task 2 — Backend: equity_series starts at the anchor [Code] [x]
- **Files:** `core_brain/kpi.py` (equity_series 1830-1860), `tests/test_portfolio_overview.py`
- **Build:** `running_equity = starting_capital` already exists — verify it now uses the DB anchor and that the series' first point's timestamp matches `starting_capital_ts`.
- **Skill:** test-driven-development
- **Verification:** harness test asserts first equity point value+ts equals the anchor; windowed frames (1D/1W/1M) still pin the anchor at left edge.

### Task 3 — Frontend: headline + pill read the DB anchor [Design/UI] [x]
- **Files:** `dashboard/static/app.js` (`portfolioEquity()` 1316, hero pill 1344, KPI-grid tile 3236, `EXPECTED_PAYLOAD_VERSION` 71), `tests/js/portfolio_card_harness.cjs`, `tests/test_portfolio_card_basis.py`
- **Build:** `portfolioEquity()` prefers `kpi.portfolio.starting_capital` (and its `starting_capital_ts`) over `status.starting_capital`; hero pill denominator and `#broker-starting-cap` / KPI-grid tile read that same figure. Bump `EXPECTED_PAYLOAD_VERSION` 251→252 together with backend.
- **Skill:** frontend-ui-engineering
- **Verification:** harness asserts `test_the_chart_baseline_matches_the_headlines_starting_capital` now passes inverted (matches DB anchor, not session snapshot); expects `+$8.41 (+15.68%)` and `Starting Bankroll: $53.63` on the fixture.

### Task 4 — Frontend: chart START point + baseline + x-label [Design/UI] [x]
- **Files:** `dashboard/static/app.js` (`buildBrokerEquitySeries()` 1450-1478, chart geometry 1480-1562), `dashboard/static/index.html` (#broker-starting-cap)
- **Build:** synthetic `Start` point at anchor value with real ISO timestamp label (`brokerPointLabel`); dashed `START: $…` baseline and x-axis label sit at anchor; fallback label `Start` only when ts is null; no `NaN`/`undefined` in SVG for degenerate store.
- **Skill:** frontend-ui-engineering
- **Verification:** browser harness asserts left-edge label is ISO timestamp before first trade, `START` line at anchor, `Current` at right edge; degenerate-store render has no NaN.

### Task 5 — Tests: version pin + no-regression sweep [Code] [x]
- **Files:** `tests/test_analytics_api.py:165`, `tests/test_portfolio_overview.py`, `tests/test_account_kpi.py`
- **Build:** pinned version assertion updated to 252; keep all existing `portfolio`/`equity_series` assertions except the inverted baseline test.
- **Skill:** test-driven-development
- **Verification:** `python -m pytest -q tests/test_portfolio_card_basis.py tests/test_portfolio_overview.py tests/test_account_kpi.py tests/test_analytics_api.py` green.

## Improvement proposal (adopted by default)
Keep the `Venue wallet` row visible as a secondary figure but never as the card's denominator — the issue already bans changing its copy, so surfacing the gap as labelled information avoids reintroducing the three-figure confusion while preserving the wallet top-up signal.

