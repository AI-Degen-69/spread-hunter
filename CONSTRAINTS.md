# Constraints: Issue #294 — Click-to-sort Orders & Trades columns

## Quality & Tests
- Zero regressions: `python -m pytest -q tests/test_orders_trades_table.py` must stay 100% green (baseline verified: 62 passed, 2026-09-28). Full-repo sweep stays with GitHub CI on push.
- New sort behavior needs tests that are RED against untouched `app.js` and GREEN after; each added assertion must fail without its change.
- Anti-cheat: strictly forbid skipping tests, deleting or weakening assertions, or bypassing linters.
- Every new pure helper must be reachable from tests: add it to the `module.exports` block (`app.js:5424-5431`).

## Behavior Boundaries
- **Backing data is the sort unit, never the DOM `<tr>`.** Orders and OPEN POSITIONS emit one row per leg with `rowspan` pair cells (`app.js:4257-4258`, `:4393-4399`); CLOSED TRADES reuses `marketRowPairHtml` which emits a main row plus an optional expanded sub-row (`app.js:4553`, `:4573-4580`). Sorting rendered rows would split pairs and strand every `rowspan`. Sort the groups/entries first, then build HTML.
- **`app.js` line numbers in the issue text are stale** (issue says `otHeadHtml` 4009 / `renderOrdersTrades` 4403 / exports 5392; actual 4037 / 4432 / 5408-5431). Verified anchors are the ones named in this file.
- **No-sort output must be byte-identical to today.** Active Markets stays `quotes_count` desc then title (`app.js:4105-4106`); CLOSED TRADES stays `realized_pnl` desc (`:4310`); Orders stays `groupOrdersByPair` order (`:4198`, `b.newest - a.newest`); Positions stays `heldMarketEntries` order (`:4282`, `total_cost` desc).
- **Numeric columns sort from the underlying value, not the rendered string.** `fmtPrice`/`fmtUSD`/`fmtCompactUSD`/`fmtStopwatch` are display formatters (`:4051-4066`); `$1,000.00` must outrank `$95.00`, 9 shares must rank below 10, `1.5d` above `10.0d`.
- **Unmeasured cells (`--`) rank last in both directions** so a column of `--` never outranks a measured value. Unmeasured sources: `queueAheadByOrder` misses (`:4266`), `age_sec` null (`:4267`), `days_to_resolve` null (`:4124`), null quote price.
- **`Market` sorts by `localeCompare` on the market title**, never on markup or `condition_id`.
- **`title="RESTING…"` must stay inside the `<th>` opening tag.** `test_active_markets_status_header_explains_the_vocabulary` (`tests/test_orders_trades_table.py:930-945`) reads `head.split("<th")[1:]` and asserts `title=` is absent from the cell body. A sort button must not push the tooltip off the `<th>`.
- **The `.ot-market-head` width floor must keep matching** (`styles.css:1353`); the sort affordance must not remove the class from the first `<th>`.
- Accessibility: the control is a real `<button type="button">`, the sorted `<th>` carries `aria-sort="ascending"|"descending"`, the arrow is not the only direction signal, and CSS adds hover + `:focus-visible` using existing `DESIGN.md` tokens (`--border-strong`, `--text-secondary`, `--text-primary`) — no new colours, no glow.
- No new external dependencies. No change to `OT_COLUMNS` label lists or to `OT_STORAGE_KEY` view persistence semantics.

# Constraints: Issue #296 — Port single-instance ownership lock to core_brain

## Quality & Tests
- Zero regressions: `tests/test_order_registry.py` and `tests/test_trader_loop.py` must pass 100%; full `python -m pytest -q` stays with GitHub CI on push.
- New/changed behavior needs a test that fails without the change (RED before GREEN): second same-role holder refused, pair coexists, eviction-stop.
- Anti-Cheat: strictly forbid skipping tests, deleting assertions, or bypassing linters.
- Tests use `tmp_path` DBs only (`tests/test_order_registry.py:62` pattern); no test ever writes into live `data/` or `run/`.

## Risk & Behavior Boundaries
- `InstanceInUse` must NOT subclass `ReconcileInProgress` (`order_manager.py:2246` per-cycle skip must never swallow a startup refusal).
- The designed fleet+poll pair on one DB must keep working; only a second holder of the SAME role is refused.
- Read-only consumers (`dashboard/server.py`, ro readers) never take the lock.
- No new external dependencies; refusal exits non-zero (2) with holder + age on stderr.

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

## Constraints: Issue #291 — shadow-03 depth-bar trial on its own feed
- Zero regressions: focused suites (`tests/test_filter_markets_out_dir.py`, `tests/test_filter_loop.py`, `tests/test_shadow_run.py`, `tests/test_trader_loop.py`, `tests/test_menu_shadow_trial.py`) plus `tests/test_trial_readiness.py` (untouched) must pass; full `python -m pytest -q` stays with GitHub CI.
- New/changed behavior needs a test that fails without the change (RED before GREEN): out-dir isolation, flag forwarding, feed override, menu launch/resume contracts.
- Anti-Cheat: strictly forbid skipping tests, deleting assertions, or bypassing linters.
- Shipped bars immutable: `core_brain/config.py` ($500 depth bar), `core_brain/market_feed.py`, `core_brain/trial_readiness.py`, dashboard feed/KPI sources stay untouched.
- Baseline byte-identical: without new flags, ranker command, loop logs, shadow default feed, and no-manifest resume behave exactly as today; shared `runtime/` artifacts never receive trial writes.
- No new external dependencies; no live execution; no volume trial; no ladder work.
- While 01/02 are live: no fresh start (menu 4), no stop without a run ID.

## Constraints: Issue #293 — Symmetric paired share sizes
- Zero regressions: focused selection (`tests/test_live_quotes.py`, `tests/test_paired_inventory_accounting.py`, `tests/test_rc_fixes.py`, `tests/test_trader_loop.py`) must pass; full `python -m pytest -q` stays with GitHub CI.
- New/changed behavior needs a test that fails without the change (RED before GREEN): skewed-price pair clamps to the min with a reason note; below-floor pair dropped with a reason; deficit-rebalancing leg untouched.
- Anti-Cheat: strictly forbid skipping tests, deleting assertions, or bypassing linters.
- Harmonize ONLY flat-inventory two-sided pairs (`risk.naked_side(inv) is None`): unbalanced deficit sizing (`size_for` strict-paired path, `quotes.py:288-294` heavy-side block) stays untouched; emergency-hedge crossed intents are never flat, never touched.
- No signature changes to `decide_quotes` / `_require_two_sided` callers; no sizing change in the rest-under-ask path (fixed `cfg.quote_shares` is already symmetric); no new dependencies; no venue, order-manager, or UI changes.

## Constraints: Issue #264 — Dashboard input lag (tab clicks freeze 2-4s)
- Zero regressions: focused selection (`tests/test_dashboard_server.py`, `tests/test_dashboard_snapshot_cache.py`, `tests/test_dashboard_narrow_viewport.py`) must pass; full `python -m pytest -q` stays with GitHub CI.
- New/changed behavior needs a test that fails without the change (RED before GREEN): e.g. a static test pinning that hidden-tab renders are skipped or deferred and that tab switching paints synchronously.
- Anti-Cheat: strictly forbid skipping tests, deleting assertions, or bypassing linters.
- Poll content MUST NOT change: state, KPIs, markets, orders/trades, and screener still refresh with the same data and endpoints; no endpoint contract changes, no new dependencies.
- Performance bar: a tab click visibly switches panels immediately even mid-poll; rapid clicks never queue and replay late.
- Frontend-only: no backend, strategy, quoting, sizing, or registry changes unless measurement proves the backend is the blocker.

## Constraints: Issue #295 — Venue category instead of Uncategorized
- Zero regressions: focused selection (`tests/test_orders_trades_table.py`,
  `tests/test_market_meta.py` (new), `tests/test_portfolio_overview.py`,
  `tests/test_registry_state.py`, `tests/test_unified_universe.py`) must pass;
  full `python -m pytest -q` stays with GitHub CI.
- New/changed behavior needs a test that fails without the change (RED before GREEN):
  filter extraction shapes, resolver precedence + keyword cases, four-view display,
  KPI/registry parity, both operator examples.
- Anti-Cheat: strictly forbid skipping tests, deleting assertions, or bypassing linters.
- Category is display-only: no selection/ranking input, no `identity_allowed` change,
  no `data/orders.db` backfill, no new dashboard filter UI.
- Venue verbatim wins: a feed label is never overridden by the keyword fallback;
  keyword vocabulary stays the two display labels (`E-Sports`, `Politics`) and must
  NOT import private regexes from `scoring/selector.py`.
- Frontend shape frozen: `OT_COLUMNS`, empty-row colspans, expanded-row `colspan`,
  `rowspan` pairing, and Data & Markets output unchanged; STATUS-header tooltip test
  (`test_active_markets_status_header_explains_the_vocabulary`) passes unmodified.
- `tests/test_portfolio_overview.py` lines 259-277 stay green without changes.
- No new external dependencies; no live execution; never run the filter CLI
  (even `--dry-run`) — it writes snapshots; no test writes into live `data/` or `run/`.

