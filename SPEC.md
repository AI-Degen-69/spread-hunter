# SPEC — #517: Capital risk analytics for expectancy, drawdown and downside risk

Branch: `i517/add-capital-risk-analytics-for-expectancy-drawdown` | Tier: Standard | Type: Code + UX/Copy (readout)

Supersedes the #516 `post-fill markout and statistical robustness` spec in this file's history (kept in
git), which had superseded #515's, then #514's, then #513's. Part 5 of the execution-telemetry family
(`#513` → `#514` → `#515` → `#516` → this), which completes the roadmap. Related: #527 (fill model),
#535/#537 (one CI critical value); the #516 follow-ups stay recorded in
`docs/issues/516-noticed-but-not-touching.md`.

## Goals

1. The operator can see **how deep and how long an equity drawdown ran** — the largest peak-to-trough on
   the capital curve, and the median/p90 length of the drawdowns that were regained, **descriptive only,
   no chip** ("recovered in 20 marks is good" has no meaning without knowing how fast this strategy
   normally compounds; a chip on a guess is a confident lie).
2. The operator can tell **where the tail risk sits** — the third standardized moment of the per-close
   returns, with the ticket's traffic-light band (`good` upside tails, `average` roughly symmetric,
   `bad` downside tails).
3. The operator can judge **return per unit of downside pain** with an explicit risk-free parameter on
   Sortino, keeping the settled field name `sortino_ratio`.
4. All three are **pure and deterministic**: values in, values out. No clock, no socket, no SQLite
   handle, no signer, no `random`. Inputs are never mutated. Every figure is rounded and
   `json.dumps(..., allow_nan=False)`-safe. Missing stays missing — never `0`.
5. **The three metrics are VISIBLE on the Reports & Analytics page** (`page-reports`) with their band
   chips (recovery unbanded). A calculation-only or CLI-only implementation does not satisfy this issue.
6. Bands are computed **server-side from the rounded payload** in the same function that shapes it and
   shipped as data; the renderer maps a band name to the existing `analytics-chip` modifier and holds no
   threshold. A payload without the section renders no chips (tested, not assumed).
7. The existing CI helper and SQN are **reused, never re-implemented**: one `mean_pnl_ci`, one
   `system_quality_number`, both already in `core_brain/statistical_analytics.py` since #516/#537, with
   `kpi._mean_pnl_ci` an alias of the module's own function. The new card links to them in copy; it
   computes neither.

## Acceptance criteria (from the ticket, restated as testable statements)

- [ ] `pnl_skewness()` returns the population third standardized moment of `_finite`-filtered per-close
      returns, `None` for fewer than three usable values or zero variance; `skewness_band()` applies the
      settled table (`good` above `0`, `average` in `[-0.5, 0]`, `bad` below `-0.5`, `unmeasured` for
      `None`).
- [ ] `sortino_ratio(returns, risk_free=0.0)` matches `kpi.compute_trade_analytics`'s Sortino exactly at
      `risk_free=0.0` (pinned by test against `kpi` as an oracle), returns `None` for fewer than two
      usable returns, zero variance, no negative excess return, or a non-finite `risk_free`; the
      docstring states the unit (percent per close) and that nothing is annualized;
      `sortino_band()` applies `good >= 2.0`, `average [1.0, 2.0)`, `bad < 1.0`.
- [ ] `drawdown_recovery(points, starting_value=None)` walks a caller-supplied equity curve once, in the
      order given, and reports the deepest drawdown in dollars and percent, whether that deepest episode
      was regained, the count of regained and still-open drawdowns, and the median/p90 length of the
      regained ones in curve points and (when both endpoints carry timestamps) in seconds.
- [ ] The `build()` payload gains exactly one additive key, `capital_risk`, always present and
      shape-frozen; every existing key and value is unchanged (`kpi.report`'s `equity_series` and
      `account_series` values are byte-identical).
- [ ] `kpi.report` feeds that section the venue account-value curve it already computes; the pure
      functions stay curve-agnostic and are tested on a caller-supplied series.
- [ ] The `#capital-risk-readout` card renders the three metrics on the Reports & Analytics page, the
      two banded tiles carrying the chip their payload band names and every recovery tile carrying none.
- [ ] Nothing is forked: the module holds one `mean_pnl_ci` (imported back by `kpi` as
      `_mean_pnl_ci`) and one `system_quality_number`, both asserted by test; no CI or SQN formula
      appears in the new test file.
- [ ] No network, signer or store side effect: importing the analytics module loads neither
      `core_brain.kpi` nor `core_brain.order_registry`.
- [ ] Runnable: `python -m pytest -q tests/test_capital_risk_analytics.py`

## Edge cases the tests pin

- Skewness: `[1, 1, 1, 1, -10]` → `-1.5`; `[-1, -1, -1, -1, 10]` → `1.5`; `[-1, 0, 1]` → `0.0`;
  `[1, 2]`, `[2, 2, 2]`, `[]`, `[5]` → `None` (`unmeasured`, never `average`).
- Band boundaries: skewness exactly `0.0` and exactly `-0.5` read `average`; `-0.500001` reads `bad`.
  Sortino exactly `2.0` reads `good`; exactly `1.0` reads `average`; `0.999999` reads `bad`.
- Sortino: `[-1, -1, -1, -1, 10]` → `1.2` at `risk_free=0`, `0.1` at `risk_free=1`, `-0.91227` at
  `risk_free=11`; `[2, 2, 2, 2, -5]` → `0.12`; `[1, 2, 3]` (no negative return) → `None`.
- Drawdown recovery curve: start `1000`, points `1100, 900, 950, 1100, 1200` at `ts` `100..500` →
  deepest `200` usd, `18.1818` %, regained (the `1100` that equals the old peak closes it), length
  `3` points / `300` seconds; the same curve with no `ts` omits `drawdown_recovery_sec` entirely.
- A curve that only rises: `deepest_usd == 0.0`, `deepest_recovered is None`, `episodes == 0`, medians
  `None`. An empty curve: every figure `None`.
- An unrecovered drawdown counts in `open`, contributes **no** length, and never enters the medians.
- A peak at or below zero: `deepest_pct is None` (where `kpi` answers `0.0` — an intentional difference,
  documented, and excluded from the oracle test).
- Points with a `NaN`, `None`, `"abc"` or infinite `v` are skipped and counted in `skipped`; a point with
  an unusable `ts` still contributes its length in points and no seconds.
- `risk_free` is percent per close, the unit of every return; a non-finite `risk_free` is `None`, not a
  silently-unannualized-zero substitute.

## Explicit out of scope

- Parts 1–4 (siblings `#513`, `#514`, `#515`, `#516`): no change to fill-path, friction, queue-realism or
  robustness metrics, and no re-banding of the CI/SQN gates that #516 owns.
- Curve assembly: the payload passes the curve it already computes; this ticket neither samples, writes
  nor re-derives an equity series, and `core_brain/account.py` / `order_registry` writers are untouched.
- Refactoring `core_brain/kpi.py` to import the new functions (operator-settled: standalone pure
  functions, `kpi` used as a cross-check oracle in tests only). Recorded as rejected in `tasks/plan.md`.
- Any change to KPI rollups, balance tracking, the validation ladder (#470/#471/#474), LIVE execution,
  `data/orders.db` or any production store.
- `payload_version`: bumped because a payload field ships (the documented rule), not because a value
  changed.
