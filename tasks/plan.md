# Plan: Issue #251 — Real values in the Quant Risk grid; stale-backend distinct from unmeasured

Size: **Standard** (5 files + tests, one architectural decision: payload versioning).
Task type: **Code** (backend statistics + frontend rendering + copy contract).

## Context

Owner directive (2026-09-20, issue comment): compute the five missing metrics for
real instead of rendering them `unmeasured`. Today `var_95_usd`, `cvar_95_usd`,
`kelly_fraction`, `half_kelly` and `payoff_ratio` exist only as render fallbacks
in `dashboard/static/app.js:2212-2219` — every run shows a fabricated
`$0.00` / `0.0%` / `0.00x`. All inputs already exist inside
`compute_trade_analytics` (`core_brain/kpi.py:300-458`).

## Formulas (locked)

- `payoff_ratio = avg_win_usd / abs(avg_loss_usd)` — same value as the existing
  `risk_reward_ratio`; NULL when no loss exists to measure against.
- `kelly_fraction = p − q / b` where `p = win_rate`, `q = 1 − p`,
  `b = payoff_ratio`. May be negative (reads as "no bet"). NULL when `b` is NULL
  (no losses yet) or `win_rate` is NULL.
- `half_kelly = kelly_fraction / 2` (NULL alongside).
- `var_95_usd` / `cvar_95_usd` — historical VaR on the per-close `return_pct`
  distribution: 5th percentile with linear interpolation; CVaR = mean of the
  tail at or below that percentile. Converted to dollars via the mean
  `cost_basis` of the measured closes, reported as a positive loss magnitude.
  NULL below `MIN_RISK_SAMPLE = 20` measured returns — a 5% tail needs at least
  one observation, and 20 is the smallest sample that provides one.
- `payload_version = KPI_PAYLOAD_VERSION` (integer, bumped by hand when new
  payload fields ship) added to the top-level `report()` envelope.

## Tasks

### Task 1 — Backend: real risk metrics [Code]
- **Files:** `core_brain/kpi.py`, `tests/test_trade_analytics.py`
- Add `payoff_ratio`, `kelly_fraction`, `half_kelly`, `var_95_usd`,
  `cvar_95_usd` to the `compute_trade_analytics` return dict, computed from the
  already-validated `wins` / `losses` / `return_pcts` / `_measured_pairs`.
  NULL when unmeasurable, never zero. Add `MIN_RISK_SAMPLE = 20` with a comment.
- **Skill:** test-driven-development
- **Verification:** new tests in `tests/test_trade_analytics.py` pin exact
  values on a known close set, plus the null cases (no losses → payoff/kelly
  NULL; < 20 measured returns → VaR/CVaR NULL). RED first.

### Task 2 — Backend: payload version marker [Code]
- **Files:** `core_brain/kpi.py`, `tests/test_trade_analytics.py`
- Add `KPI_PAYLOAD_VERSION = 251` module constant (comment: bump when payload
  fields ship) and `"payload_version": KPI_PAYLOAD_VERSION` in the top-level
  `report()` return dict, sibling of `trade_analytics`.
- **Verification:** test asserts `report(...)["payload_version"] ==
  KPI_PAYLOAD_VERSION`.

### Task 3 — Frontend: render real values, unmeasured when NULL [Code]
- **Files:** `dashboard/static/app.js` (`renderQuantRiskGrid`, ~2212-2256)
- Remove every fabricated fallback (`'$0.00'`, `'0.0%'`, `'0.00x'`) for VaR,
  CVaR, Kelly, Half-Kelly, Payoff. NULL/absent renders `unmeasured`, following
  the `Median: unmeasured` pattern at `app.js:2275-2278`. A real VaR keeps its
  `negative`-coloured loss styling.
- **Skill:** frontend-ui-engineering
- **Verification:** harness-driven test (Task 5) asserts tile text.

### Task 4 — Frontend: stale-backend note via payload_version [Code]
- **Files:** `dashboard/static/app.js` (`pollStatus` 4973-5044, top-level state),
  `dashboard/static/index.html`, `dashboard/static/styles.css`
- `const EXPECTED_PAYLOAD_VERSION = 251;` + `let payloadVersionWarned = false;`.
  In `pollStatus()`, after `if (kpi) lastKpi = kpi;`: stale when
  `payload_version` is absent or `< EXPECTED_PAYLOAD_VERSION` → show
  `#quant-stale-note` (amber tokens, hidden unless `.show`) and one
  `console.warn` (flag-gated). Not stale → hide. Note copy: "Dashboard backend
  is older than this page — restart the dashboard to see all metrics."
- **Verification:** harness test asserts note shown for old/missing version and
  hidden for current; copy assertions pin the note id/class/copy.

### Task 5 — Tests: harness + pinned rendering [Code]
- **Files:** `tests/js/sign_colours_harness.cjs`,
  `tests/test_negative_values_read_as_losses.py`, `tests/test_analytics_api.py`
- Harness: scrape the VaR, Kelly and Payoff tile texts/classes and the stale
  note visibility (drives `renderQuantRiskGrid` directly; pollStatus logic is
  pinned via copy assertions). Keep all 12 existing assertions unchanged.
- `test_negative_values_read_as_losses.py`: new scenario with the five fields
  absent/null → tiles read `unmeasured`; with real values → tiles show them.
- `test_analytics_api.py`: copy contracts — no fabricated-zero fallbacks near
  `var_95_usd`/`kelly_fraction`/`payoff_ratio` reads; `EXPECTED_PAYLOAD_VERSION`
  and the stale-note id/class/copy present in `app.js` / `index.html` /
  `styles.css`. No assertion deleted.
- **Verification:** `python -m pytest -q tests/test_analytics_api.py
  tests/test_negative_values_read_as_losses.py tests/test_trade_analytics.py`

### Task 6 — Docs: restart flow [Docs]
- **Files:** `docs/agents/first-run.md` (near "### 5 · Dashboard")
- Note: `core_brain/*` / `dashboard/server.py` changes need a dashboard
  restart; a stale backend shows the "backend older than page" note in the
  Quant Risk grid plus a one-time console warning.
- **Verification:** doc review only; no copy assertion required.

## Constraints

See `CONSTRAINTS.md` → "Issue #251" section. Headlines: existing formulas
(`expectancy_usd`, `mean_return_pct`, `ci90_lower_pct`, Sharpe/Sortino,
`profit_factor`, `risk_reward_ratio`) MUST NOT change; new fields are
display-only and never feed a gate; no new dependencies; no assertion deleted.

## How to verify (operator, hands-on)

1. Restart the dashboard (`.\scripts\spread-hunter-menu.ps1` → dashboard option,
   or stop + host again) so the new backend serves the page.
2. Open http://127.0.0.1:8799 → Analytics → Quant Risk grid: with 75 closes the
   VaR/CVaR, Kelly and Payoff tiles show real numbers, not `$0.00`/`0.0%`/`0.00x`.
3. Open the same page against an old backend (skip the restart): the amber
   "backend older than page" note appears above the grid, with one console
   warning — no flood.

