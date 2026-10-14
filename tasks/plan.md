# Plan - #540 Analytics readouts can stay blank after a reload while the tiles beside them keep painting

Branch: i540/analytics-readouts-can-stay-blank-after-a-reload | Issue: #540

Size: **Standard** — 3 files, one pre-decided approach (issue default verified against
live code), test-heavy. Task type: **Debug + Code**. Helper skills:
`test-driven-development`, `incremental-implementation` (all tasks);
`debugging-and-error-recovery` (T1). Verification mode: focused pytest files only
(full suite is the CI merge gate).

## Intake - CodeRabbit plan (adopted / rejected / resolved)

- Adopted: phase skeleton (fail-first tests → one cached branch → acceptance run),
  affected files, exact readout assertions, regression file list, `try`/`catch` shape,
  keep-guard + keep-deferred-frame decisions, input-lag-harness reuse.
- Rejected (>4 splits merged into 3 tasks); rejected the specified error-isolation
  poison — see correction 3 below (adopted by default: it is edge-case hardening
  with verbatim evidence, not scope).
- Resolved, all spot-checked live, no [UNVERIFIED] remains:
  1. Line drift: `renderAnalyticsSurface` is app.js:5180, `renderCachedSections`
     588–633, `renderKPIs` 5273–5354, generation guard 5347–5353, exports incl.
     `renderCachedSections` at 9369.
  2. `#analytics-surface` exists (index.html:450) and contains all five readouts
     (489–534) plus `#kpi-grid` (538); rail maps it to Reports (prototype.js:77).
     `#analytics-gates` lives on Strategy — never gate on it.
  3. `trade_analytics.sharpe_ratio` is NOT surface-only: `renderKPIs` reads the same
     field the same guarded way (app.js:5328 vs 5215). A string poison there throws
     in the EXISTING branch first, failing the test even with the correct fix.
     Replacement poison: `trade_analytics.mean_return_pct = "<string>"` with
     `n_closes >= 1` — `renderKPIs` never reads it; the quant grid throws at 5358
     (`.toFixed(2)` on a string, guard passes on non-null), unguarded call at 5237.
  4. Error-isolation scenario must NOT flush: the deferred callback (5348–5353) is
     deliberately untouched and unguarded, so flushing the poisoned frame would
     throw outside the new branch. Record state synchronously after
     `renderCachedSections()`; harness runs one scenario per process so the queued
     frame dies with it.
  5. Deferral source check (`test_dashboard_input_lag.py:199`) matches the existing
     deferred call, which stays — safe.
  6. Startup guard: init calls `switchTab` (673) before any poll with `lastKpi`
     undefined, so the new branch MUST require `currentKpi` or it paints zeros over
     placeholders on a fresh load.

## Open questions (needs-answers — resolved from code, operator confirms at review)

1. One `paintable(tab2, …)` branch calling `renderAnalyticsSurface` — YES (default).
   Readouts each have own `try`/`catch` (5230–5234) but tiles (5202–5223), quant grid
   (5237) and gates/sample (5249–5255) do not, so one call + one local `try`/`catch`
   matches existing style and contains all of them.
2. Keep the generation guard — YES. Purpose pinned by comment (578–582) and the
   deferral tests; the stale-generation test below guards it.

## Spec (embedded — Small surface, Standard ceremony waived to this block)

- Goal: opening Reports & Analytics repaints the five readouts synchronously from
  the cached snapshot, the way KPI tiles already do.
- Acceptance: `python -m pytest -q tests/test_analytics_surface_mount.py tests/test_analytics_api.py`
  green; `tests/test_dashboard_input_lag.py` green unedited.
- Edge: dropped deferred frame (hidden-tab flush) → cached branch restores;
  stale generation → still no paint; surface throw mid-navigation → navigation
  survives + `console.error`; empty cache at startup → placeholders untouched.
- Out of scope: metrics, payload, `payload_version`, readout markup/copy, deferred
  mechanism (#266), sub-view filter, `#kpi-grid` tiles, header pills from cache.
- Must not change: `index.html`, `prototype.js`, `analytics_surface_harness.cjs`,
  `test_analytics_api.py`, `test_dashboard_input_lag.py`, `data/orders.db`;
  in app.js: `deferPaint` callback, `deferPaint`/`tabVisible`/`paintable`,
  generation increment, poll paint gates, readout renderers, per-card catches.

## Interface contract (no new app interfaces; harness JSON is the contract)

- Fix (app.js ~611, directly after the `kpi-grid` branch):
  `paintable(tab2, getElementById('analytics-surface')) && currentKpi` →
  `try { renderAnalyticsSurface(currentKpi, lastStatus); } catch (e) { console.error(...); }`.
  Tiles paint first, readouts in the same call, no fetch.
- Harness (`input_lag_harness.cjs` only): argv `{scenario, kpi}` feeds `/api/kpi`
  (+ fetch counter); readout sentinels seeded pre-load + record helper; rail links
  `readout → analytics-surface → page-reports`; one-head frame runner (never
  `__flushRaf` to observe callback A); `console.error` counter; scenarios:
  `analytics-startup`, `analytics-tabs-recovery`, `analytics-rail-recovery`,
  `analytics-error-isolation`, `analytics-stale`.
- Tests (`test_analytics_surface_mount.py` only): reuse the five existing fixture
  builders + add `portfolio` and `trade_analytics` with `n_closes >= 1` (guarded
  reads need it). Per-readout assertions per the issue strings (fill-path `per
  close`/`0.30`/`33.3%`; rescue `SLED (¢)`/`15.80`; queue `Trade-Through Depth
  (¢)`/`2.50`/`10.0s`; robustness `Markout 5m (¢)`/`-2.50`/`SQN`; capital `Deepest
  DD ($)`/`14.60`/`Sortino`). Recovery records after `switchTab(2)` BEFORE any
  flush; queue holds exactly 1 new unrun callback; fetch count unchanged on reopen.

## Proposal (adopted by default — hardening, evidence above)

Use the `mean_return_pct`-string poison for error isolation instead of the planned
`sharpe_ratio` poison: the specified poison throws in `renderKPIs` (5328) before
the new branch runs, so the test would fail with the fix in place. Evidence:
`ta.sharpe_ratio.toFixed(2)` at 5328 (existing branch, reached first) vs
`ta.mean_return_pct.toFixed(2)` at 5358 via unguarded 5237 (`renderKPIs` never
reads that field). No scope expansion proposed.

## Tasks

- [x] T1 (M, [Debug]): harness scenarios + recovery/error/stale tests; run RED —
  tabs-immediate and rail-Reports records still show sentinels; stale A-run shows
  no change only via one-head runner. Depends on: —.
- [x] T2 (S, [Code]): one cached branch + local catch in `renderCachedSections`;
  new tests GREEN. Depends on: T1. Checkpoint: open Reports post-reload paints
  readouts synchronously; report RED→GREEN record + stale-guard removal check.
- [ ] T3 (S, [Code]): acceptance command + `test_dashboard_input_lag.py` green
  unedited; record proofs (recovery RED→GREEN; stale fails with guard removed,
  passes restored). Depends on: T2.

## How to verify (operator, hands-on — no pytest)

1. `python -m scripts.build_demo_db --out data/demo_analytics_unique.db` (unused
   name, no `--force`). 2. `python -m dashboard.server --db
   data/demo_analytics_unique.db` (dashboard only, no bridge, `BRIDGE_CONTROL`
   unset, never START). 3. Open `http://127.0.0.1:8799` → Reports & Analytics:
   all five readouts show figures. 4. Reload → Home → Reports & Analytics:
   readouts show figures immediately. Failure look: placeholder text beside
   painted KPI tiles.
