# Issue #248 — Audit and align dollar expectancy with mean return metrics

## Scope and classification
- **Size:** Standard — coordinated Python KPI + dashboard JS + audit doc + test updates, one design decision (companion fields, formulas unchanged).
- **Types:** Code + Docs + Design/UI.
- **Stack:** Python/pytest (`core_brain/kpi.py`), browser JS served by the Python dashboard (`dashboard/static/app.js`).
- **Primary skills:** `test-driven-development`, `incremental-implementation`, `verification-before-completion`.

## Spec (embedded — Small surface, no SPEC.md ceremony beyond this)
- `expectancy_usd` = mean of `realized_pnl` over ALL closes (equal-weighted dollars).
- `mean_return_pct` = mean of `100*pnl/cost` over closes with valid positive `cost_basis` only (equal-weighted percents, smaller population).
- Sign divergence (+$ vs −%) is mathematically valid: a small-cost −100% trade dominates the percent mean without outweighing larger-dollar wins.
- New read-only companions: `n_measured_returns` (len of measured percent population), `dollar_weighted_return_pct` = 100·Σpnl/Σcost over measured trades only. Both `None` when unmeasurable — never fabricated zero.
- Gate inputs (`pct_pass`, `dollar_pass`, `ci90_lower_pct`, `run_profitability`) MUST NOT change; new fields are display-only.

## Evidence-based improvement proposal
Pin gate-immunity with an explicit test: the GO/NO-GO `passed` verdict is identical before/after the companion fields are added (the issue says "unless the audit proves a metric currently gates a decision incorrectly" — so the audit must prove the gate is untouched, not just promise it).

## Implementation tasks

### Task 1 — Write the audit document [Docs]
- **Files:** `docs/audits/expectancy-vs-mean-return.md` (new).
- **Build:** Five-column table per metric (numerator, denominator, population, weighting, sign interpretation) for `expectancy_usd`, `mean_return_pct`, `dollar_weighted_return_pct`; plain-language conclusion that both formulas are correct for their names.
- **Verification:** File exists with the table; test in Task 3 pins the same facts as assertions so doc and code cannot drift.

### Task 2 — Add companion fields to trade analytics [Code/Test]
- **Files:** `core_brain/kpi.py` (`compute_trade_analytics`), `tests/test_trade_analytics.py`.
- **Build:** Add `n_measured_returns` and `dollar_weighted_return_pct` (measured-trades only, `None` when unmeasurable), placed next to `mean_return_pct`; flows through existing `report()` `trade_analytics` block with no extra handling.
- **Verification:** New assertions fail before the change (RED), pass after (GREEN); existing `expectancy_usd`/`mean_return_pct`/`ci90_lower_pct` values unchanged.

### Task 3 — Screenshot-like regression fixture [Code/Test]
- **Files:** `tests/test_trade_analytics.py`, `tests/test_seed_preview_fixture.py` (operator-facing comment only).
- **Build:** Deterministic closes reproducing +$0.11 expectancy vs −0.70% mean return shape; assert both new companion fields (`n_measured_returns`, `dollar_weighted_return_pct`) including their `None` behavior on unmeasurable input.
- **Verification:** `python -m pytest -q tests/test_trade_analytics.py tests/test_seed_preview_fixture.py` green; regression fails if formulas are altered.

### Task 4 — Dashboard explainer copy and sublabels [Design/UI]
- **Files:** `dashboard/static/app.js` (`renderAnalyticsSurface` + `renderQuantRiskGrid`), `tests/test_analytics_api.py`.
- **Build:** Reuse `.info-bubble`/`.info-tooltip` click pattern for a plain-language sign-divergence explainer in BOTH grids; sublabel dollar-weighted return under mean return, `n_measured_returns` next to `n_closes`; `None` renders "unmeasured", never `0`. Keep pinned tile labels ("Average Profit Per Close", "Mean Return Per Trade") unchanged.
- **Verification:** `tests/test_analytics_api.py` asserts new copy/sublabels present and pinned labels intact; browser eyeball of `#kpi-grid` + `#quant-grid`.

### Task 5 — Gate-immunity and regression sweep [Code/Test]
- **Files:** `tests/test_trade_analytics.py`, `tests/test_mean_pnl_ci.py` (assertions only unless a mismatch is found).
- **Build:** Assert GO/NO-GO `passed` verdict identical with/without companions; `run_profitability` untouched; never add new fields to any gate.
- **Verification:** `python -m pytest -q tests/test_trade_analytics.py tests/test_mean_pnl_ci.py tests/test_seed_preview_fixture.py tests/test_analytics_api.py` green.

## Acceptance checklist
- [ ] Audit table covers numerator/denominator/population/weighting/sign per metric.
- [ ] +$0.11 vs −0.70% regression demonstrates the valid divergence in operator language.
- [ ] Formulas preserved with clearer copy, or changed consistently everywhere — no silent meaning change.
- [ ] Missing/non-positive cost basis excluded from percent calcs, surfaced as unmeasured.
- [ ] Focused four-file pytest selection green; gate verdict provably unchanged.
