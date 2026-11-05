# Pre-registration: second-round 10-arm tweak tournament — 2026-11-05

Follow-up to #938. The #938 interim shadow read (~200 cycles/arm, live-sports
lane) came back negative on all ten arms: orphan exits 47–88% of closes, worst
singles bigger than #799 in 9 of 10 arms, one shared driver — one-sided BUY
fills in running books (NBA UTA-NOP recurs as the worst market), after which
the hedge is unreachable under the $0.99 pair cap or a stop fires. Merges pay a
fixed ~+$0.60 while exits lose $1–5, so nothing shows direction until
one-sided fills stop.

Each arm gets exactly one notch off its own #938 worst parameter (evidence and
direction recorded per arm in `configs/tournaments/i940-second-round-tweaks_arms.json`).

## Starting state

Every arm starts from zero: a fresh run directory with empty stores (0 trades)
and `BOT_QUOTE_BANK_BASELINE_USD=100.0` pinned in each arm's `env`, matching
the #938 $100 baseline so the two runs read on the same money.

## Gate (unchanged from #938)

An arm is "fixed" for its tweak if its orphan-exit rate falls by at least a
third versus its #938 interim value AND its worst single close is smaller in
magnitude than the #938 worst on a comparable sample (≥30 closes).

| Arm | #938 interim | Applied tweak for #940 |
|---|---|---|
| loose-baseline | 6c holds resting leg stale, exits 50.9% (27/53) | `BOT_ORDER_REQUOTE_DEAD_BAND_USD=0.04` |
| tight-touch | balanced fills leg one rich, exits 47.2% (25/53) | `BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS=3` |
| balanced-mid | 4c clamp fills rich, exits 51.0% (26/51) | `BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS=5` |
| wide-shield | 5c stop fires on noise, exits 72.0% (36/50) | `BOT_RISK_STOP_ADVERSE_DROP_USD=0.060` |
| prudent-hybrid | 6c excursions strand legs, exits 52.0% (26/50) | `BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS=5` |
| static-floor-2c | unbounded tracking whipsaws, exits 51.0% (25/49) | `BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS=6` |
| low-churn-deadband | stalest quotes, exits 50.0% (27/54) | `BOT_ORDER_REQUOTE_DEAD_BAND_USD=0.04` |
| hair-trigger-2c | 4.5c stop factory, exits 87.5% (42/48) | `BOT_RISK_STOP_ADVERSE_DROP_USD=0.055` |
| patient-stop-9c | 6c stop factory, exits 75.0% (36/48) | `BOT_RISK_STOP_ADVERSE_DROP_USD=0.070` |
| queue-clear-5m | tails reach -$4.70, exits 48.9% (22/45) | `BOT_ORDER_QUEUE_MAX_CLEAR_MIN=2` |

Launch (operator, only after #938 finishes — same arm ports 8801–8810):
menu tournament reconcile with `-Tourney 940`, or
`python -m scripts.shadow_tournament --issue 940 --arms-file configs/tournaments/i940-second-round-tweaks_arms.json --minutes 1440`.
Cockpit page: `http://127.0.0.1:8799/cockpit/940` (dashboard restart required
to pick up the new page registration).
