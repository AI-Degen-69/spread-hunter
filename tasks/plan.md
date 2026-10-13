# Plan — #515 [ADD] Queue realism analytics for trade-through depth and fill latency

Branch: i515/add-queue-realism-analytics | Issue: #515
Size: Small (2 source files; all interface decisions already settled on the issue) | Type: Code + Docs
Labels: `enhancement`, `ready-for-agent`, `family:execution-telemetry`
Spec: embedded below (Small tier — no `SPEC.md` ceremony). `SPEC.md` on `main` is #514's merged artifact and is left untouched. Guardrails: `CONSTRAINTS.md`, rewritten for this branch (the #514 text is branch-scoped by its own header and stays in git history).

## Pipeline bookkeeping

1. Tree was clean and on `main`, synced with `origin/main` (verified at planning time).
2. `gh issue edit 515 --add-assignee @me` — claimed.
3. Branch from HEAD: `i515/add-queue-realism-analytics`. Slug derivation: title → `add-queue-realism-analytics-for-trade-through-depth-and-fill-latency` (64 chars) exceeds the 50-char cap, so it is cut at a word boundary rather than mid-word.
4. `tasks/plan.md` (this document), `tasks/todo.md`, `CONSTRAINTS.md` written on this branch. Both task files held #514's merged plan; this issue has no prior plan, so they are replaced, not appended to (Step 6.7 reconciliation applies per-issue only).
5. Step 0C: #515 carries no `quick-fix` label → no gate, no lane offered.

## CodeRabbit intake note (read once; the `<!-- <rawResChunk><planningResult> -->` echo was skipped)

**Adopted**
- The test file path: `tests/test_queue_realism_analytics.py` — the ticket names it, and the AC's runnable check is that file.
- Its frozen-dataclass input shape (Design Choice 2) and "the caller supplies pre-aggregated records; no DB or tape ingestion here" (matches the ticket's own operator decision).
- Its codebase findings that survive the reshape, each re-verified in the tree this session: `quotes.fill_ts` is the earliest fill (`order_registry._attribute_fill_to_quote`), quote/queue timestamps are seconds while order/fill timestamps are milliseconds, and no `queue_depth_ahead` symbol exists (the ticket's own vocabulary renames it; this plan adds no queue field at all).
- Its "about 100 lines, stdlib + `_finite` only" sizing (Design Choice 1's rationale, not its conclusion).

**Rejected**
- **Its entire task list (Tasks 1.1 and 1.2).** `touch_to_fill`, `entry_depth`, `drain_status`/`queue_drain` + phantom-fill flag, and `fill_latency_depth_correlation` + `_pearson` are the four metrics the ticket's settled "Operator decisions" list **removes** as superseded by the trade-through fill fix. Building them would ship exactly the tautologies the ticket exists to kill.
- **New module `core_brain/queue_realism_analytics.py`.** Its stated reason was keeping the `build` payload unchanged, but the payload is unchanged either way (see the contract: no `build()` key). Against it: the ticket's Scope says "Housing the routines in `core_brain/statistical_analytics.py` alongside the other telemetry sections", and #514's plan rejected the same shape for the same reason (a sibling file needs another copy of shared constants and of the band contract). **Decision: extend `statistical_analytics.py`.**
- **Its `QueueRealismRecord` field set** (`touches`, `drained_volume`, `filled`, `size`) — those fields exist only to feed the removed metrics. The settled vocabulary is `trade_through_depth_cents` / `fill_latency_sec`.
- **Its `>= 1.0` drain rule, phantom-fill semantics, order-level denominators, and its twelve "contradictions"** about `queue_ahead` — all moot post-reshape, which removed every queue-based metric.
- **Its Task 1.3 verification text**, which hands the operator `python -m pytest` steps. This repo forbids that: the operator's verification is hands-on (see the block below), and pytest is agent-run background only.

**[UNVERIFIED] remaining:** none. Every seam cited here was read in the tree this session.

## Step 5 — one improvement proposal (evidence-first, adopt-by-default: hardening)

Evidence, verbatim from the ticket: *"Traffic-light bands: Trade-through depth, **median** across fills, in cents"* and the AC line *"Trade-through depth (median, p75) computed as a pure deterministic function over recorded fills."* The banded number is the median, so the median's definition is load-bearing.

The repo holds two percentile conventions. `scripts/family_probe_report.py:68 _percentile` is nearest-rank (`ceil(fraction * n) - 1`) so a thin family still reports; `statistical_analytics.monte_carlo`'s local `pct` is nearest-rank on the index too. Under nearest-rank, the median of `[10, 20, 30, 40]` is **20.0**, while `statistics.median` — used by `kpi.median_queue_ahead`, `kpi.median_seconds_to_fill`, `cancel_report` and `statistics_report` — gives **25.0**. One word, two numbers, in one repo, on the value that carries the colour.

**Proposal:** compute the percentiles by linear interpolation at index `fraction * (n - 1)`, so `p50` is literally the median the rest of the repo means, and pin it with a test that fails under nearest-rank. `n = 1` returns the value itself (which is why the script helper exists at all); `n = 0` returns `None`. Classification: **hardening / simplification — adopted by default**, folded into T1.

A second, cheaper decision is recorded as contract rather than as a proposal, because the ticket settles it: **the band is computed in Python and shipped as data**, following `friction_band`'s own frozen rule — *"The thresholds live here and reach the dashboard as data, so the renderer maps a band name to a colour and holds no threshold of its own -- a renderer that carried its own copy is how a screen starts disagreeing with the payload it is drawing."* `trade_through_depth_band()` is therefore public, and the depth dict carries `band`.

## Frozen interface contract

All functions are pure: values in, values out. No clock, no socket, no SQLite, no signer, no `random`. No input is mutated. No value is ever `NaN`/`Infinity`; every float is rounded to 6 decimals and the result is `json.dumps(..., allow_nan=False)`-safe. Missing stays missing — never `0`, never clamped.

```python
# core_brain/statistical_analytics.py

# The ticket's settled depth bands. The band scores the MEDIAN across cleared fills:
# below one cent the level cleared on the tape we sat on; 1-3c the tape had to move past
# us; above 3c the quote sat far from where the market actually traded.
TRADE_THROUGH_GOOD_BELOW_CENTS = 1.0            # [0, 1.0)   -> "good"
TRADE_THROUGH_AVERAGE_AT_OR_BELOW_CENTS = 3.0   # [1.0, 3.0] -> "average"; (3.0, inf) -> "bad"

@dataclass(frozen=True)
class ClearedFill:
    """One credited fill, its resting price, and the print that cleared it."""
    order_id: str
    order_price: Optional[float] = None
    clearing_price: Optional[float] = None
    placed_ts: Optional[float] = None
    filled_ts: Optional[float] = None

def _percentile(values: Sequence[float], fraction: float) -> Optional[float]: ...
def trade_through_depth_band(median_cents: Optional[float]) -> str: ...  # good|average|bad|unmeasured
def trade_through_depth(fills: Sequence[ClearedFill]) -> dict: ...
def fill_latency(fills: Sequence[ClearedFill]) -> dict: ...
```

Return shapes (frozen):

```python
trade_through_depth -> {"count", "missing", "not_trade_through",
                        "median_cents", "p75_cents", "band"}
fill_latency        -> {"count", "excluded", "median_sec", "p90_sec"}   # deliberately no band
```

Invariants the tests pin: `count + missing + not_trade_through == len(fills)` for depth; `count + excluded == len(fills)` for latency; a fill is measured for depth only when both prices are finite and `clearing_price < order_price`; a wait is measured only when both timestamps are finite and `filled_ts >= placed_ts`.

Not built, deliberately: no combined summary entry point (no caller exists), no `units` key (`median_cents` / `median_sec` already name their unit), no queue-depth distribution function (see contradictions 7), no `build()` key.

## Spec (embedded — Small tier)

**Goal.** Score the two behaviours the shadow sim can honestly observe now that fills are credited only on tape trade-through: how deep the tape had to go below our price, and how long the quote waited.

**Acceptance criteria → where they land**

| AC | Where |
| --- | --- |
| Trade-through depth (median, p75) pure and deterministic over recorded fills | T1 `trade_through_depth` + `_percentile` |
| Fill latency (median, p90) from the existing quote and fill timestamps | T2 `fill_latency` |
| Bands match the settled table; queue depth and latency unbanded | T1 `trade_through_depth_band` (+ `band` in the depth dict); `fill_latency` returns no band; no band is attached to `queue_ahead` anywhere |
| Cleared-fill, empty-run, zero-division, missing-timestamp cases in unit tests that fail without the change | T1/T2 test cases below (RED first: `ImportError`); the empty/single-sample guards are the zero-division class in the settled scope |
| No network or signer side-effects; no fill logic or LIVE path touched | Stdlib + `_finite` only; `CONSTRAINTS.md` names the must-not-modify files; focused `tests/test_shadow_fills.py` run guards it |
| Runnable: `python -m pytest -q tests/test_queue_realism_analytics.py` | T1/T2/T3 focused runs (agent-run; never handed to the operator) |

**Edge cases.** Empty list; single fill; both prices present but not a trade-through; exactly one price missing; `None`/`NaN`/`inf` in any numeric field; equal timestamps (0.0 wait is measured — a same-second fill is a measurement, not a missing one); clock skew (`filled_ts < placed_ts`); prices present with no timestamps, and timestamps present with no prices (the two metrics are independent); reversed input order (determinism); input list not mutated.

**Out of scope.** The fill model (`shadow_fills.credit_fills`), `shadow_exec`'s crediting path, DB/tape ingestion, any band on `queue_ahead`, fill-model calibration (#474), the validation ladder (#470/#471), LIVE execution, and any dashboard or report consumer (recorded as the follow-up in T3).

## Contradictions & risks (all verified in the tree at planning time)

1. **The AC's phrase "over recorded fills" is not yet satisfiable — the clearing print is not recorded anywhere.** `shadow_fills.credit_fills` (`core_brain/shadow_fills.py:96-146`) credits `ShadowFill(local_id, token_id, o.price, o.remaining)` — our own price — and `_credit_market` (`core_brain/shadow_exec.py:685-695`) writes `FillRecord(price=f.price, venue_ts=now_ms, recorded_ts=now_ms)`. The strictly-below prints live only in the local `traded` dict and are dropped when the function returns. `queue_marks` records tape volume only at the level we rest at (`shadow_exec.py:143-159`), never below it. **Consequence:** the two functions ship with no production caller; a later ticket must capture the closest below-print at credit time. Operator-confirmed scope: pure functions + tests now, gap recorded in T3.
2. **The trade-through fix has landed.** `83c8cbd fix(shadow): credit fills only on trade-through, remove exact-price path (#527)` is on `main`, so the ticket's "assumes it lands" precondition holds; the old exact-price consumption path really is gone.
3. **Shadow fill timestamps are recognition times, not print times.** `_credit_market` sets `now_ms = int(now_fn() * 1000)` and uses it for both `venue_ts` and `recorded_ts`, so a wait measures placement-to-settlement-recognition — an upper bound — and fills credited in one cycle can share a latency. Documented in the docstring; not fixed (fill-model change = out of scope).
4. **`quotes.fill_ts` is milliseconds while `quotes.ts` is seconds.** `order_registry._attribute_fill_to_quote` (`:1210-1238`) sets `fill_ts = MIN(COALESCE(venue_ts, recorded_ts))`; `kpi.py:1447-1449` divides by 1000 before subtracting. The contract's `filled_ts` is seconds and the caller converts.
5. **This module deliberately diverges from `kpi`'s clamp.** `kpi.py:1449` wraps the wait in `max(0.0, ...)`; `fill_latency` excludes a negative wait instead, because clamping manufactures a "filled instantly" reading from a clock artifact. Recorded here so a reviewer sees it as a decision, not an oversight.
6. **The ticket's "zero-division" AC line has no ratio left in the settled scope.** Both metrics are subtractions plus percentiles; the only zero-denominator class remaining is the empty/single-sample percentile (`fraction * (n - 1)`, and `statistics.quantiles`, which needs two points). Tests cover `n = 0 -> None` and `n = 1 -> the value itself`, and assert no `ZeroDivisionError`.
7. **No queue-depth metric is added here** (operator-confirmed). The descriptive readers already exist: `kpi.median_queue_ahead` (`kpi.py:2326`), `core_brain/cancel_report.py:87`, and `scripts/queue_report.py`. Every `queue_ahead` name stays unchanged and unbanded.
8. **CodeRabbit's plan is stale by one reshape.** It was produced against the pre-reshape scope; its Assumptions/Risks items about `touches`, `drained_volume`, and `queue_depth_ahead` describe inputs the reshape deleted, not gaps in this plan.
9. **Units landmine for the follow-up.** `orders`/`fills` timestamps are ms, `quotes`/`queue_marks`/`markouts` are seconds; a future ingestion ticket must convert once, in one place.

## Dependency graph & tasks

```
T1 (percentiles + depth + band) ──> T2 (fill latency) ──> T3 (decision record + focused runs)
        ^                                    ^
        └── shares the record and the helper ┘
```
T2 depends on T1 (reuses `ClearedFill` and `_percentile`). T3 depends on T1 + T2. Risk-first: the percentile definition and the band edges carry the only real uncertainty (they are where the ticket is thin), so T1 lands first; the latency function is arithmetic once the record exists.

Completion state (Station III owns the `[x]` marks):

- [x] T1 — record, percentile helper, depth + band, with their tests
- [x] T2 — fill latency, with its tests
- [x] T3 — decision record and the focused regression runs

Built as planned, on three commits (plan artifacts, T1, T2, T3), with one recorded
deviation: T1's `p75` expectation was corrected from `4.75` to `4.25` at build time (the
plan's blend weight was wrong; the arithmetic was re-derived in the test before the
implementation existed). Nothing outside `core_brain/statistical_analytics.py`,
`tests/test_queue_realism_analytics.py` and the two documentation files was touched, and
`build()` keeps every key it had. Focused runs at T3: 179 passed across the five files
named in `CONSTRAINTS.md`.

### T1 — `ClearedFill`, `_percentile`, `trade_through_depth` + band · S · `[Backend/Logic]`
- Files: `core_brain/statistical_analytics.py`, `tests/test_queue_realism_analytics.py` (NEW).
- Builds: `TRADE_THROUGH_GOOD_BELOW_CENTS`, `TRADE_THROUGH_AVERAGE_AT_OR_BELOW_CENTS`, `ClearedFill`, `_percentile`, `trade_through_depth_band`, `trade_through_depth`, plus one module-docstring paragraph (the section joins `fill_path_edge` and `rescue_friction` as "always present, no input synthesised").
- Helper skill: `test-driven-development` (RED first: the import cannot resolve), then `incremental-implementation`.
- Test cases (exact values): cleared pairs `(0.50, 0.48) -> 2.0¢`, `(0.50, 0.495) -> 0.5¢`; percentiles over depths `[0.5, 2.0, 4.0, 5.0]` → `count=4`, `median_cents=3.0` (fails under nearest-rank, which gives 2.0), `p75_cents=4.25` (index `0.75 * 3 = 2.25` → `4.0 + 0.25 * (5.0 - 4.0)`, corrected from `4.75` at build time: the plan's first figure had the blend weight wrong), band `average`; band edges `0.0 -> good`, `0.99 -> good`, `1.0 -> average`, `3.0 -> average`, `3.01 -> bad`, `None`/`NaN`/`inf -> unmeasured`; empty input → all counts `0`, both percentiles `None`, band `unmeasured`, no `ZeroDivisionError`; single fill → `median_cents == p75_cents`; missing prices counted in `missing`; non-trade-through rows (equal and above) counted in `not_trade_through`; partition invariant; determinism; no mutation; `json.dumps(..., allow_nan=False)`.
- Depends on: —
- Verify (agent-run): `python -m pytest -q tests/test_queue_realism_analytics.py`

### T2 — `fill_latency` · S · `[Backend/Logic]`
- Files: `core_brain/statistical_analytics.py`, `tests/test_queue_realism_analytics.py`.
- Builds: `fill_latency`, and the docstring paragraph stating why it carries no band and why it excludes rather than clamps (contradiction 5).
- Helper skill: `test-driven-development`.
- Test cases (exact values): waits `5.0, 10.0, 15.0` → `count=3`, `median_sec=10.0`, `p90_sec=14.0`; empty input → `count=0`, `excluded=0`, both `None`, no `ZeroDivisionError`; single wait → `median_sec == p90_sec`; missing timestamps (`None`/`NaN`/`inf`) → `excluded`; clock skew (`filled_ts < placed_ts`) → `excluded`, explicitly not `0.0`; equal timestamps → measured `0.0`; independence of the two metrics (prices without timestamps, and the reverse); no `band` key; partition invariant; determinism; no mutation; `json.dumps(..., allow_nan=False)`.
- Depends on: T1
- Verify (agent-run): `python -m pytest -q tests/test_queue_realism_analytics.py`

**Checkpoint after T2:** both metrics measured, focused suite green, nothing outside the two files touched. Reported as one line (Mode A).

### T3 — decision record and focused regression runs · XS · `[Docs]`
- Files: `docs/issues/515-noticed-but-not-touching.md` (NEW), `tasks/plan.md` / `tasks/todo.md` completion marks.
- Builds: the operator-facing note recording (a) the missing clearing-print data path — the metrics have no production caller until a later ticket captures the closest below-print at credit time, as an additive telemetry column/table next to `queue_marks`; (b) the recognition-time caveat from contradiction 3; (c) the deliberate divergence from `kpi`'s clamp; (d) the follow-up's unit conversion (ms → s) to do in one place.
- Agent-run focused checks, all of which must pass (`python -m pytest -q` each): `tests/test_queue_realism_analytics.py`, `tests/test_statistical_analytics.py` (guards `build()` and `_finite`), `tests/test_fill_path_analytics.py` and `tests/test_rescue_friction_analytics.py` (sibling sections of the same module), `tests/test_shadow_fills.py` (proves fill logic is untouched). The full suite stays with CI (Ubuntu + Windows) as the merge gate.
- Depends on: T1, T2
- Verify (agent-run focused runs above; operator block below).

**Sub-issues:** skipped and recorded rather than invented — sub-issue mapping is the `wayfinder:map` flow, and #515 carries no `wayfinder:map` parent (labels: `enhancement`, `ready-for-agent`, `family:execution-telemetry`). The graph above is this session's tracker.

**Persona skips (recorded, not simulated):** `code-explorer` — Small-tier work whose seams were read directly (`shadow_fills.credit_fills`, `shadow_exec._credit_market`, `order_registry` `quotes`/`fills` schemas, `kpi` waits, `statistical_analytics` bands); no legacy path needed tracing. `type-design-analyzer` — the contract adds one frozen record and pure functions returning dicts of floats/`None`, matching every sibling section; nothing to encapsulate.

## How to verify (operator — hands-on; no pytest anywhere in this list)

There is deliberately no dashboard card and no report file in this change: the registry records no clearing print yet (contradiction 1), so anything wired today would read `unmeasured` forever. What you can touch is the two public functions, through the interface the follow-up ticket will use.

1. In the repo root, run:
   `python -c "from core_brain.statistical_analytics import ClearedFill, trade_through_depth, fill_latency; rows=[ClearedFill('a',0.50,0.48,1000.0,1005.0),ClearedFill('b',0.50,0.47,1000.0,1015.0),ClearedFill('c',0.50,None,None,None)]; print(trade_through_depth(rows)); print(fill_latency(rows))"`
   Expect `count=2`, `missing=1`, `not_trade_through=0`, `median_cents=2.5`, `p75_cents=2.75`, `band="average"` for the depth, and `count=2`, `excluded=1`, `median_sec=10.0`, `p90_sec=14.0` for the latency. Any `0` where the third row is missing, or a `band` key inside the latency dict, is the failure signal.
2. Flip the same call to a deep trade-through — `ClearedFill('d', 0.50, 0.35, 1000.0, 1005.0)` (15¢ below) — and expect `band="bad"`; at `0.4999` expect `band="good"`. The colour must follow the number, never a hardcoded label.
3. Confirm nothing else in the running stack moved: `python -m core_brain.shadow_run --minutes 5 --db data/515_shadow_<stamp>.db` behaves exactly as before (identical payload, no new report, no new card) — this change measures, it does not alter any fill, sizing or quoting behaviour.
4. Read `docs/issues/515-noticed-but-not-touching.md` for the one thing that needs your call later: capturing the clearing print price at credit time so these numbers stop being hand-fed.

No money is at risk in any of these steps: nothing here reaches the venue, signs, or places an order, so there is no undo action.
