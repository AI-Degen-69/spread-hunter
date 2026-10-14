# Baseline run: loose preset reference, 2026-10-14 (#569)

The reference run for every later tightening test. Everything below is
measured from `data/i569_tournament-1_baseline-1.db` and the tournament
results file - no estimates, no projections. Where a figure could not be
measured it says so.

**Read this before starting a preset comparison.** A tweak is only
interpretable against a baseline taken under the same conditions, and this
is that baseline.

---

## Setup

| | |
| --- | --- |
| Run id | `shadow-569-tournament-1-baseline-1` |
| Arm slug | `baseline-1` |
| Store | `data/i569_tournament-1_baseline-1.db` |
| Plan | `runtime/tournaments/i569_tournament-1.json` |
| Results | `reports/tournaments/i569_tournament-1_results.json` |
| Arms file | `runtime/tournaments/i569_baseline_arms.json` (`[{"name": "baseline"}]`) |
| Started | 2026-10-14 ~19:05 local |
| Duration | 5 minutes (`--minutes 5`) |
| Rotation interval | 1 s (`--interval 1`, at the rotation ceiling) |
| Preset | `baseline` via `HUNTER_TOURNAMENT_PRESET` (dynamic offset ON, 0.5x range, 1c floor / 20c ceiling, no widen snap-back, 15c score window, queue hold 500, below-target hold 8c, stop $0.045) |
| Universe | `runtime/markets.json` ranked same-day (`filter_markets --top 20` -> 5 spread markets, $760 capital) |
| Money | None spent: shadow rehearsal, no signer |

## Measured totals

| | |
| --- | --- |
| Fills | 0 |
| Filled orders | 0 |
| Closes | 0 (merges 0, single-leg exits 0) |
| Realized PnL | +$0.0000 |
| Arm exit code | 0 (`no_fills`) |

Per-market (`pair_fill_report.py` on the store): 6 markets quoted
two-sided, no leg filled on any of them — ONE-leg 0.00%, BOTH-legs 0.00%.

## What the log shows

- 4 of the picked markets left the active universe mid-run
  (`market_dropped` lifecycle stops); the CS2 market kept 2 quotes resting
  at target spread to the deadline.
- Late cycles refused new pairs on the completable-cost gate
  (maker fill + taker completion at exactly $1.0000) — the gate held, no
  zero-profit pair assembled.
- The queue-clear gate stayed record-only throughout (0 reported).

## Reading

A 5-minute window on these books produced no fills — that is the measured
fact, not a failure. Longer reference runs on this preset should expect the
same shape (long no-fill stretches, cost gates holding) unless the universe
or the window changes. Compare tweak arms against these numbers, not
against hopes.
