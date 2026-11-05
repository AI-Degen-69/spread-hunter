# Pre-registration: fresh 10-arm tweak rematch of Tournament #799 — 2026-11-05

Re-run all ten Tournament #799 arms from empty stores with each arm's concluded
worst-catalyst tweak applied, to test whether the tweaks fix the orphan-exit
bleed measured in the 2026-11-05 #799 live run (all arms negative, orphan exits
57-95% of closes; worst single close -$7.70 on static-floor-2c).

Per-trade σ measured in #799 (`docs/audits/live-migration-799.md`, measured
table) sizes the gate: arms need hundreds to thousands of closes before the
primary gate can move, so this run is read on risk shape (exit rate, worst
single, merge rate), never nominal PnL.

An arm is "fixed" for its tweak if its orphan-exit rate falls by at least a
third versus its #799 live-run value AND its worst single close is smaller in
magnitude than the #799 worst on a comparable sample (≥30 closes). The tweak
keys are unchanged from the #799 conclusions; only the values move.

Whole config comes from `configs/tournaments/i938-tweak-rematch_arms.json`,
envs applied:

| Arm | #799 catalyst (live run 2026-11-05) | Applied tweak for #938 |
|---|---|---|
| loose-baseline | shipped 3c deadband churn, 64.9% exits | `BOT_ORDER_REQUOTE_DEAD_BAND_USD=0.06` |
| tight-touch | aggressive 0.25x starves leg two, 61.9% | `BOT_QUOTE_TOURNAMENT_PRESET=balanced` |
| balanced-mid | balanced 2-4c gives up, 58.3% | `BOT_ORDER_REQUOTE_DEAD_BAND_USD=0.06` |
| wide-shield | 8c leash sizes losses, 64.7% | `BOT_RISK_STOP_ADVERSE_DROP_USD=0.050` |
| prudent-hybrid | 0.60x still orphans, 59.5% | `BOT_ORDER_REQUOTE_DEAD_BAND_USD=0.06` |
| static-floor-2c | static offset, single -$7.70 orphan | `BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON=1` |
| low-churn-deadband | 10c holds stale quotes, 57.9% | `BOT_ORDER_REQUOTE_DEAD_BAND_USD=0.06` |
| hair-trigger-2c | 2c stop factory, 94.9% exit rate | `BOT_RISK_STOP_ADVERSE_DROP_USD=0.045` |
| patient-stop-9c | 9c never fires, 61.1% | `BOT_RISK_STOP_ADVERSE_DROP_USD=0.060` |
| queue-clear-5m | 5m fills into drift, 57.1% | `BOT_ORDER_QUEUE_MAX_CLEAR_MIN=3` |

Launch (operator): menu tournament reconcile with `-Tourney 938`, or
`python -m scripts.shadow_tournament --issue 938 --arms-file configs/tournaments/i938-tweak-rematch_arms.json --minutes 1440`.
Cockpit page: `http://127.0.0.1:8799/cockpit/938` (dashboard restart required
to pick up the new page registration).
