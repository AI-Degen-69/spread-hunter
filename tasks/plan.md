Branch: i749/consolidate-reports-and-analytics | Issue: #749

# Plan — Consolidate Reports and Analytics into one Performance and Analytics page

**Size:** Large (cross-cutting markup + shell wiring + ~10 test files). **Task types:**
Code, Frontend/UI. **Stack:** static HTML/CSS/JS served by FastAPI; pytest + node
stub-DOM harnesses.

## Intake note (read once, then trust this)

- **No CodeRabbit plan exists.** The only issue comment is the operator's
  `@coderabbitai plan` trigger; CodeRabbit never posted a planning result. Every
  seam below was verified against the working tree at `main` (`34d62e5`), not
  adopted from a plan comment.
- **Adopted from the issue body:** all of it — the two ported cards, the
  stop-slippage move, the delete list, the settled decisions.
- **Rejected:** nothing in the body; the build rejects one *implementation* option
  instead (see "Improvement adopted").
- **Resolved while verifying (were unstated in the issue):**
  - The renderers take an optional `hostId` (`app.js:5244`, `:5548`) and default to
    the legacy ids. **Reusing the same host ids** (`pnl-ci-readout`,
    `execution-funnel`, `stop-slippage-readout`) means the port needs **no renderer
    change and no call-site change** — the ids simply live in a new card.
  - `app.js:698` and `app.js:10622` gate the cached repaint on
    `document.getElementById('kpi-grid')`. Deleting `#kpi-grid` silently makes the
    `#analysis-v2` cached repaint at `:710`/`:10627` unreachable. The gate is
    re-pointed in T4.
  - `#analytics-gates` (`index.html:746`) is physically **inside**
    `#analytics-surface`, which T4 deletes. It must be re-parented to `#tab-2`, or
    the Strategy page loses it.
  - `renderAnalyticsSurface` (`app.js:6258-6263`) paints five readouts plus the
    gates. It stays: the gates call still has a host, the rest no-op.
  - The legacy card **markup** goes; the legacy renderer **functions** stay
    (they are unit-tested through the stub-DOM harness). Retiring them is a
    follow-up issue — deleting them here would delete ~40 tests of code the
    issue never asked to remove.

## Dependency graph

```
T1 (rail: retire reports, rename page)
  └─ T2 (port CI + funnel into #analysis-v2)
       └─ T3 (move stop-slippage onto #analysis-v2)
            └─ T4 (delete the rest of the legacy surface + subnav wiring + re-point gates)
                 └─ CP2, T5 (acceptance sweep)
```
T2–T4 all touch `dashboard/static/index.html`, so they cannot run in parallel.

## Improvement adopted (evidence-first)

**Evidence:** `app.js:698`
`if (paintable(tab2, document.getElementById('kpi-grid')) && currentKpi) {`
and `app.js:705`
`if (paintable(tab2, document.getElementById('analytics-surface')) && currentKpi) {`
— both gates name nodes this issue deletes, and `app.js:710` (the `renderAnalysisV2`
cached repaint) sits inside the same block, so the canonical page would stop
repainting on a tab switch. **Classification: edge-case hardening → adopted.** T4
re-points both gates at `#analysis-v2`.

**Rejected (recorded so it does not resurface):** *also* deleting the unreachable
renderers (`renderSampleSufficiency`, `renderFillPathEdge`, `renderRescueFriction`,
`renderQueueRealism`, `renderRobustness`, `renderCapitalRisk`, the chart renderers,
`renderKPIs`, `renderQuantRiskGrid`, the simulator) in this PR. It is dead-code
sweeping, not this issue, and it would delete the suites that test those renderers.
Follow-up issue.

## Tasks

### T1 — Retire the `reports` page, relabel the analytics page
- **Status:** done (`d81e4d2`).
- **Size:** S · **Domain:** [Frontend/Code] · **Verify:** node harness + pytest (not browser)
- **Files:** `dashboard/static/prototype.js` (`PAGES` :20, `PAGE_LAYOUT` :84-97,
  `normalizePage` comment :104), `dashboard/static/index.html:139`,
  `tests/test_sidebar_prototype.py`
- **Build:** drop `'reports'` from `PAGES`; delete the `reports` `PAGE_LAYOUT`
  entry; `analysis` entry label → `Performance & Analytics` (note updated to say
  it is the one analytics page); hidden tab-shell label at `index.html:139` →
  `PERFORMANCE &amp; ANALYTICS`; fix the "six known pages" comment.
- **Tests (same commit):** `PAGES` tuple in `test_sidebar_prototype.py`; the two
  tests that navigate with `stored="reports"` / `clicks=["reports"]` switch to
  `"trades"`; `test_the_analysis_entry_keeps_its_identity` expects the new label.
- **Depends on:** —

### T2 — Port the CI card and the funnel card into `#analysis-v2`
- **Status:** done (`fe0717c`).
- **Size:** M · **Domain:** [Frontend/UI] · **Verify:** pytest (node harness)
- **Files:** `dashboard/static/index.html` (canonical cards :852-888, :936-953;
  legacy cards :474-496), `dashboard/static/analysis_v2.js` (`paintCiScale`
  :594-636, its `applyAll` call :646, derived keys `_ci90/_ci95/_ciVerdict`
  :225-227 and `_f0.._f3/_fdrop0..2` :246-252), `tests/test_analysis_v2_binding.py`,
  `tests/test_541_data_analysis.py`, `tests/js/analysis_v2_harness.cjs`
- **Build:** in the canonical page, replace the `.ci-scale` / `.ci-legend` /
  `.verdict` body of the *Average Close* card with
  `<div id="pnl-ci-readout" class="pnl-ci-readout">` and the stepped `.funnel`
  body of the *Execution* card with `<div id="execution-funnel" class="execution-funnel">`,
  keeping each card's `.card-head` / `.card-title` / `.info` chrome. Delete the
  `#ciScale` painter and the seven derived keys the deleted markup carried.
  Delete the legacy `#card-pnl-ci` and `#card-execution-funnel` cards.
- **Tests (RED first):** new assertions that `index.html` contains exactly one
  `id="pnl-ci-readout"` and one `id="execution-funnel"`, both inside `#analysis-v2`,
  and that `id="ciScale"` no longer exists; update the two `analysis_v2` harness
  tests that read `out["ciScale"]`; the mount suite keeps asserting the renderers'
  copy (unchanged host ids).
- **Depends on:** T1

### T3 — Move STOP-EXIT SLIPPAGE onto the canonical page
- **Status:** done (`2142973`).
- **Size:** S · **Domain:** [Frontend/UI] · **Verify:** pytest (node harness)
- **Files:** `dashboard/static/index.html` (new card in the Microstructure &
  Execution section; delete legacy card :523-532), `tests/test_stop_slippage.py`
- **Build:** a `.card` block with the canonical chrome
  (`card-head`/`card-title`/`info`) whose body is
  `<div id="stop-slippage-readout" class="stop-slippage-readout">`. Renderer and
  call site unchanged.
- **Tests (RED first):** `stop-slippage-readout` occurs once, inside
  `#analysis-v2`; the existing four-tile + footer copy assertions keep passing.
- **Depends on:** T2

### T4 — Delete the remaining legacy surface and its wiring
- **Status:** done (see the deviations in the completion record).
- **Size:** L · **Domain:** [Frontend/UI] · **Verify:** pytest (focused suites)
- **Files:** `dashboard/static/index.html` (:454-751, minus `#analytics-gates`
  which is re-parented and minus `#broker-portfolio-overview` which stays),
  `dashboard/static/app.js` (`initStatisticalSubnav` subnav block :3873-3899,
  `statsFilterScope` :3901-3909, `STATS_VIEW_TARGETS` :3914-3919,
  `pruneStatsSubnav` :3921-3929, `applyStatsViewFilter`, the `kpi-grid` /
  `analytics-surface` paint gates :698 / :705 / :10622),
  `dashboard/static/prototype.js` (`pruneStatsSubnav()` call),
  `tests/test_analytics_impact_tiers.py`, `tests/test_analytics_api.py:94`,
  `tests/test_sidebar_prototype.py` (two subnav tests),
  `tests/test_portfolio_card_basis.py:73`, `tests/js/input_lag_harness.cjs:156-162`,
  `tests/js/analytics_surface_harness.cjs` (view-filter case)
- **Build:** delete `.stats-subnav-container`, `#analytics-surface` and every card
  inside it except `#analytics-gates` (moved to `#tab-2`), the `#kpi-grid`,
  `#quant-risk-deck`, `#analytics-charts-matrix` (all five charts),
  `#card-sensitivity-simulator`, `#dist-container`. Delete the subnav wiring in
  `app.js` (the buttons it filters no longer exist) and the `pruneStatsSubnav()`
  call in `prototype.js`. Re-point the two paint gates at
  `document.getElementById('analysis-v2')`.
- **Tests:** `test_analytics_impact_tiers.py` is rewritten to its surviving
  journey — the go/no-go pair leads the canonical page (document order inside
  `#analysis-v2`) — and drops the per-deck `data-tier` parameterisation whose
  decks are gone; `test_analytics_api.py:94` asserts the same order on the
  canonical page; the two `stats-subnav` tests in `test_sidebar_prototype.py`
  retire with the subnav (reason recorded here); `test_portfolio_card_basis`
  splits the KPI strip on a boundary that still exists; the two node harnesses
  stop mapping the deleted ids.
- **Depends on:** T2, T3

### T5 — Acceptance sweep
- **Size:** XS · **Domain:** [Test] · **Verify:** the four suites the issue names
- **Build:** no production code. Run and fix fallout in
  `tests/test_sidebar_prototype.py tests/test_541_parity_ledger.py
  tests/test_stop_slippage.py tests/test_analytics_surface_mount.py` plus the
  files T1–T4 touched.
- **Depends on:** T4

## Completion record

- **T1–T4 done.** Commits: `d81e4d2` (T1), `fe0717c` (T2), `2142973` (T3), T4 with this file.
- **T4 deviated from the plan in three places, each on evidence:**
  1. **The `#kpi-grid` paint gate is deleted, not re-pointed.** `renderKPIs` reads
     `grid.innerHTML` unguarded, so with `#kpi-grid` gone its only call sites were
     dead branches. The decision gates were painted by the *deferred* repaint
     inside `renderKPIs`, so `renderAnalyticsSurface` gained its own gate and its
     own call site in `pollStatus` — otherwise the Strategy page's gates card
     would have stopped repainting entirely.
  2. **A new `paintAnalysisV2()` helper owns the analytics page.** The three
     ported cards live under `#analysis-v2` but were painted by a function gated
     on tab 2, so inside the rail layout `paintable()` refused the gate on the
     page that actually shows them: the port was unreachable in production while
     the rail-less test harnesses stayed green. Both call sites now go through
     the helper, gated on `#analysis-v2`.
  3. **Two pieces of chrome were ported with the readings they explain.**
     `#quant-stale-note` (the amber "backend older than this page" band) and the
     `$ vs %` explainer in the Mean Return tooltip lived only in the deleted
     markup; dropping them would have removed a signal and a sentence, not a
     duplicate.
- **Retired tests, with subjects that no longer exist:**
  - `test_sidebar_prototype.py::test_the_analytics_subnav_filters_only_its_own_page`
    and `::test_a_subnav_view_with_nothing_on_the_page_is_dropped` — the sub-nav
    they pin is deleted with the surface.
  - `test_analytics_surface_mount.py::test_no_analytics_sub_view_hides_the_go_no_go_row`
    (parametrised ×6) — same subject.
  - `test_analytics_surface_mount.py::test_stale_frame_paints_nothing` and the
    `analytics-stale` scenario — the rAF staleness guard lived in `renderKPIs`'
    deferred chart paint, which has no call site left. The renderer and its
    remaining suite retire in the follow-up issue below.
  - `test_analytics_impact_tiers.py::test_each_analytics_deck_declares_its_impact_tier`
    (parametrised ×9) — the decks carrying `data-tier` are gone; the surviving
    ordering is pinned by document order on the canonical page. The two CSS
    ordering rules stay asserted; the stylesheet sweep is the follow-up's job.
- **Follow-up issue (not this PR):** retire the renderers whose hosts this issue
  deleted (`renderKPIs`, `renderQuantRiskGrid`'s grid, the five accounting
  readouts, the five chart renderers, the sensitivity simulator) together with
  their suites and the now-dead `.analytics-chart-grid` tier CSS.

## Checkpoints

- **CP1 (after T1+T2):** the one analytics page shows the band CI card and the
  funnel, painted by the legacy renderers, and no `#ciScale` remains.
- **CP2 (after T4):** the served page contains exactly one analytics surface and
  no legacy deck; the Strategy page still shows the gates card.

## Definition of done

Targeted suites green (above), every changed behaviour covered by a test that
fails without it, and a hands-on **How to verify** block for the operator
(launch the dashboard, open the page, look at the cards — no pytest narration).
