# Implementation Plan — Refine Market Filter Kanban Phrasing & Card Metrics (#441)
Branch: i441/refine-market-filter-kanban-phrasing | Issue: #441

## Summary
Refine Dashboard Tab 3 ("Market Filter Pipeline") Kanban board:
1. Rephrase Stage 7 from "7. Horizon & Income Gate" / "TEST: HORIZON & INCOME" to "7. Horizon Gate" / "TEST: HORIZON", dropping the obsolete income floor.
2. Rebrand Stage 8 hero from "TEST: PAIR MERGE ARBITRAGE · Pair Cost ≤ $0.995 -> $1.00 USDC" to "QUALIFIED FLEET · Passed all screening gates · Quoting on venue".
3. Strip redundant metrics (P&L, Spread percentage, Est Ret) from the passed and eligible market cards.

## Intake & Seam Reconciliation
- **Seams verified in code:**
  - `dashboard/static/app.js:5655`: `STAGE_DEFS` contains `{ key: 'horizon', name: '7. Horizon & Income Gate', cls: 'rejected' }`.
  - `dashboard/static/app.js:5807-5816`: `getStageHero` generates `TEST: HORIZON & INCOME` and `TEST: PAIR MERGE ARBITRAGE`.
  - `dashboard/static/app.js:6171-6178`: `card-metrics-grid` renders Fills, P&L, 24h Vol, Spread, Days, Est Ret.
  - `dashboard/static/app.js:6192`: Runner-up cards render `Est Ret: ... | Vol: ...`.
  - `tests/test_dashboard_server.py:1561-1569`: asserts `TEST: HORIZON & INCOME` and `income > $0.00/day`.
- **Protected Files (MUST NOT MODIFY):**
  - `scripts/filter_markets.py`
  - `scoring/selector.py`
  - `core_brain/order_manager.py`
  - `core_brain/order_registry.py`
  - `core_brain/markets.py`
  - `data/orders.db`

## Task Decomposition

### Task 1: Rephrase Horizon Gate & Rebrand Qualified Fleet Column Hero [x]
- **Task ID:** TASK-1
- **Size:** S
- **Domain Tag:** `[Design/UI]` + `[UX / Copy]`
- **Helper Skill:** `frontend-ui-engineering`
- **Depends on:** None
- **Files:** `dashboard/static/app.js`, `tests/test_dashboard_server.py`
- **Description:**
  - In `dashboard/static/app.js`:
    - Rename `STAGE_DEFS` entry for `horizon` to `7. Horizon Gate`.
    - In `getStageHero('horizon')`, change `param` to `TEST: HORIZON` and `value` to `≤ ${Number(horizonDays).toFixed(1)} days`.
    - In `getStageHero('passed')`, change `param` to `QUALIFIED FLEET` and `value` to `Passed all screening gates · Quoting on venue`.
    - Clean up unused `rewardIncome` and `spreadIncome` variables from `getStageHero`.
  - In `tests/test_dashboard_server.py`:
    - Update `test_app_js_states_the_payout_floor_per_market_source` to `test_app_js_states_the_horizon_gate_rule`: assert `TEST: HORIZON` present, `income >` absent, and `TEST: PAIR MERGE ARBITRAGE` absent.
- **Verification:** `python -m pytest -q tests/test_dashboard_server.py -k "horizon or payout"`

### Task 2: Remove Redundant Card Metrics from Passed & Eligible Cards [x]
- **Task ID:** TASK-2
- **Size:** S
- **Domain Tag:** `[Design/UI]`
- **Helper Skill:** `frontend-ui-engineering`
- **Depends on:** TASK-1
- **Files:** `dashboard/static/app.js`, `tests/test_dashboard_server.py`
- **Description:**
  - In `dashboard/static/app.js`:
    - In `.passed-card` metrics grid, remove `P&L`, `Spread`, and `Est Ret`.
    - Keep only essential non-redundant fields: `Fills: <span class="card-fills">${esc(m.fills || 0)}</span>`, `24h Vol: <span style="color:var(--text-primary)">${volStr}</span>`, `Days: <span style="color:var(--text-primary)">${daysStr}</span>`.
    - In eligible runner-up cards, remove `Est Ret:` so it renders `Vol: ${fmtUSD(el.volume || 0)}${el.days_to_resolve ? ' · ' + esc(el.days_to_resolve) + 'd' : ''}`.
  - In `tests/test_dashboard_server.py`:
    - Add unit test verifying that passed cards in `app.js` do not include `P&L:`, `Spread:`, or `Est Ret:` in their markup.
- **Verification:** `python -m pytest -q tests/test_dashboard_server.py`
