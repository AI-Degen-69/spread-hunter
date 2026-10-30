# Sharpe Ratio Convention (Issue #875)

Locked definition for every surface that reports a Sharpe number. The old
cockpit "Sharpe proxy" (`mean_pnl / std_pnl × 10`, a per-trade ratio scaled by
ten) is retired; nothing may reuse the Sharpe label for it.

## Formula

> Sharpe Ratio = mean(portfolio excess return) / stdev(portfolio excess return),
> annualised × √(periods per year)

Source of truth: `core_brain/sharpe.py` (`SHARPE_FORMULA_TEXT`).

## Timing

- Fixed **5-minute clock buckets** (`bucket_seconds=300`), from the first point
  through the first grid time at or after the last point.
- Carry the last known value forward; quiet periods count as zero-return steps.
- Step return = `end/start − 1` on portfolio value (starting money + running
  realised PnL), not on per-trade PnL.

## Annualisation

- 365-day year: `× √(31_536_000 / bucket_seconds)`.
- Assumes steps are independent; short samples are noisy — treat small-N ratios
  as indicative, not decisive.

## Risk-free rate

- `BOT_SHARPE_RISK_FREE_RATE`, yearly decimal ratio (`0.05` = 5%/yr), default `0`
  (USDC). Allowed `0.0..1.0`; anything else fails at startup with a named error.
- Per-step conversion by compounding: `(1 + annual)^(step/31_536_000) − 1`.
- Never a live market rate (out of scope); constant only.

## Capital base and fallback

1. Arm env `BOT_QUOTE_BANK_BASELINE_USD` when the plan records it.
2. Otherwise the cockpit process default from `config.load()`.

Published per arm as `sharpe_capital_basis` (`arm_env` / `assumed_default`).
The default is an assumption, not a recorded fact — a resumed arm may have run
under different money. `shadow_paired_runs` is deliberately NOT consulted: arm
DBs need not carry that table.

## Unmeasurable cases

`None` (rendered `NO_DATA`): no valid points, fewer than 2 step returns, or zero
spread (flat equity). Never `NaN`, never placeholder `0.0`.

## Threshold review

Gates keep their numbers (`SHARPE_TARGET = 2.70`, `SHARPE_MARGINAL = 2.20`) but
those were tuned for the old proxy — they now apply to a different metric.
Re-check against future runs; do not retune on history (no backfills — future
runs only).

## What is NOT this metric

- `core_brain.kpi.compute_trade_analytics["sharpe_ratio"]` stays per-trade and
  unannualised (trades are not daily observations). Different statistic, same
  family name — do not interchange them.
- No trader use; the execution path never reads this module.

## Open follow-ups (noticed, not touching in #875)

| ID | Candidate | Evidence | Status |
| --- | --- | --- | --- |
| N1 | `matrix_rationale` arm narratives still quote old proxy numbers (e.g. "Sharpe 4.67") | `scripts/tournament_cockpit_server.py` matrix rationale copy | open |
