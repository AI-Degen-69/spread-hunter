# Paired admission shadow experiment: submarket admission vs shipped identity

**Status:** The admission trial feed, arm consumption with family attribution and
fallback guard, and the read-only admission analyzer are implemented with
synthetic coverage. **No long-running experiment has been started. Launch is an
operator decision.**

## Question and arms

Does admitting submarket lines (spread / over-under / team-prop) change shadow
portfolio outcomes enough to justify widening the identity gate beyond primary
Moneyline/Outright and Macro/Politics markets?

- **Control:** the shipped `identity_allowed` decision, unchanged.
- **Treatment:** at most one market per event cluster — the best eligible
  mainline member, else the best eligible submarket member as a fallback
  carrying the mainline member's reject reason.
- Blocked-keyword, resolved and unreadable rows stay refused in both arms. Volume
  pins to the permanent $125,000 bar; no depth/spread/volume trials combine.
- One rank pass scores the shared universe once, then emits both top-N feeds
  with one `snapshot_id` in `spread_hunter.paired-admission.v1`. The bundle is
  atomic; scan truncation or missing event identity refuses publication. A
  treatment-only guard keeps one fallback holding per event at a time.

## Pre-registered decision rule

Only the h3 (900 s) excess markout enters the verdict:

1. **Markout parity:** treatment mean h3 excess markout minus control mean is at
   least −0.01 price units per share.
2. **Uncertainty:** the lower bound of the joint paired 95% interval for that
   difference is above −0.01.
3. **Risk:** treatment maximum drawdown minus control maximum drawdown is at
   most $1 per $100 of starting bankroll.
4. **Naked-leg guardrail:** treatment single-buy-exit loss per fill notional
   (fills: `price * size` over run-owned fills; losses over closes with method
   `single_buy_exit` or `naked_exit`) does not exceed control.

`inconclusive` for any limitation: unfinished or short run, snapshot-set
mismatch, starting bankrolls more than one cent apart, missing or stale equity
marks, missing or changing cluster attribution, fewer than 30 event clusters
with a usable h3 mark in either arm, any due fill without an h3 excess markout,
or zero fill notional in either arm. PnL uplift never enters the verdict. Do not
stop early when a raw cumulative PnL line happens to lead. `inconclusive` never
justifies changing the shipped gate.

## Comparison design

1. Establish an isolated, unique experiment run ID, store, output directory, and
   statistics artifacts. Never re-use shadow-01/02/03 stores or `data/orders.db`.
   Keep the 100-hour target fixed for both arms.
2. Run the ranker once with `--paired-admission` into `runtime/trials/<id>/`.
   Inspect `paired_admission_markets.json` (`format`, `snapshot_id`, both arms,
   fallback reasons) before starting either loop.
3. Launch two credential-free shadow loops against that bundle, each with its own
   `--db`/`--run-id`, one `--paired-admission-arm control`, the other
   `treatment`. Identical bankroll, caps, quoting config, poll cadence, duration.
4. The read-only analyzer (`--trial-axis admission`, or auto from both runs'
   `trial_axis`) combines run-owned markouts with closes and equity marks.
   Missing attribution/marks, feed errors, unfinished/short runs, unequal
   bankrolls, unmatched snapshots, or thin coverage means **inconclusive**.
5. At the fixed 100-hour endpoint, make one final analysis (no repeated efficacy
   peeking). Adopt only if all four bars hold with no limitations; otherwise
   reject, or gather more data when inconclusive.

**Capacity caveat:** as with the depth trial, the two arms test selection policy
on equal per-arm bankrolls without modeling shared-capacity competition.

## Launch status and safety

I have not launched these loops. The existing shadow-01 remains untouched.
Paired shadow commands write only to new per-run shadow stores.

## How to run and verify when authorized

Ranker pass (read-only venue reads, isolated output):

```powershell
python -m scripts.filter_markets --top 20 --out-dir runtime/trials/<id> --paired-admission --full-scan
```

Inspect `format`, `snapshot_id`, both arms, and fallback reasons in
`paired_admission_markets.json` before starting either loop. Then launch two
shadow loops (operator decision — these start long-running processes):

```powershell
python -m core_brain.shadow_run --minutes 6000 --db data/<id>-control.db --run-id <id>-control --markets-path runtime/trials/<id>/paired_admission_markets.json --paired-admission-arm control
python -m core_brain.shadow_run --minutes 6000 --db data/<id>-treatment.db --run-id <id>-treatment --markets-path runtime/trials/<id>/paired_admission_markets.json --paired-admission-arm treatment
```

At the endpoint, generate the report (read-only over both stores):

```powershell
python -m scripts.paired_depth_report --trial-axis admission --control-db data/<id>-control.db --treatment-db data/<id>-treatment.db --control-run-id <id>-control --treatment-run-id <id>-treatment --output reports/paired-admission-<id>.json
```

Expect `decision: inconclusive` unless all four bars hold with complete
coverage; do not change the shipped identity gate on anything less.
