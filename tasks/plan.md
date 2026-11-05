# Plan - #934 Show the running arm's parameters in the dashboard's telemetry card

Branch: i934/show-the-running-arm-s-parameters-in-the-dash | Issue: #934

## Tier & type

- **Tier: Standard** — 3 frontend files + tests, one architectural decision
  (name-keyed cache + sequence guard for once-per-arm fetching).
- **Task type: Design/UI + Code** — bento card repurpose with renderer logic;
  verification is browser-surface (markup) + Node harness (logic).

## Quick-fix lane

Skipped: the issue carries no `quick-fix` label
(`✨ Feature`, `🎨 UI & Design`, `🤖 Ready for Agent`, `🧪 Testing`).

## CodeRabbit plan intake (cost paid once, do not re-read the comment)

- **Adopted:** two-phase skeleton (card + renderer/cache/actions, then tests);
  seam pointers after verification below; the 13 renderer test scenarios and
  the regression-removal list as the test basis.
- **Rejected / merged:** nothing structural — the 2-phase shape already fits
  the 3-task limit. Rejected any backend, catalog, modal, prototype, or global
  CSS change; the plan comment's "do not modify" list stands.
- **[UNVERIFIED] at intake, verified during planning:** CodeRabbit's line
  numbers drifted (it cited `app.js:5512`/`5604-5652`; actuals are
  `renderBrokerPortfolioOverview` at `app.js:5575`, tile lookups at
  `~5667+`, `renderPortfolioOverview` at `app.js:8426`; telemetry card at
  `index.html:419`, not `424-479`). All seams below were re-verified against
  the live tree. Remaining [UNVERIFIED]: exact line numbers inside
  `tests/test_portfolio_card_basis.py`, `tests/test_negative_values_read_as_losses.py`,
  and the two JS harnesses — Station III verifies each deletion against the
  live file before deleting.

## Open questions (all 4 resolved from code — no operator questions)

1. **Which arm counts as running** → default stands. Verified:
   `_resolve_active_profile` (`dashboard/server.py:2079-2123`) attaches
   `tournament_arm` only on the shadow path; the live path
   (`_read_trader_profile`, `:1798`) carries name/preset only. Card renders
   `active_profile.tournament_arm`, else `active_profile.name` + no-arm note,
   never invents an arm.
2. **What "parameters" means** → default stands. Verified: `/api/profile/arm-detail`
   (`server.py:3410+`) returns `label`/`profile`/`env`; card shows env
   overrides + profile line, `Full parameters` jumps to the full table.
3. **Tiles leave entirely** → default stands. Verified: the four subcards and
   their ids exist only in the telemetry slot (`index.html:419-479`); the same
   figures exist elsewhere (win rate / profit factor tiles, hero PnL pill).
4. **Parameters panel stays** → untouched per CONSTRAINTS.

## Verified seams (live tree)

- Markup: `dashboard/static/index.html:419` card shell
  (`data-purpose="execution-telemetry-card"`, title `EXECUTION TELEMETRY`,
  tag `POLY-ENGINE`), subcard ids `broker-kpi-winrate/wins/pf/sharpe/spread/
  expectancy/pairs`, bar `bento-winrate-bar`, edge `bento-edge-tag`.
- Paint: `renderBrokerPortfolioOverview` (`app.js:5575`, tile lookups `~5667+`,
  bar/edge writes after); `renderPortfolioOverview` (`app.js:8426`) early-returns
  on missing `kpi.portfolio` — the card renderer must be called BEFORE that
  return (card needs only `status`).
- Reuse: `openArmDetailModal` (`app.js:3396`), env row markup
  `<li><code>K</code> = <code>V</code></li>` (in `renderArmDetailHtml`),
  `esc` (`app.js:591`), `safeJsonFetch` (`app.js:12884`),
  `paramFetchSeq` guard pattern (`app.js:5064,5384-5394` — dedicated
  card-only counter, do NOT reuse the variable).
- Params jump: `loadParamArms` builds `plan_key|arm` options (`app.js:5161+`);
  `#param-arm-select` (`index.html:236`), `#params-panel` (`index.html:218`);
  page switch by clicking `.proto-nav-btn[data-page="strategy"]`
  (`prototype.js:230-239` pattern, `showPage` at `:155` is private).
- Backend (read-only): `active_profile.tournament_arm` enters the status
  payload via `_resolve_active_profile` + `:2279`; arm definition from
  `GET /api/profile/arm-detail?name=` (label/profile/env, KPI work at `:3452`
  — the reason for once-per-arm fetching).
- Tests: `test_milestone8_html_contains_required_sections`
  (`tests/test_dashboard_server.py:402`); harness pattern
  `tests/js/*_harness.cjs` loaded via `new Function` with stub DOM,
  dispatched from Python `subprocess.run`
  (cf. `tests/test_live_state_language.py:761`).

## Improvement proposal (adopted — simplification)

> **Use `safeJsonFetch` for the card's arm-detail request, not raw `fetch`.**
> Evidence — `async function safeJsonFetch(url, timeoutMs = 8000)` at
> `dashboard/static/app.js:12884`; the card spec needs timeout + failure-note
> behavior that `safeJsonFetch` already implements, while `openArmDetailModal`
> (`app.js:3396`) uses raw `fetch`. One call site, zero new helpers, and the
> failure-note path comes for free. Folded into Task 2.

## Dependency graph

- T1 (markup + CSS) → unlocks T2 (renderer writes to the new ids).
- T1 + T2 → unlock T3 (tests pin both).
- Order: T1, then T2 (riskiest logic first within its dependency), then T3.

## Tasks

### T1 — Card markup + scoped CSS [Design/UI] · size S · Depends on: none [x]

- `dashboard/static/index.html` (telemetry slot only): keep
  `bento-col bento-col-telemetry` + header shell + `bento-telemetry-stack`;
  set `data-purpose="running-arm-card"`, title `RUNNING ARM`, tag
  `id="running-arm-state"` (`bento-tag-engine`, initial `--`); replace the
  four subcards with label (`running-arm-label`, `broker-kpi-val`), profile
  (`running-arm-profile`, `broker-kpi-sub`), `<ul class="arm-detail-env"
  id="running-arm-env">`, note (`running-arm-note`, `broker-kpi-sub`), action
  row (`⋯` → `running-arm-details-btn`, `Full parameters` →
  `running-arm-params-btn`, both `arm-detail-btn`).
- `dashboard/static/styles.css`: add ONLY
  `#running-arm-env code { min-width: 0; overflow-wrap: anywhere; }`.
- Verify: static-markup assertions (in T3) + open
  `http://127.0.0.1:8799`, expect the `RUNNING ARM` title.

### T2 — Renderer, once-per-arm cache, actions, retire tile writes [Frontend/Logic] · size M · Depends on: T1 [x]

- beside `renderBrokerPortfolioOverview`: module state (payload `Map`,
  last-requested arm, card-only seq counter, last shown payload, note, action
  arm). Fetch via `safeJsonFetch('/api/profile/arm-detail?name=' +
  encodeURIComponent(arm))` only when arm ≠ last-requested AND not cached.
  Accept only on counter-match + still-current arm; `null` / `ok !== true`
  ⇒ failure note, keep previous content, no caching, no retry until arm
  changes or reload.
- `renderRunningArmCard(status)`: null/undefined ⇒ return silently;
  empty strings ⇒ missing. Tag `RUNNING` / `STOPPED` / `NO RUN`. Arm present:
  cached label/profile/escaped env rows (`textContent` + `innerHTML` with
  `esc()` for rows only), empty profile hidden, empty env ⇒ baseline
  sentence, first paint ⇒ arm name + loading note, arm-switch ⇒ keep rows +
  naming note, failure ⇒ keep rows + `Could not load…` note. No arm: profile
  name + no-arm note, no fetch, buttons disabled. No profile: `No run profile
  recorded.`, buttons disabled.
- Call it from `renderPortfolioOverview` BEFORE the `kpi.portfolio` early
  return; export it (and keep `renderPortfolioOverview` exported) in the
  `module.exports` block (`~13087`).
- Delete the 7 retired lookups (`elPairs/elSpread/elExpectancy/elWinrate/
  elWins/elPf/elSharpe`) + win-rate bar + edge-tag writes + values used only
  by them (`activePairs`, spread/expectancy/winrate/wins/pf/sharpe locals);
  keep realized-PnL, liquidity writes, `#broker-kpi-strip`, liquidity/equity
  cards. Bind both buttons once (`dataset.wired`): `⋯` ⇒
  `openArmDetailModal(currentArm)`; `Full parameters` ⇒ first option whose
  arm part (after last `|`) exactly equals the arm → set + dispatch `change`,
  else card note; then click Strategy nav btn if present + `scrollIntoView({
  behavior:'smooth', block:'start' })` on `#params-panel`; skip missing
  elements silently.
- Verify: T3 harness scenarios (all 13).

### T3 — Tests: new card coverage + retired-tile cleanup [Testing] · size M · Depends on: T1, T2 [x]

- `tests/test_dashboard_server.py`: extend `test_milestone8_html_contains_required_sections`
  (new title/purpose/7 ids + retained `broker-kpi-strip`; absent old purpose;
  absent 7 retired ids in BOTH `index.html` and `app.js`) + 13 `running_arm_*`
  tests via new `tests/js/running_arm_harness.cjs` (stub-DOM + per-URL fetch
  router + `argv[2]` scenario dispatch + `_running_arm_harness()` helper,
  skip when `shutil.which("node") is None`).
- Remove ONLY: 3 tile tests in `tests/test_portfolio_card_basis.py`, 3 spread
  assertions in `tests/test_negative_values_read_as_losses.py`, `pairs`/`wins`
  outputs in `portfolio_card_harness.cjs`, spread lookup + 2 outputs in
  `sign_colours_harness.cjs`. Keep all liquidity/equity/PnL/quant coverage;
  `live_state_harness.cjs` + its tests untouched.
- Verify: `python -m pytest -q tests/test_dashboard_server.py -k
  "milestone8_html or running_arm"` + the two regression modules, quiet tree.

## Checkpoints (Mode A: one-line progress reports)

- After T1: card shell visible with `--` tag and empty rows.
- After T2: card renders arm label/env from cache, actions work.
- After T3: acceptance filter + regression modules green.

## Handoff

Next: `/iii-build-plan auto` on branch
`i934/show-the-running-arm-s-parameters-in-the-dash` (claimed, `@me`
assigned). No backend, prototype, neon, or catalog work. Quiet tree required.
