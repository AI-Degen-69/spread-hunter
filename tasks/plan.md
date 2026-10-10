# Plan - Issue 460: Account tile resting + held dollars

Branch: i460/account-tile-resting-and-held-dollars | Issue: #460
Tier: Small (frontend tile + poll wiring, one new subcard; no backend change) | Type: Code + UI
Stack: vanilla JS (dashboard/static/app.js), node stub-DOM harness; pytest focused files
Labels: enhancement, ready-for-agent (no quick-fix label: no gate, straight to planning)

## CodeRabbit intake (adopted/rejected)

- Adopted: frontend-only; cash = equity minus resting minus held, NULL-safe; lastState via poll + cached-nav; hero/bankroll/pill untouched; exact-value tests; no new endpoints; shadow-store operator check.
- Rejected: BUY-only filter on backend resting_committed - unasked backend change; issue says read it as-is. SELL double-count is NOTICED-BUT-NOT-TOUCHING.
- Rejected: filled_committed fallback for Held - issue order is open_committed_usd, then positions_value_usd, then unmeasured.
- Rejected: 6-task split - merged into 4 tasks below.

## Skills

test-driven-development (RED to GREEN T1-T3), frontend-ui-engineering (tile). Tagged: incremental-implementation, doubt-driven-development.

## Spec (Small tier)

Goal: TOTAL ACCOUNT VALUE card shows resting open-order dollars (state.capital.resting_committed, fresh each poll) plus held-positions dollars (open_committed_usd then positions_value_usd), cash = total minus both. Unmeasured reads as `--`, never `$0.00`.
Out of scope: trading, quoting, sweeps, event-stream, start/stop, strategy, backend math.
Acceptance: issue's 5 boxes (resting + held shown; nonzero resting with no sweep while venue stays unmeasured; bars + pct match; hero/bankroll unchanged; tests + pytest account_kpi + dashboard_server green).

## Interface contract (locked before logic)

renderBrokerPortfolioOverview(kpi, status, state): state is the /api/state payload or null; resting from state.capital.resting_committed.
renderPortfolioOverview(kpi, status, state): forwards state; keeps the missing-portfolio guard.

Resting number -> Resting shows it; Held = open_committed_usd ?? positions_value_usd ?? `--`.
Resting missing -> Resting `--`; cash `--` (unknown input, never computed).
Held unmeasured (both null) -> Held `--`; cash `--` when any leg unmeasured.
Empty registry payload (resting 0.0) -> Resting `$0.00` (measured zero); Held `--`; cash `--`.
Cash = totalVal minus resting minus held only when all three are numbers, else null. fmtUSD(null) already renders `--`. Bars follow the same numbers; unmeasured bar renders empty, never 0 pct.

## Tasks

### T1 [x] [M] [Tests] Tile contract tests first (RED)
Files: tests/test_portfolio_card_basis.py, tests/js/portfolio_card_harness.cjs (read), tests/test_account_kpi.py.
- Harness accepts input.state, emits resting/resting_pct/resting_bar_width; helper keeps state=None default.
- Cases at 85.418581/0.35/85.77: resting+held measured; resting nonzero no sweep (held --, cash --); resting missing (resting --, cash --); venue fallback held; hero/bankroll/gain unchanged every case.
- Static assert in test_account_kpi.py: no ?? 0 on the new path.
Verification: focused files; new cases MUST fail pre-change (no broker-kpi-resting, no state arg). Skill: test-driven-development.
Depends on: none.

### T2 [x] [S] [Frontend] Resting subcard markup + harness outputs
Files: dashboard/static/index.html (284-317 stack), tests/js/portfolio_card_harness.cjs.
- Copy Held subcard as Resting in orders: ids broker-kpi-resting, broker-kpi-resting-pct, bento-resting-bar; init -- / -- / empty.
- Harness stubs the 3 ids, reads input.state, emits the 3 outputs.
Verification: _strip_markup contains Resting in orders; outputs present. Skill: frontend-ui-engineering.
Depends on: T1.

### T3 [x] [M] [Frontend] Render math + poll wiring (GREEN)
Files: dashboard/static/app.js only (renderBrokerPortfolioOverview 1817-1931, renderPortfolioOverview 3948-3955, poll 7832, cached nav 596).
- Render fn takes (kpi, status, state): resting as-is from state.capital.resting_committed; held = open_committed_usd ?? account.positions_value_usd ?? null; cash = total-resting-held when all numbers else null; hero/bankroll/pill untouched.
- Thread lastState at poll 7832 + cached nav 596 (last-good, never failed null).
Verification: T1 RED cases turn green, nothing else changes. Skill: incremental-implementation.
Depends on: T2.

### T4 [x] [XS] [Tests] Focused verification
Files: none. Run pytest -q test_account_kpi + test_dashboard_server + test_portfolio_card_basis + test_registry_state (agent-run, background-only).
Verification: all green; git status shows only intended files.
Depends on: T3.

## Checkpoints

- After T1: one-line RED proof. After T3: one-line progress (tile green, hero unchanged).

## How to verify (hands-on, no pytest/gh/git)

- Shadow store only (never data/orders.db): dashboard.server --db data/X.db --port 8799, then shadow_run --minutes 5 --db data/X.db; open http://127.0.0.1:8799.
- Bids resting: Resting above \.00; no sweep: Held --; all measured: resting+held+cash = hero, bars match pct; hero + Starting Bankroll unchanged.

## Rejected scope (do not resurface)

- BUY-only backend filter; filled_committed fallback; trading/quoting/sweeps/stream/start-stop/strategy/new endpoints; second CodeRabbit loop.

## NOTICED-BUT-NOT-TOUCHING

- resting_committed sums resting SELLs too (registry_state.py:263-264); cash may double-count when a SELL rests over held shares. Future issue.
