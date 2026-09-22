# Constraints: Issue #240 — End-of-window proximity gate

## Quality & Tests
- Zero regressions: full `python -m pytest -q` must remain green.
- Targeted suites must pass 100%: quote/decision tests (`tests/test_price_risk_widen_override.py` pattern), new endgame regression + config-validation + cadence tests.
- Anti-Cheat: strictly forbid skipping tests (`@pytest.mark.skip`), deleting assertions, or bypassing linters.
- New/changed behavior needs a test that fails without the change (gate-off posts the S&P pair; gate-on refuses/deepens it).

## Risk & Behavior Boundaries
- Gate defaults OFF (`enforce_endgame_gate=False`); no live behavior change until explicitly enabled.
- Unknown cadence (`None`) fails open — gate must not fire.
- Light-side balancing quotes are never blocked (`risk.naked_side` check).
- No signature changes to `decide_quotes` / `evaluate_market_quote` callers; no new timestamp source besides `LiveMarket.t_remaining()`; no new external dependencies.
- `window_frac=None` for graduated markets — never invent a window origin.
- `deepen` path reuses the existing offset pipeline + `max_spread_from_mid` clamp; no parallel pricing path.
- No `rehearsal.is_rehearsal()` gating on the new knobs (risk-tightening applies everywhere).

## Constraints: Issue #242 — Portfolio equity chart and header
- Zero regressions: focused dashboard tests covering modified files must pass; GitHub CI runs the full regression suite on push.
- New chart behavior must be covered by a test that fails when synthetic curve data is restored.
- Preserve the existing `broker-starting-cap`, `broker-venue-wallet-row`, `broker-venue-wallet`, and `broker-venue-wallet-note` ids and wallet rendering behavior.
- Do not change backend KPI calculations or add an endpoint; consume the existing top-level `kpi.equity_series` payload.
- Do not add external dependencies or change the chart timeframe/control contract.
- Do not skip or weaken existing assertions, and do not use fake/synthetic equity points in the production chart path.

## Constraints: Issue #248 — Expectancy vs mean-return audit
- Zero regressions: focused four-file selection (`tests/test_trade_analytics.py`, `tests/test_mean_pnl_ci.py`, `tests/test_seed_preview_fixture.py`, `tests/test_analytics_api.py`) must pass; full `python -m pytest -q` stays with GitHub CI.
- New/changed behavior needs a test that fails without the change (RED before GREEN): companion fields, regression fixture, explainer copy.
- Anti-Cheat: strictly forbid skipping tests, deleting assertions, or bypassing linters.
- Formulas `expectancy_usd`, `mean_return_pct`, `ci90_lower_pct` MUST NOT change; gate verdict (`passed`) provably identical. New fields are display-only companions.
- Missing or non-positive `cost_basis` stays excluded from percent calculations and renders as unmeasured — never a fabricated zero.
- Pinned dashboard tile labels ("Average Profit Per Close", "Mean Return Per Trade") stay unchanged; only explainer copy and sublabels added.
- No new external dependencies; no live-trading, execution, risk-cap, or threshold changes.

## Constraints: Issue #251 — Real Quant Risk values + stale-backend note
- Zero regressions: focused selection (`tests/test_trade_analytics.py`, `tests/test_negative_values_read_as_losses.py`, `tests/test_analytics_api.py`) must pass; full `python -m pytest -q` stays with GitHub CI.
- New/changed behavior needs a test that fails without the change (RED before GREEN): payoff/kelly/VaR values + null cases, unmeasured rendering, stale-note visibility.
- Anti-Cheat: strictly forbid skipping tests, deleting assertions, or bypassing linters.
- Existing formulas (`expectancy_usd`, `mean_return_pct`, `ci90_lower_pct`, Sharpe/Sortino, `profit_factor`, `risk_reward_ratio`) MUST NOT change. New fields are display-only companions and never feed a gate.
- Every new metric is NULL when unmeasurable — never a fabricated zero. VaR/CVaR NULL below `MIN_RISK_SAMPLE = 20` measured returns.
- `payload_version` is a hand-bumped integer; bump it whenever new payload fields ship.
- No new external dependencies; no live-trading, execution, risk-cap, or threshold changes.

## Constraints: Issue #254 - Speed up Windows pytest CI job
- Zero regressions: the full suite (all 145 test files, 2193 tests) still runs and passes on both Ubuntu and Windows; `gh pr checks` green is the merge gate.
- Anti-Cheat: strictly forbid skipping tests, deleting assertions, lowering coverage, or weakening the timing-sensitive `stale` assertion in `tests/test_seed_preview_fixture.py` to make CI faster.
- No product-code changes: only `.github/workflows/tests.yml`, CI tooling under `scripts/ci/`, and `pytest.ini` if needed may change; `core_brain/*`, `dashboard/*`, and test files stay untouched.
- No new dependencies of any kind — not even dev-only (no `pytest-xdist` without explicit operator approval); the speedup must come from job structure, not new packages.
- Performance target: Windows wall time under ~4 minutes (baseline ~9-14 min on PR #253) while Ubuntu stays green.

## Constraints: Issue #252 - Portfolio equity chart anchored to DB run-start
- Zero regressions: focused suites `tests/test_portfolio_card_basis.py`, `tests/test_portfolio_overview.py`, `tests/test_account_kpi.py`, `tests/test_analytics_api.py` must pass; full `python -m pytest -q` stays with GitHub CI.
- New/changed behavior needs a test that fails without the change (RED before GREEN): DB anchor value + timestamp, version bump, chart baseline, hero pill denominator.
- Anti-Cheat: strictly forbid skipping tests, deleting assertions, weakening `test_the_chart_baseline_matches_the_headlines_starting_capital`, or suppressing linters.
- The card describes the run: every start figure (`starting_capital`, `starting_capital_ts`, chart Start point, START baseline, pill %, Starting Bankroll tile) derives from the same DB anchor; session snapshot `runtime/processes.json` `starting_account_value` never feeds the card.
- No trading, quoting, sizing, or registry-schema changes; no new endpoint; no new frontend dependencies; `Venue wallet` row stays as-is; timeframe semantics (1D/1W/1M/ALL) keep pinning run-start at left edge.
- `KPI_PAYLOAD_VERSION` and `EXPECTED_PAYLOAD_VERSION` must be bumped together (251 → 252); degenerate store (no marks) falls back to `_CFG.bankroll_usd` with `starting_capital_ts = null` and renders without NaN.

## Constraints: Issue #259 — Equity tooltip trade facts
- Zero regressions: focused suite `tests/test_portfolio_card_basis.py` must pass; full `python -m pytest -q` stays with GitHub CI.
- New/changed behavior needs a test that fails without the change (RED before GREEN): harness `tooltip_html` pins all four rows on a fixture close, plus `--` fallbacks for missing/zero `cost_basis` and missing `hold_seconds`.
- Anti-Cheat: strictly forbid skipping tests, deleting assertions, or bypassing linters.
- Backend: no new DB queries, no schema changes, no new helpers in `order_registry.py`. Reuse `_resolve_market_meta()` (at most once per market) and the run-filtered in-memory `quotes`. `hold_seconds` is `None` when no quote exists or delta is negative. Missing/non-positive `cost_basis` renders unmeasured — never a fabricated 0%.
- Frontend: conditional-copy passthrough (fixtures without new fields keep the old point shape); all strings through `esc()`; percent via `fmtPct()`, hold via the order-age formatter; badge reuses the `gateBadge()` pattern and `.param-badge` style; `tooltipW` changes only if new rows overflow centering.
- No trading, quoting, sizing, or registry-schema changes; no new endpoint or frontend dependency; out of scope stays out (#252 anchor, #257 geometry, styling beyond content rows).

## Constraints: Issue #276 — strategy_explainer DESIGN.md tokens
- Zero regressions: focused dashboard suites covering touched files must pass; full `python -m pytest -q` stays with GitHub CI.
- New/changed behavior needs a test that fails without the change (RED before GREEN): static test pinning no-gradient/no-glow, DESIGN.md token values, Big Shoulders headers.
- Anti-Cheat: strictly forbid skipping tests, deleting assertions, or bypassing linters.
- UI-only: only `dashboard/static/strategy_explainer.html` plus the new test may change; no content/script/backend change; info copy stays identical.
- No new dependencies; fonts only extend the existing Google Fonts request.

## Constraints: Issue #277 — 44px touch targets + focus-visible
- Zero regressions: focused dashboard suites covering touched files must pass; full `python -m pytest -q` stays with GitHub CI.
- New/changed behavior needs a test that fails without the change (RED before GREEN): static test pinning >=44px control heights, compact marked meta chips, signal-color focus ring.
- Anti-Cheat: strictly forbid skipping tests, deleting assertions, or bypassing linters.
- UI-only: only `dashboard/static/styles.css` plus the new test may change; no layout reflow (wrap stays), no backend change; mobile 360px still wraps cleanly.

## Constraints: Issue #278 — status strip overflow cue + keyboard scroll
- Zero regressions: focused dashboard suites covering touched files must pass; full `python -m pytest -q` stays with GitHub CI.
- New/changed behavior needs a test that fails without the change (RED before GREEN): static test pinning tabindex, no-shrink desktop row, `.is-scrollable` cue, focus ring, JS toggle, intact mobile wrap.
- Anti-Cheat: strictly forbid skipping tests, deleting assertions, or bypassing linters.
- UI-only: only `dashboard/static/index.html` (one attr), `styles.css` (cue rules), `app.js` (toggle + export) plus the new test may change; no control moves, no backend change; mobile wrap at 900px unchanged.

## Constraints: Issue #264 — Dashboard input lag (tab clicks freeze 2-4s)
- Zero regressions: focused selection (`tests/test_dashboard_server.py`, `tests/test_dashboard_snapshot_cache.py`, `tests/test_dashboard_narrow_viewport.py`) must pass; full `python -m pytest -q` stays with GitHub CI.
- New/changed behavior needs a test that fails without the change (RED before GREEN): e.g. a static test pinning that hidden-tab renders are skipped or deferred and that tab switching paints synchronously.
- Anti-Cheat: strictly forbid skipping tests, deleting assertions, or bypassing linters.
- Poll content MUST NOT change: state, KPIs, markets, orders/trades, and screener still refresh with the same data and endpoints; no endpoint contract changes, no new dependencies.
- Performance bar: a tab click visibly switches panels immediately even mid-poll; rapid clicks never queue and replay late.
- Frontend-only: no backend, strategy, quoting, sizing, or registry changes unless measurement proves the backend is the blocker.

