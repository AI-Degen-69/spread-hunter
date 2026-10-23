# Plan — Issue #787: decide whether to raise the EV / edge floor

Branch: `i787/decide-whether-to-raise-the-ev-edge-floor` | Issue: #787

## Right-sizing

- **Tier: Standard** — one new script, one new test file, one test added to an
  existing file, one audit document; no schema, API, or live-behaviour change.
- **Task types:** `Research` (the bucket analysis and the decision) + `Docs` (the
  decision record) + `Code` (the analysis script). No Design/UI, no Debug, no
  performance work.
- **Verification modes:** unit tests for the analysis logic (agent-run, focused file);
  operator hands-on for the surface (script output, one shadow rehearsal, dashboard).
- Spec is embedded below (no root `SPEC.md`): this repo keeps specification inside the
  task plan and the audit document, and the decision record *is* the durable spec.

## Intake note (CodeRabbit plan comment, read once)

**Adopted:** the pair/position unit `(run_id, condition_id)` with the *lowest leg edge*
as the bucket key; the `"negative"` bucket; bookkeeping closes excluded; the floor sweep
at 0.02/0.03/0.04/0.06 with the exact label list per floor; the confound splits
(size, implied pair cost, category); separate data-quality counts; the pre-committed
decision rule; the decision-record structure; "rehearsal / not evidence" labelling.

**Rejected, with reasons:**

1. **A new read-only loader, bucketing function and PnL attribution inside the new
   script.** `core_brain/fill_quality_report.py` already carries all three
   (`load_store` L461, `build_report` L597, `edge_bucket_for` L288, `score_ab` L382,
   `wilson_interval` L184, `DEFAULT_MIN_OBS` L51). A second copy is exactly the drift
   this repo has already paid for; the script imports them instead.
2. **Two simultaneous 5-minute shadow runs as the comparison.** They sample different
   markets, so the difference is not attributable to the floor. `score_ab` already
   replays gate-off vs gate-on over the *same* tape (docstring: "Same window, gate off
   vs on"). The plan keeps **one** shadow run, for the end-to-end proof that the floor
   loads and the loop exits clean.
3. **`_bucketize`/`edge_bucket_for` changed to emit a `"negative"` label.** That is a
   live-gate behaviour change (out of scope) and would silently un-block negative edges
   for any operator list that names `0.06+`. The analysis labels negatives itself; the
   gap goes in the record as a follow-up needing sign-off.
4. **Tests for gate boundaries / negative / missing edges.** Already covered by
   `tests/test_fill_quality_report.py` (bucket boundaries) and
   `tests/test_fill_quality_gate.py::test_evaluate_is_pure_and_empty_means_allow_all`.
   Only the untested, decision-bearing consequence is added (T2).
5. **The issue's `entry_allowed` and its line numbers.** No such function exists; the
   gate is `evaluate_fill` (L293) and the file's numbers have moved. Line numbers are
   re-derived below from the live tree.

**Resolved `[UNVERIFIED]` items (checked against the tree, not assumed):**

- `core_brain/kpi.py` exists; `bot_eval_stat_gate_threshold_pct` is a *different*
  mechanism (percent stat gate, config L244) and carries no per-edge outcome table.
- `fill_quality_report.py`: `EDGE_EDGES` L64, `_EDGE_LABELS` L236, `edge_bucket_for`
  L288, `evaluate_fill` L293, `propose_gate` L340, `score_ab` L382,
  `DEFAULT_MIN_OBS = 30` L51.
- **`edge_bucket_for(-0.01)` returns `"0.06+"`** (verified by execution). A negative
  edge is currently labelled as the *top* bucket by both the report and the live gate.
- `build_report`'s `_condition_outcomes` (L568) sums **every** close for a condition,
  including `venue_sync` bookkeeping rows, so `attr_pnl` must be recomputed over
  filtered closes — done by filtering `closes` before `build_report`, not by editing it.
- `closes` carries no pair/fill/token identifier (columns verified): attribution to a
  position is inferred per `(run_id, condition_id)`, never exact.
- `market_meta.resolve_market_meta` (L331) is offline: local `markets.json` /
  `market_universe.json` feed, then slug-keyword classification. Safe to call from a
  script.

## Data reality (read-only probes, plan time)

- `core_brain.order_registry.DEFAULT_DB_PATH` = `data/orders.db`, the production
  registry: **empty** — 0 orders / 0 fills / 0 quotes / 0 closes. There is no live
  history to analyse today.
- This worktree carries only `data/demo_orders.db` (44 orders / 10 fills / 3 closes,
  synthetic → smoke fixture, never evidence).
- The operator's main checkout carries the two edge-bearing tapes:
  - `data/01_shadow_23-10_20-16.db`, run `shadow-01`: 82 fills, 46 closes
    (merge 30 / settlement 2 / single_buy_exit 14).
  - `data/01_shadow_prudent_23-10_20-17.db`, run `shadow-01-prudent`: 91 fills,
    54 closes (merge 36 / settlement 1 / single_buy_exit 17).
- Existing report on `shadow-01` already shows: edge `0.02-0.03` n=17 dead-rate 13.3%,
  `0.03-0.04` n=55 dead-rate 5.6%, `0.04-0.06` n=3, `0.06+` n=7, no `0.00-0.02`
  fills at all. `--ab --block edge:0.02-0.03` → gate off −13.82 / on −2.11 /
  delta +11.71, 9 pairs excluded.
- No store contains a single negative-edge quote (0 in all three), so the negative
  bucket is exercised by fixtures only, and the record says so.

## Decision rule (locked before the numbers are read)

Raise the floor **only if all three hold**:

1. every bucket the floor removes has **≥ 30 closed positions** (`DEFAULT_MIN_OBS`);
2. removing them **improves total realized PnL on the same tape** (`score_ab` delta > 0,
   paired);
3. the low-vs-high direction holds in **all three** confound splits (median shares,
   median implied pair cost, category).

Otherwise the proposal is **"keep current"**, with the candidate floor and its numbers
named so a later run with more data can decide. A floor is expressed only as a
`BOT_ENTRY_FILL_QUALITY_BLOCKED_EDGE` label list (the contiguous labels below the
chosen boundary).

## Interface contract (locked)

`scripts/edge_floor_analysis.py`

```
python scripts/edge_floor_analysis.py [--db PATH] [--run-id ID] [--min-obs N]
                                      [--out DIR] [--json]
```

- `--db` default `core_brain.order_registry.DEFAULT_DB_PATH`; opened read-only through
  `load_store` (never `OrderRegistry`).
- `--run-id` default none (every run in that file).
- `--min-obs` default `DEFAULT_MIN_OBS` (30).
- `--out` default `reports/edge_floor_<UTC stamp>`; written only here.
- `--json` prints the report dict instead of text.
- Exit `0` on a readable store (including an empty one); exit `1` only when the store is
  `NO_DATA` / `UNREADABLE`, so a verify step can tell a bad path from a bad result.

Pure functions (all unit-tested, no I/O):

```python
position_rows(report) -> tuple[Position, ...]         # pair_key, bucket, edge, pnl, closed, shares
bucket_table(positions, min_obs) -> tuple[BucketRow, ...]  # n, closed, win rate+Wilson, mean, median, total
crossover(buckets) -> float | None                     # lowest boundary passing the sufficiency rule
floor_sweep(positions, quotes) -> tuple[SweepRow, ...] # kept/removed pairs+pnl, kept win rate, labels, quotes below floor
confound_splits(positions, categories) -> tuple[Split, ...]
```

Bucket rule: `"negative"` when the position edge `< 0`, `"unknown"` when no leg carries
an edge, else `edge_bucket_for(edge)`; the position edge is the **lowest** leg edge.
Win = position PnL `> 0`; a still-open position is counted and excluded from the
win-rate denominator.

## Tasks

### T1 — [Backend/Logic] S/M — the analysis + its tests

`scripts/edge_floor_analysis.py`, `tests/test_edge_floor_analysis.py`.
Depends on: nothing.

- Load with `load_store`, filter `closes` to exclude `close_methods.BOOKKEEPING_METHODS`,
  `dataclasses.replace` the `StoreLoad`, then `build_report`.
- Build positions from `report.fills` grouped by `pair_key` (bucket by lowest
  `edge_vs_mid`; PnL = sum of `attr_pnl`); emit the bucket table, the crossover, the
  floor sweep (2/3/4/6 cents) and the three confound splits; print the data-quality
  counts (orphan fills = fills whose order is missing, open positions, unknown/negative
  positions, unmatched closes) and the "rehearsal / not evidence" banner whenever `--db`
  is not `DEFAULT_DB_PATH`.
- Categories from `market_meta.resolve_market_meta(cid, closes=…, quotes=…)`.
- Tests reuse the fixture kit already in `tests/test_fill_quality_report.py`
  (`_write_ddl`, `_seed_order`, `_seed_fill`, `_seed_quote`, `_seed_close`, `_seeded_db`
  — repo precedent: `from tests.stat_541_fixture import …`). One test each for:
  negative/unknown/lowest-leg bucket keys; one position from two legs; multiple closes
  summed; bookkeeping close excluded and settlement kept; open position counted but out
  of the win-rate denominator; sweep conservation at every floor and the exact label
  list at 0.03; crossover needs ≥30 and stops at a losing bucket; `--run-id` filters;
  a run against `data/demo_orders.db` exits 0 and leaves the file's hash unchanged.

**Verify:** `python -m pytest -q tests/test_edge_floor_analysis.py` green; then
`python scripts/edge_floor_analysis.py --db data/demo_orders.db` prints a table with the
rehearsal banner.

### T2 — [Backend/Logic] XS — pin the single-buy consequence of the gate

`tests/test_fill_quality_gate.py`. Depends on: nothing.

One test: with the gate on and `("0.02-0.03",)` blocked, an UP intent at 0.025 is
dropped, the opposite DOWN intent at 0.10 is still submitted (the gate is **not**
pair-atomic), and a resting UP order comes back cancelled as `CANCEL_NOT_QUOTED`.
This is the risk the floor decision is balancing — a raised floor can manufacture the
single-leg state the strategy exists to avoid — and nothing currently pins it.

**Verify:** focused file green; the test fails if the gate is made pair-atomic.

### T3 — [Docs] M — the numbers and the decision record

`docs/audits/edge-floor-decision.md`. Depends on: T1, T2.

- Run the script on both shadow tapes (read-only, in place in the operator's checkout),
  and `score_ab` for each candidate floor on each tape; run **one** 5-minute
  `shadow_run` at the proposed settings (fresh store, own `--run-id`) to prove the floor
  loads and the loop exits clean — record exit code, logged errors, final heartbeat,
  open inventory.
- Apply the locked decision rule and write the record in the style of
  `docs/audits/expectancy-vs-mean-return.md`: method and its limits (rehearsal, inferred
  attribution, empty production registry, label-only floor, unsaved refusals, category
  from current metadata), the bucket table, the crossover, the confound result, the
  proposal with projected win rate / net PnL / posted-quote count change, the shadow
  comparison, the operator's live step, the undo, the $0 added dollars at risk, the
  four follow-ups (negative edge, missing edge, unsaved refusals, relative-edge view),
  and a "How to verify" block with no `pytest` and no test counts.

**Verify:** the document exists, every table is filled from a named store and run id,
and each number is reproducible by the command printed beside it.

## Checkpoints

- After T1: the script runs end to end on the demo store; numbers visible.
- After T2: the gate consequence is pinned.
- After T3: the decision is written and reproducible.

## Dependency graph

```
T1 ──┐
     ├──> T3
T2 ──┘
```

## Improvement proposal (one, adopted — simplification)

**Evidence (issue text, verbatim):** "Run the shadow harness at the current settings and
at the proposed threshold to compare decisions (spends nothing)".
**Evidence (repo, verbatim):** `score_ab` — "Same window, gate off vs on, judged on
total realized inclusive PnL."

Two fresh 5-minute runs sample different markets, so their difference is not the floor's
effect; the existing A/B replays both settings over one recorded tape and already returns
a decision-grade paired delta. **Adopted:** one shadow run for the end-to-end proof,
`score_ab` for the comparison. Rejected alternative recorded above (Intake note, item 2).

## Facts Station III must not re-derive

| Fact | Where |
|---|---|
| Gate consult, per intent | `core_brain/trader_loop.py` L266–283 |
| `CANCEL_NOT_QUOTED` | `core_brain/trader_loop.py` L87, recorded L321 |
| Gate settings + env | `core_brain/config.py` L181–182, L384–390 |
| `EDGE_EDGES` / labels / min-obs | `core_brain/fill_quality_report.py` L64 / L236 / L51 |
| Negative edge → `"0.06+"` | `edge_bucket_for(-0.01)` (executed) |
| Bookkeeping methods | `core_brain/close_methods.py` L114 → `("venue_sync",)` |
| Test fixture kit | `tests/test_fill_quality_report.py` L15–208 |
| Audit-doc style | `docs/audits/expectancy-vs-mean-return.md` |
