# Interactive CI visualizer — spec

**Date:** 2026-10-26
**Status:** proposed (awaiting operator review)
**Owner:** operator, implemented by agent

## The problem

The Tier 1 Average Close card pins one metric — mean realized PnL per close —
with a 90/95 band rendered by `renderPnlCiReadout` in `dashboard/static/app.js`
from `trade_analytics.mean_pnl_ci`. The operator wants the same interactive 90/95
treatment for other core means (expectancy, mean return %, win rate), and the
five `paintCharts` cards in `dashboard/static/analysis_v2.js` (ch1–ch5) read flat:
no axes, no hover values, inconsistent footers.

## Decisions (approved)

- V1 upgrades the existing Average Close card in place; same footprint, default
  stays Mean PnL $. No new card, no layout change.
- KPI picker limited to core means: Mean PnL $, Expectancy $, Mean Return %,
  Win Rate. Path splits (merge / single-leg / stop), hold time, and drawdown are
  explicitly out — drawdown is a single run-level number, not a per-close
  distribution, so it gets no band in v1.
- Chart-grid lift is readability + hover only: real axes/ticks, zero lines, native
  SVG `<title>` hover values, consistent µ/σ/CI footers. No poll-freshness wiring,
  no CI shading on histograms, no new server fields.

## What changes

### 1. CI card (`#pnl-ci-readout`)

- Add a KPI picker to the card (segmented control above the band, rendered by
  the same readout so it survives repaints): Mean PnL $ / Expectancy $ /
  Mean Return % / Win Rate. Selection is view-local module state; a poll
  repaint preserves it.
- Add `resolveCiView(kpiKey, trade_analytics)` in `app.js`: one adapter returning
  `{mean, n, l90, l95, verdict, units}` from fields the payload already ships —
  `mean_pnl_ci` for Mean PnL $, the same spread for Expectancy $, `mean_return_pct`
  + `ci95_return_pct` + `ci90_lower_pct` for Mean Return %, `win_rate` +
  `win_rate_ci95` for the 95 band and the shipped `win_rate_ci90` for the 90
  band (both Wilson rows from `core_brain/kpi.py::_wilson_ci`; the z lives in
  the backend's one critical-value table per #535, so no renderer writes one
  down). The only `kpi.py` change is the additive `win_rate_ci90` field.
- Keep the nested-bar language: 90 inner, 95 outer, mean-diamond pointer,
  zero line (`$0.00` for $ metrics, `0%` for return %, `50%` reference for win
  rate), per-KPI verdict text ("band clear of zero" for $/% means; "band clear
  of 50%" for win rate), hover tooltips with exact lower/upper per band, and a
  "95 is X% wider than 90" caption.
- NULL/empty per KPI renders `--` plus the existing NO BAND YET standby note when
  n<2 (win rate clamps to 0–100%). Unknown KPI key falls back to Mean PnL $.

### 2. Chart grid (`#analysis-v2` ch1–ch5 in `analysis_v2.js`)

- Axes: min/max x tick labels, count/density y ticks, named zero line where the
  metric crosses it (return %, markout bps).
- Hover: `<title>` per bar/path with exact bin range + count/density; Monte Carlo
  median plus p01/p99 edge values. Native SVG titles only — no tooltip library,
  no new dependency, strings stay node-testable.
- Consistency: every card keeps its current data and layout but gets `statsLine`
  (µ / σ / SE or equivalent + sample n) and `foot` chips in the same order, badge
  states from existing band words only. Empty/insufficient-sample cards render an
  explicit "Unmeasured — n closes" note instead of an empty box.

## Non-goals (v2 candidates)

- Unified backend `metric_ci` envelope in `kpi.py`; per-path CI grouping
  server-side; CI shading on the histograms; poll-driven freshness/stale badges.

## Testing

- Node harness assertions per CI KPI: the adapter reads the correct payload field
  per key, plus a no-band case — each fails if the field mapping is wrong.
- Node assertions per chart card: axes ticks present, every bar/path carries a
  `<title>`, footers follow the µ/σ/n order, empty sample renders the Unmeasured
  note (all five cards, ch5 included), and a stale-then-empty repaint clears
  the old series. No golden-payload churn: `kpi.py` gains one additive field
  (`win_rate_ci90`) and existing goldens are unchanged.

## How to verify (hands-on, no pytest)

1. Open `http://127.0.0.1:8799`, go to Performance & Analytics, find the Average
   Close card — default still reads Mean PnL $ with the nested 90/95 bar.
2. Switch the KPI picker to Win Rate — band, zero reference, and verdict switch
   units; hover a band shows exact lower/upper. Fail looks like: units stay in $.
3. Switch to Mean Return % with fewer than two measured closes — card reads NO
   BAND YET, not zero. Fail looks like: a zero band.
4. Scroll to Statistical Distribution & Simulation — hover any histogram bar shows
   its bin range + count; Monte Carlo median hover shows values. Fail looks like:
   no hover text on bars.
