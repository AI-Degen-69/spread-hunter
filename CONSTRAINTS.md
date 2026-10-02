# Constraints: Issue #339 — Ladder rungs priced off live book

## Quality & Tests
- Zero regressions: `tests/test_ladder_quotes.py`, `tests/test_ladder_shadow_rehearsal.py`,
  `tests/test_ladder_live_books_trial.py`, `tests/test_ladder_shadow.py`,
  `tests/test_shadow_run.py` stay 100% green. Full-repo sweep stays with GitHub CI on push.
- New behaviour requires tests RED against untouched code and GREEN after; each added
  assertion must fail without its change.
- Anti-cheat: strictly forbid skipping tests, deleting or weakening assertions, or
  bypassing linters. Tests use `tmp_path` fixtures; no test writes into live `data/` or `run/`.
  `data/orders.db` is never touched by a report, a test, or the runner.

## Behaviour Boundaries
- **Derive rungs from the live book**: Rungs are priced relative to each leg's `best_bid`
  (`price = round(best_bid - (i - 1) * tick, 4)` for `i` in `1..cfg.ladder_rungs`), not a static 0.50.
- **Completable pair gate**: A market whose book cannot support a rung under `max_pair_cost`
  (via `risk.completable_pair_block(cfg, price, hedge_ask)`) posts nothing, rather than an
  off-market quote far behind the touch.
- **Report rungs truthfulness**: `build_report` falls back to reading distinct quoted prices from the
  store's `orders` rows when `rungs` is empty/omitted, ensuring reports reflect real prices.
- **Paper only, no signer, zero venue writes.** No changes to live signing or production defaults.
- **No new external dependencies.**

---

# Constraints: Issue #333 — Full 4-hour live-books ladder trial (paper only, no signer)

## Quality & Tests
- Zero regressions: `tests/test_ladder_shadow_rehearsal.py`,
  `tests/test_ladder_shadow.py`, `tests/test_ladder_quotes.py`,
  `tests/test_ladder_config.py`, `tests/test_ladder_discovery.py`,
  `tests/test_ladder_exit.py`, `tests/test_shadow_run.py` stay 100% green.
  Full-repo sweep stays with GitHub CI on push (merge gate).
- The single code change (report `issue` tag parameter) needs a test RED
  against untouched code and GREEN after. Live network is never touched by a
  test — books/feeds are stubbed; T2's live run is operator-hands-on, not pytest.
- Anti-cheat: strictly forbid skipping tests, deleting or weakening assertions,
  or bypassing linters. Tests use `tmp_path` fixtures; no test writes into live
  `data/` or `run/`. `data/orders.db` is never touched by a report, a test, or
  the runner.

## Behaviour Boundaries
- **Paper only, no signer, zero venue writes.** The runner must never
  construct a signing client; `run_shadow` builds the signer-less shadow client
  itself. Only public reads (books, resolution state) may touch the network.
- **Trial-only config.** `ladder_mode=True` and the funded `ladder_budget_usd`
  live in the trial invocation, never in production `MakerConfig` defaults
  (`ladder_mode=False`, `ladder_budget_usd=0.0` stay).
- **Fresh `--db` per trial.** A rerun on a populated store counts the previous
  trial's fills as orphans (guard in `run_trial` refuses populated stores).
- **Committable artifact is `docs/runs/`, not `reports/`.** `reports/` is
  gitignored, so the JSON stays machine-local and the probe-vs-live write-up
  (with embedded conservation numbers) is committed under `docs/runs/`
  following the existing dated-run convention.
- **Untouched:** screener modules, Dynamic Caps, dashboard UI, production
  registry (`data/orders.db` refused by name), `fetch_live_market` single-pair
  path, probe/shadow collectors. No go-live decision and no recommendation —
  that is a separate issue.
- **No new external dependencies** without explicit approval.

---

# Constraints: Issue #331 — Live-books ladder shadow trial (paper only, no signer)

## Quality & Tests
- Zero regressions: `tests/test_ladder_shadow_rehearsal.py`,
  `tests/test_ladder_shadow.py`, `tests/test_ladder_quotes.py`,
  `tests/test_ladder_config.py`, `tests/test_ladder_discovery.py`,
  `tests/test_ladder_exit.py`, `tests/test_shadow_run.py` stay 100% green.
  Full-repo sweep stays with GitHub CI on push (merge gate).
- New behaviour needs tests RED against untouched code and GREEN after; each added
  assertion must fail without its change. Live network is never touched by a test —
  books/feeds are stubbed; T3's live run is operator-hands-on, not pytest.
- Anti-cheat: strictly forbid skipping tests, deleting or weakening assertions, or
  bypassing linters. Tests use `tmp_path` fixtures; no test writes into live `data/`
  or `run/`. `data/orders.db` is never touched by a report, a test, or the runner.

## Behaviour Boundaries
- **Paper only, no signer, zero venue writes.** The trial runner must never
  construct a signing client; `run_shadow` builds the signer-less shadow client
  itself. Only public reads (books, resolution state) may touch the network.
- **Trial-only config.** `ladder_mode=True` and the funded `ladder_budget_usd`
  live in the trial runner invocation, never in production `MakerConfig` defaults
  (`ladder_mode=False`, `ladder_budget_usd=0.0` stay).
- **Extend the rehearsal, don't fork it.** Reuse the locked `--out` JSON schema
  (plus a live-books source tag); no second report format.
- **Untouched:** screener modules, Dynamic Caps, dashboard UI, production registry
  (`data/orders.db` refused by name), `fetch_live_market` single-pair path.
  No go-live decision and no recommendation — that is a separate issue.
- **No new external dependencies** without explicit approval.

---

# Constraints: Issue #311 — Aged-out one-sided legs are never revisited (fail-closed gap)

## Quality & Tests
- Zero regressions: `tests/test_single_buy_saver.py`, `tests/test_auto_pairs.py`,
  `tests/test_dual_stop_loss.py`, `tests/test_market_resolution_settlement.py`,
  `tests/test_shadow_run.py` stay 100% green. Full-repo sweep stays with GitHub CI
  on push (merge gate).
- New behaviour needs tests RED against untouched code and GREEN after; each added
  assertion must fail without its change. The new harness is
  `tests/test_aged_out_rescue.py` (pure verdict + full pass on a synthetic store).
- Anti-cheat: strictly forbid skipping tests, deleting or weakening assertions, or
  bypassing linters. Tests use `tmp_path` fixtures; no test writes into live `data/`
  or `run/`. `data/orders.db` is never touched by a report or a test.

## Behaviour Boundaries
- **`pairs_exit_window_sec` stays 900.0 and keeps its meaning** (a discovery
  filter). It is never raised to close this gap; the fix is a separate arm.
- **New defaults only.** `enable_aged_out_rescue = True` and
  `aged_out_rescue_lead_sec = 900.0` are additions; no existing config default may
  change (`should_exit()`, `single_buy_max_loss_pct` 0.10,
  `single_buy_max_loss_usd` 0.045, grace defaults, `max_pair_cost`, the dynamic
  risk caps, the direction gate).
- **The in-window route order is frozen**: complete → adverse-drift →
  hold-in-grace → grace-expiry. The new arm acts only on a pair whose dated last
  fill is strictly older than the window; an undated fill is never acted on, in
  either arm.
- **Fail closed, both directions.** An unreadable end, or a market the venue no
  longer lists as open, means no action: the leg stays naked and the read is
  retried next rotation. A deadline is never invented and a blind close is never
  sent. A venue-closed market is left to the resolution/settlement path.
- **Never sell twice.** The arm reuses the existing close-coverage guard
  (`last_fill_ms` vs `latest_close_ms` per condition) so a leg already closed by
  merge, single-buy exit or settlement is not sold again.
- A close row is written only after a successful venue sale; a refusal is reported
  per pair and retried, never forced. Per-pair failures are isolated.
- The deadline read is the venue's own market state (public GET, read-only).
  `fetch_market_end_state`'s `closed=true` semantics are not changed — a
  `closed=true` read returns zero rows for a still-open market, which classifies as
  `unreachable` and would silently disable this arm.
- Sports `endDate` is not the end of trading (verified: `endDate` a week out while
  `gameStartTime` is hours past; #312 established the kickoff case). A market whose
  stated end has passed while the venue still accepts orders is treated as its
  closing phase, not as "resolved".
- No new external dependencies; no schema change; no live execution during build.

# Constraints: Issue #306 — Rescue-exit forensics + reason instrumentation

## Quality & Tests
- Zero regressions: focused suites `tests/test_rescue_exit_report.py` (new),
  `tests/test_single_buy_saver.py`, `tests/test_dual_stop_loss.py`,
  `tests/test_auto_pairs.py`, `tests/test_shadow_run.py` must stay 100% green.
  Full-repo sweep stays with GitHub CI on push (merge gate).
- New behaviour needs tests RED against untouched code and GREEN after; each added
  assertion must fail without its change.
- Anti-cheat: strictly forbid skipping tests, deleting or weakening assertions,
  or bypassing linters.

## Read-only guarantees
- `scripts/rescue_exit_report.py` opens every SQLite store read-only
  (`file:...?mode=ro`, the `grace_sweep_report.py::_ro` pattern). A test asserts
  both stores are unchanged after a report run (schema_version + row counts).
- `data/**` is never committed. The findings doc carries aggregate output only.

## Behavior Boundaries (the fail-closed order is frozen)
- Do NOT change `should_exit()`, `single_buy_max_loss_pct` (0.10),
  `single_buy_max_loss_usd` (0.045), grace defaults, `pairs_exit_window_sec` (900),
  or the route order: complete → adverse-drift → hold-in-grace → grace-expiry.
- `exit_single_buy` gains `reason: Optional[str] = None`; stray-guard and any other
  caller without a reason must keep working unchanged.
- The close reason is written only AFTER a successful venue sale — never before,
  never on a refusal. The close is still the only ledger record of the sell.
- `closes.reason` is added by the existing `PRAGMA table_info` + `ALTER TABLE`
  migration pattern (tx_hash/run_id precedent, order_registry.py:492-494); old
  stores must still open and old close rows must read as NULL reason.
- The report labels Question 1 results "sampled upper bound, not an executable
  fill" or emits "unanswerable from this store" when no opposite-leg samples exist.
- Classification is `late_trigger` / `gapped` / `unresolved`; NULL `best_bid`
  rows and missing windows resolve to `unresolved`, never guessed.
- n=4 cannot calibrate a gate: the report states this; no policy change ships.
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

# Constraints: Issue #312 — Market universe is empty / sports endDate misread + submarket admission trial

## Quality & Tests
- Zero regressions: `tests/test_unified_universe.py`, `tests/test_movement_gate.py`,
  `tests/test_pre_start_gate.py`, `tests/test_family_probe.py` stay 100% green.
  Full-repo sweep stays with GitHub CI on push (merge gate).
- New behaviour needs tests RED against untouched code and GREEN after; each added
  assertion must fail without its change. The pre-start suite (`test_pre_start_gate.py`)
  pins its own gate — do not weaken it to fix the resolve side.
- Anti-cheat: strictly forbid skipping tests, deleting or weakening assertions,
  or bypassing linters. Tests use `tmp_path` fixtures; no test writes into live
  `data/` or `run/`. Never run the filter CLI (even `--dry-run`) — it writes snapshots.

## Behavior Boundaries
- **Started ≠ resolved.** Fix the sports `endDate`-as-kickoff misread using real
  venue signals (`closed`, `acceptingOrders`, resolution state). A market that is
  `closed=False` and still accepting orders is not resolved, whatever `end_date_iso` says.
- **Fail closed on ambiguity.** A market whose resolution state cannot be read is
  refused, never assumed live. Unknown start time keeps its current fail-open
  behaviour (`pre_start` returns False) — that precedent is pinned by
  `test_an_unknown_start_time_is_not_pre_start` and stays.
- **No in-play policy change.** Admitting live sports past the horizon gate is fixing
  a misclassification, not opening in-play quoting. `identity_allowed` keeps refusing
  genuine submarkets; the submarket family stays refused until the trial (ADDENDUM)
  runs on its own feed with a pre-registered success criterion.
- Do NOT touch `should_exit()`, risk caps, grace, `pairs_exit_window_sec`, rescue
  route order, or `identity_allowed`'s blocked-keyword arm.
- `_cause()` bucketing (`filter_markets.py:1023`) must keep collapsing to the same
  gate cards — new reason text must still bucket identically; existing bucket tests stay green.
- No new external dependencies; no live execution.

# Constraints: Issue #314 - D12 submarket admission trial (trial-only machinery)

## Quality & Tests
- Zero regressions: `tests/test_paired_depth.py`, `tests/test_unified_universe.py`,
  `tests/test_family_probe.py`, `tests/test_filter_markets_publish_json.py`,
  `tests/test_market_feed.py`, `tests/test_shadow_run.py`,
  `tests/test_paired_shadow_report.py`, `tests/test_filter_loop.py` stay green.
  Full-repo sweep stays with GitHub CI on push (merge gate).
- Every named test is written first and fails before implementation; each added
  assertion must fail without its change. Tests use `tmp_path` fixtures; no test
  writes into live `data/` or `run/`. `data/orders.db` is never touched.
- Anti-cheat: strictly forbid skipping tests, deleting or weakening assertions,
  or bypassing linters.

## Behavior Boundaries
- **Prohibited files (never modified):** `scoring/selector.py` (incl. shipped
  `identity_allowed` default), `core_brain/single_buy_saver.py`, risk caps
  (`order_risk_pct`, `naked_risk_pct`, `bankroll_ceiling_pct`) and `max_pair_cost`
  in `core_brain/config.py`, `core_brain/market_resolution.py`,
  `core_brain/order_registry.py` schema, `data/**` incl. `data/orders.db`.
- **Trial isolation:** `--paired-admission` requires an isolated `--out-dir` and
  refuses the shared runtime dir, `--dry-run`, volume/spread trials, legacy
  rewards, and all paired-depth flags; requires a complete Gamma listing and pins
  the volume gate to the permanent threshold. Unflagged ranker output is
  byte-identical (no `trial_arm`/`family`/`admission_role` tags).
- **Depth contract frozen:** depth bundle format, depth cutoff checks ($500/$250),
  depth verdict path, and depth report fixtures are unchanged.
- **Grouping unit is the event cluster** (`gamma-event:<id>`, slug fallback);
  `family_key` is a descriptive tag only. No new in-progress gate: a member is
  unavailable only if an existing gate refuses it. Mid check stays strict
  (`0.20 < mid < 0.80`). Admission emits `reject` (not `retain_control`); single-buy
  loss uses fill notional (`price * size`) for admission only.
- **Analyzer is read-only and fail-closed:** reads run-owned markouts through
  `_read_only`; never substitutes zero/raw for missing marks; `inconclusive` on
  any limitation; no PnL uplift, no paired-variance prior in the verdict.
- **No launch in this pipeline:** build + short rehearsal only. The 100-hour trial
  launch is an operator decision recorded in the pre-reg doc.

# Constraints: Issue #49 — Short-window ladder (probe-first, shadow-only)

## Quality & Tests
- Zero regressions: focused suites covering touched files (`tests/test_shadow_exec.py`,
  `tests/test_cycle_stream.py`, quote/config suites) must stay 100% green; full
  `python -m pytest -q` stays with GitHub CI on push (merge gate).
- New/changed behavior needs tests RED against untouched code and GREEN after;
  each added assertion must fail without its change. The four proof tests are
  placement, fill-sim, one-leg exit, and the off-test.
- Anti-cheat: strictly forbid skipping tests, deleting or weakening assertions,
  or bypassing linters. Tests use `tmp_path` fixtures; no test writes into live
  `data/` or `run/`. `data/orders.db` is never touched.

## Behavior Boundaries (operator direction 2026-09-30)
- **Separate ladder allocation.** The ladder does NOT share spread-hunter
  Dynamic Caps accounting; per-rung sizing and the blended-residue check run
  against the ladder budget. No existing cap default may change.
- **Shadow-only.** No live execution during build; the screener
  (`scripts/filter_markets.py`, `runtime/markets.json` rules) is untouched —
  the ladder reaches crypto series through the shadow seam only.
- **Off means identical.** With `ladder_mode` off, quoting output is
  byte-identical to today; `fetch_live_market` and the rollover loop unchanged.
- **Probe gates build.** Ladder code tasks do not start on a probe no-go;
  Q1–Q3 (rung count, exit window, order lifetime) are answered by the probe's
  CIs, with one-leg residues scored at real resolution outcomes, never zero.
- No new external dependencies.

# Constraints: Issue #325 — Build the ladder path (gated on shadow go)

## Quality & Tests
- Zero regressions: focused suites covering touched files
  (`tests/test_shadow_exec.py`, `tests/test_single_buy_saver.py`,
  quote/config suites) stay 100% green; full `python -m pytest -q` stays
  with GitHub CI on push (merge gate).
- New/changed behavior needs tests RED against untouched code and GREEN
  after; each added assertion must fail without its change. The four proof
  tests are placement, fill-sim, one-leg exit, and the off-test.
- Anti-cheat: strictly forbid skipping tests, deleting or weakening
  assertions, or bypassing linters. Tests use `tmp_path` fixtures; no test
  writes into live `data/` or `run/`. `data/orders.db` is never touched.

## Behavior Boundaries (carry #49 direction + #324/#326 findings)
- **Separate ladder allocation.** Per-rung sizing and the blended-residue
  check run against the ladder budget, never Dynamic Caps. No existing cap
  default may change (`max_pair_cost`, grace, windows, route order frozen).
- **Off means identical.** With `ladder_mode` off, quoting output is
  byte-identical to today; `fetch_live_market`, the rollover loop, and the
  screener are untouched — crypto series enter through the shadow seam only.
- **One-shot rungs.** A filled rung retires; an exit close retires the
  market. Ladder legs never re-post under an exited `pair_id` (structural
  side of the #326 trap); the #326 netting guards the live carry path.
- **Shadow-only.** No live execution during build; no new external deps.
- **Gate was GO.** BTC + ETH 5-min probe verdicts go (shape 2 / exit_60);
  order lifetime stays configurable (probe left it open).

# Constraints: Issue #323 — ETH 5-min collect + probe (verify-and-close)

## Quality & Tests
- Zero regressions: focused suites `tests/test_ladder_probe.py` and
  `tests/test_ladder_tape_collect.py` stay 100% green as a health check;
  full `python -m pytest -q` stays with GitHub CI on push (merge gate).
- Anti-cheat: strictly forbid skipping tests, deleting or weakening assertions,
  or bypassing linters. No test writes into live `data/` or `run/`.
  `data/orders.db` is never touched.

## Behaviour Boundaries
- **No code change, no venue calls.** This is record verification only:
  the collect + probe already ran (2026-09-30), the verdict is posted,
  and the scripts are byte-unchanged since. Re-collecting tapes is out.
- **No new external dependencies; no live execution.**
- The stale prior-session branch (`i323/d11-followup-collect-probe-eth-5-min-series`)
  is never merged (it predates #324/#325); its prune was deferred because it
  is checked out in a sibling worktree — recorded in `tasks/plan.md` (T3).

# Constraints: Issue #324 — Shadow rehearsal on BTC+ETH 5-min ladders (no signer)

## Quality & Tests
- Zero regressions: `tests/test_shadow_run.py`, `tests/test_shadow_exec.py`,
  `tests/test_single_buy_saver.py`, `tests/test_ladder_probe.py`,
  `tests/test_ladder_tape_collect.py` stay 100% green; full `python -m pytest -q`
  stays with GitHub CI on push (merge gate).
- New behaviour needs tests RED against untouched code and GREEN after; each added
  assertion must fail without its change. The new harness is
  `tests/test_ladder_shadow_rehearsal.py` (fixture rehearsal, offline).
- Anti-cheat: strictly forbid skipping tests, deleting or weakening assertions,
  or bypassing linters. Tests use `tmp_path` fixtures; no test writes into live
  `data/` or `run/`. `data/orders.db` is never touched by the harness or a test.

## Behavior Boundaries
- **Rehearsal only, no production code.** `scripts/ladder_shadow_rehearsal.py`
  drives `run_shadow` through the injectable seam (`markets_fn`, `decide_fn`,
  `fetch_books`) and changes no shipped module. The screener, quoting, caps,
  and the pairs/exit thresholds are untouched.
- **No signer, no venue writes.** Fills come from the tape cursor, books from
  the same cursor; the only network is the rehearsal's own public resolution
  read (stubbed in tests). Order ids stay `shadow-`-prefixed; closes carry no
  on-chain hash.
- **One pair_id per market, rung lifecycle.** Every rung carries the market's
  single `pair_id` (the `record_submit` carry semantic); filled rungs retire,
  cancelled rungs re-quote, and a market with an exit close goes dark (no
  re-post of exited shares under the market-wide stamp).
- **Probe gates, rehearsal proves.** The gate (both series verdict go) is
  checked before any rehearsal; the rehearsal reports placement + fill + exit
  per series with conservation (filled == merged legs + exited + settled,
  zero orphans, zero double-counts). Merge rows count pair-units; only
  `shadow_merge_legs` counts leg shares.
- No new external dependencies; no live execution during build.

# Constraints: Issue #326 — Refill-after-exit decision + conditional netting fix

## Quality & Tests
- Zero regressions: `tests/test_single_buy_saver.py`, `tests/test_shadow_exec.py`,
  `tests/test_shadow_run.py`, `tests/test_auto_pairs.py` stay 100% green; full
  `python -m pytest -q` stays with GitHub CI on push (merge gate).
- New behaviour needs tests RED against untouched code and GREEN after; each added
  assertion must fail without its change. The repro test documents the trap
  before any fix exists.
- Anti-cheat: strictly forbid skipping tests, deleting or weakening assertions,
  or bypassing linters. Tests use `tmp_path` fixtures; no test writes into live
  `data/` or `run/`. `data/orders.db` is never touched.

## Behavior Boundaries
- **Decision first, code gated.** T3 builds only on a CHECKPOINT 1 netting-wins
  verdict; a lifecycle-wins verdict closes the issue on the decision note.
- **`load_pair` stays fills-only.** Netting lives locally in the exit-sizing
  path; `complete_pair`, merge accounting, and reports keep their shared view.
- **Frozen:** `max_pair_cost`, grace defaults, `pairs_exit_window_sec`, rescue
  route order, `should_exit()`, risk caps, screener, registry schema.
- **Fail-closed bias:** the guard (`_check_positions`) keeps refusing genuine
  divergence; netting may only shrink the sized amount toward venue agreement,
  never bypass the check. Attribution scoping must prove its bias (over-netting
  strands, under-netting oversells) with a test.
- No new external dependencies; no live execution during build.
