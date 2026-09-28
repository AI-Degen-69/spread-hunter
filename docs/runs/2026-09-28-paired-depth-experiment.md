# Paired depth-bar shadow experiment: $250 vs $500

**Status:** The paired feed, run-scoped attribution/marking path, and read-only analyzer are implemented with synthetic coverage. **No long-running experiment has been started.** A defensible verdict still requires a valid independent paired-variance prior and complete real-run coverage; the existing independent shadows do not supply that prior.

## Question and arms

Does lowering the Market Filter top-three bid-depth bar from **$500** to **$250** improve shadow portfolio outcomes enough to justify admitting the additional thinner-book markets?

- **Control:** $500 top-three bid notional on **each** outcome token.
- **Treatment:** $250 top-three bid notional on **each** outcome token.
- A market must clear the cutoff on both sides; equality fails, matching the existing gate.
- All other selection gates and the ranker score are held constant. Paired mode pins 24-hour volume to the shipped $125,000 bar and refuses volume/spread/legacy-reward trials.
- One rank pass fetches and scores the shared universe once, then emits both top-N feeds with one `snapshot_id`. The bundle is atomic; scan truncation or missing stable Gamma event IDs/slugs refuses publication. The treatment-only population is retained for audit.

## Pre-registered decision rule

The operator chose:

1. **Practical uplift:** treatment must improve net portfolio PnL by at least **$1 per $100 of equal starting bankroll per 100 elapsed hours**.
2. **Uncertainty:** the paired 95% confidence interval for the policy-level uplift must be wholly above zero.
3. **Risk:** treatment's maximum drawdown may be at most **$1 per $100 of equal starting bankroll** above control.
4. **Naked-leg guardrail:** single-buy exit loss per fill notional must not worsen in treatment.

If sample size or interval precision is inadequate, verdict is **inconclusive**, not “no effect.” Do not stop early when a raw cumulative PnL line happens to lead.

## Comparison design

1. Establish an isolated, unique experiment run ID, store, output directory, and statistics artifacts. Never re-use shadow-01/02/03 stores. Keep the 100-hour target or another explicitly agreed window fixed for both arms.
2. Start the paired ranker/feed loop with control `$500`, treatment `$250`, and shipped volume gate. Each `runtime/trials/<experiment-id>/paired_markets.json` contains both arms atomically. `paired_depth_audit.jsonl` records each common snapshot and candidate sets.
3. Run two credential-free shadow loops against that same bundle, each with its own unique shadow store/run ID, one `--paired-depth-arm control` and the other `treatment`. Each loop reads only its arm. Durable shadow-only tables record selected snapshot, arm, cutoff, stable event family, outcome token map, per-order attribution, per-market liquidation marks, and total equity marks. Positions are replayed from run-owned fills/closes; they are not reconstructed by nearest scan or condition-wide inventory.
4. Give each arm the same starting bankroll, max-market/top-N limit, order and total-risk caps, quote/exit configuration, polling cadence, and wall-clock duration. Record effective configs at launch. No arm borrows capacity or starting balance from the other.
5. Observe hourly: process health, distinct market/event exposure, closes by path, single-buy exits, unresolved positions, fill notional, drawdown, rank snapshots, and feed/quote attribution. Keep all outcomes; do not cherry-pick pair merges.
6. The read-only report at `python -m scripts.paired_depth_report` combines realized closes with common-window start/end liquidation marks, computes drawdown from timestamped equity, measures single-buy exit loss per close cost basis/fill notional, and resamples paired event-family totals. Missing attribution/marks, invalid or stale coverage, feed errors, unfinished/short runs, unequal starting bankrolls, unmatched snapshots, or absent power prior means **inconclusive**. The analyzer only reads input SQLite stores; optional report output is a separate JSON artifact.
7. At the fixed 100-hour endpoint, make one final paired analysis (no repeated efficacy peeking). Normalize each arm to the same $100 starting bankroll and 100 elapsed hours. The cluster bootstrap samples event-family total paired uplift and reports its interval and number of clusters; quotes, fills, scans, or repeated markets from one event are not independent samples. Report coverage, missing/failed snapshots, raw closes, unresolved exposure and the full interval.
8. Estimate required cluster count from an **independent paired pilot** or defensible historical paired data: for a target uplift `delta` and standard deviation `sigma` of normalized paired cluster differences, use `n ≈ ((1.96 + 0.842) * sigma / delta)^2` for a two-sided 95% interval and 80% power, then validate using a cluster-aware method. Neither existing independently generated shadows nor `stat_gate.py`'s independent-close calculation supplies this variance. The pilot is preregistered separately in [2026-09-28-paired-depth-pilot.md](2026-09-28-paired-depth-pilot.md) — 72-hour window, acceptance criteria, the mechanical σ selection rule (point estimate at k ≥ 30 clusters, 75% chi-square upper bound at 20–29), and the four-point independence audit at freeze time. The report accepts a pilot sigma as an explicit input and otherwise remains inconclusive; supplying a number is not proof that the data source is independent, so it must be preregistered and audited by the operator before interpreting power. No arbitrary cluster-count floor substitutes for this. If no defensible variance estimate is available before launch, treat 100 hours as a screening run only and predeclare that an inconclusive result cannot justify changing the shipped bar. Adopt $250 only if all four registered decision conditions are satisfied; otherwise retain $500 or gather more independent data.

**Important capacity caveat:** Two independent stores each running the same $100 baseline bankroll test selection policy without competition. They do not faithfully model one shared bankroll split between feeds. Equal per-arm caps and the per-arm policy-level result avoid giving the treatment more capital than control, but do not simulate strategy-level competition for finite market slots. If the production decision must include that competition, add a deterministic shared-capacity replay or randomized market/event allocation after the paired screen, rather than silently allowing both arms to claim the same capacity.

## Readiness gate

The synthetic fixture exercises durable run attribution, event-cluster mapping, snapshot mismatch, missing/stale position marks, equity mark coverage, read-only behavior, PnL, drawdown, exit-loss denominators, and the inconclusive/no-prior state. Before starting a long rehearsal, do a short, explicitly authorized isolated shakedown and verify in its own store that:

- selected feed rows survive market lookup failures as visible coverage events;
- an order cannot be posted without a valid paired admission and stable event family;
- regular maker orders, taker completion orders, merges, single-buy exits, and terminal settlements are represented in the same run's attribution/replay;
- every shadow equity mark is valid or exposes why it is incomplete, and open positions are fully covered by bid depth;
- selected rows the loop never visited appear as `unvisited_selected_markets` events in `shadow_paired_feed_events` (the run audits this against its full feed set before marking itself finished); and
- the two arms observe the same snapshot set and config/bankroll/duration; and
- a report with no independent paired-variance prior is labeled inconclusive, never a pass/fail verdict.

Do not start the 100-hour run until the shakedown and the pilot-variance provenance are accepted. Do not use historical shadow-03 or shadow-06 as the paired variance pilot: they did not run both policies on the same ranked universe/time window.

## Launch status and safety

I have not launched these loops. Doing so would create two long-running public-book shadow rehearsals, write new stores/runtime files, and consume sustained network/CPU resources. The existing shadow-01 remains untouched. Paired shadow commands write only to new per-run shadow stores; never point them at `data/orders.db`.

Before a run is launched, inspect the freshly generated pair bundle and confirm:

- both arms carry the same snapshot ID and shipped non-depth gates;
- any snapshot ID one arm observed and the other did not (anywhere in either arm's wall clock, not just the overlap) is treated as a coverage failure by the report;
- paired run stores must be fresh: a run start is refused in a store that already holds orders, fills, quotes, closes, or a paired run row;
- both selected feeds are nonempty and at most the same top-N;
- each control row clears $500 on both outcome books;
- each treatment row clears $250 on both outcome books;
- the incremental treatment candidates are visible in the audit;
- launch config snapshots, stores, run IDs, and dashboard ports are all unique; and
- the synthetic report is inconclusive without a variance prior and has no missing synthetic coverage when one is supplied.

## How to run and verify when authorized

From PowerShell, use a **new** experiment slug in place of `<id>` and do not use menu reset/clean options:

```powershell
python -m scripts.filter_loop --out-dir runtime/trials/<id> --trial-depth 250 --paired-depth-control-usd 500
```

The first successful rank must write `runtime/trials/<id>/paired_markets.json`; inspect `format`, `snapshot_id`, `control_depth_usd`, `treatment_depth_usd`, `volume_gate_usd`, both counts, and the treatment-only candidate audit before starting either loop. A truncated universe or missing event identity must stop the ranker rather than produce an experiment bundle. Stop the paired filter at the end of the experiment.

After authorizing and verifying the bundle and capturing per-arm starting configs/stores, launch two separate shadow-loop processes using `--markets-path runtime/trials/<id>/paired_markets.json --paired-depth-arm control` and `--paired-depth-arm treatment`, each with a different `--db`, `--run-id`, and identical `--minutes`/risk configuration. Point each dashboard/observer at its own store. Verify the run's `shadow_paired_orders` and `shadow_paired_equity_marks` in that per-run database show attribution and valid marks; if marks are invalid or the snapshot IDs drift, stop and treat that interval as invalid.

At the fixed endpoint, an operator can generate the report with:

```powershell
python -m scripts.paired_depth_report --control-db <control-shadow.db> --treatment-db <treatment-shadow.db> --control-run-id <control-run-id> --treatment-run-id <treatment-run-id> --output reports/paired-depth-<id>.json
```

Without an audited independent pilot sigma, expect `measurement_status: inconclusive` and do not change the shipped depth bar.
