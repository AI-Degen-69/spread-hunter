Branch: i474/fill-model-calibration-cli | Issue: #474

# Plan — Step 4: fill-model calibration (simulated vs observed fill-rate)

**Issue:** [#474 — [VALIDATE] Step 4 - Fill-model calibration](https://github.com/AI-Degen-69/spread-hunter/issues/474)
**Parent:** #470 (validation ladder) · **Blocked by:** #471 (needs ≥60 closes of live data)
**Size:** Standard (2 files, one module, one architectural decision — a read-only text/JSON
report over two SQLite stores) · **Task type:** Code

## What is being built, in one paragraph

A read-only command, `python -m core_brain.fill_calibration --shadow-db <path> --live-db
<path>`, that opens both SQLite files with `mode=ro`, takes one record per **resting maker
bid**, buckets those records by price and by queue-ahead, and prints the shadow fill rate
next to the live fill rate with a signed gap (`shadow − live`). Two rates per bucket: share
fill fraction and order any-fill rate. Nothing is written, nothing is repaired, nothing
reaches the venue.

## Locked seams (verified against the working tree at plan time)

| Seam | Location now | Use |
| --- | --- | --- |
| `orders` schema | `core_brain/order_registry.py` CREATE TABLE — `id, order_id, condition_id, token_id, side, price, original_size, status, posted_ts, last_polled_ts, pair_id, …, run_id` | cohort source |
| `fills` schema | same file — `trade_id, order_uuid, size, price, venue_ts, recorded_ts, run_id` | numerator |
| `quotes` schema | same file — `id INTEGER PRIMARY KEY AUTOINCREMENT, ts, …, queue_ahead, filled, local_id, run_id` | the "is a rest" test + queue value |
| Join | `fills.order_uuid = orders.id = quotes.local_id` | confirmed in `kpi.py` and `shadow_exec.py:899` |
| Read-only connect | `core_brain/run_scorer.py:128-131` — `f"file:{path.resolve().as_posix()}?mode=ro"`, `uri=True` | copy the pattern |
| Status words | `core_brain/run_scorer.py:119,137` — `NO_DATA`, `UNREADABLE`; success word `OK` (`:315`) | copy the vocabulary |
| Queue boundaries | `core_brain/kpi.py:1723` — `[(0,1),(1,50),(50,150),(150,400),(400,1e12)]`; labels built at `:1731` by `f"{lo:.0f}-{hi:.0f}"` / `f"{lo:.0f}+"` → `0-1 … 400+` | copy, do not import |
| Test seed idiom | `tests/test_stat_validation_artifacts.py:314-327` — `OrderRegistry(db_path=tmp)`, `log_quote(QuoteRecord(...))`, `create_order(OrderRecord(...))`, `record_fill(FillRecord(...))` | fixtures |
| Shadow fill model | `core_brain/shadow_fills.py:72` `credit_fills` | the model under calibration; **read, never change** |

**Drift corrections (issue and CodeRabbit text, resolved):**

1. The issue cites `shadow_fills.py:83` for `credit_fills`. The function is **defined at
   line 72**; line 83 sits inside its docstring. Path and symbol were right, the line was stale.
2. `shadow_taker_completions` exists (`core_brain/shadow_exec.py:175`) and marks completions
   by `(pair_id, token_id)`, not by order. It is **not needed**: a completion BUY is created
   through `create_order` (`shadow_exec.py:1519-1527`, OrderRecord at `:1519`, write at
   `:1527`) with **no `log_quote` call** — only `record_fill` at `:1536` — so the
   "BUY with a `quotes` row" cohort rule already excludes it, and the same rule reads
   identically on the live side where no marker table exists. Symmetric, no special case.
3. Live `queue_ahead` is empty in practice: in `data/orders.db` 0 of 14 quote rows carry a
   value, and 0 of 15 orders have one. The live side therefore always lands in `unknown`.
   That is a finding to print, not a defect to fix.
4. **Deliberate divergence from `kpi.py`:** `kpi.py:1726` reads a missing queue as
   `float(q.get("queue_ahead") or 0.0)`, i.e. NULL counts as queue 0. This report maps NULL
   to the `unknown` bucket instead — a live quote with no queue reading has no queue reading,
   and folding it into `0-1` would invent a fill rate at the best queue. The price buckets and
   the queue *boundaries* are still identical to `kpi.py`; only the NULL handling differs.

## Grounding against the real stores (read-only probes, plan time)

Run before writing a line of the tool, so the cohort rule is evidence-based rather than
plausible:

| Store | Cohort orders | Fills | Partial | Overfill |
| --- | --- | --- | --- | --- |
| `i608_tournament-1_the-accountant-1.db` | 793 | 246 | 0 | 0 |
| `…the-surgeon-1.db` | 779 | 241 | 0 | 0 |
| `…the-strict-parent-1.db` | 762 | 253 | 0 | 0 |
| `…the-gambler-1.db` | 760 | 262 | 0 | 0 |
| `…the-metronome-1.db` | 758 | 256 | 0 | 0 |
| `…the-chaperone-1.db` | 747 | 238 | 0 | 0 |
| `…the-daydreamer-1.db` | 732 | 230 | 0 | 0 |
| `…the-graceful-1.db` | 728 | 244 | 0 | 0 |
| `…the-regular-1.db` | 715 | 231 | 0 | 0 |
| `…the-patient-one-1.db` | 708 | 227 | 0 | 0 |

Four conclusions that changed the plan:

1. **The cohort rule is exactly right.** In `the-daydreamer`, 733 orders hold 732 quoted
   rests; the one order with **no `quotes` row** is `status='filled'`, has a `pair_id`, one
   fill of its full 5.0 shares, and `shadow_taker_completions` holds **exactly one** row.
   That lone order *is* the taker completion buy, and the quote-row rule removes it without
   ever reading the marker table. The marker join is unnecessary — confirmed empirically, not
   by reading code.
2. **The shadow fill model is binary.** 0 partials and 0 overfills in 7,382 cohort orders
   across ten stores. So the two rate measures coincide on the shadow side; they diverge only
   on live. Both stay in the report because the live side is where the distinction lives — and
   the fixture keeps a synthetic partial so the summing path has coverage.
3. **`stats_*.db` are not registries.** The three 130–148 MB `stats_21-10_*_shadow-01.db`
   files contain a single `snapshots` table and no `orders`/`quotes`/`fills`. Pointing
   `--shadow-db` at one must yield `UNREADABLE`, which is now a test case rather than a
   surprise. The usable shadow registries are the `i608_tournament-1_*.db` files.
4. **Queue data exists on the shadow side only.** All 793 quotes in `the-accountant` carry
   `queue_ahead`; 0 of 14 in `data/orders.db` do. Queue rows will be real for shadow and
   `insufficient` for live — a property of the data, printed rather than papered over.

## One improvement proposal (Step 5) — adopted by default

**The report must not repeat the issue's "pessimistic by construction" claim.**

Evidence, verbatim. Issue #474: *"shadow_fills.credit_fills (core_brain/shadow_fills.py:83)
fills a resting order only when real volume trades through it, and is pessimistic by
construction."* The model's own docstring, `core_brain/shadow_fills.py:88-92`: *"a print
strictly below our price is a trade-through. It proves our level was cleared … The lower
print is evidence, not shared volume -- it is never consumed, so one print can fill every
order resting above it."*

One print below the bid fills **every** order resting above it, so the model can be
optimistic as easily as pessimistic. Classification: **edge-case hardening / wording
correction, not scope expansion** — adopted into the plan. Effect: the gap is signed and
both directions are printed; the footer names the one-print-fills-all artefact instead of
asserting pessimism. No line of `shadow_fills.py` changes.

## CodeRabbit plan intake (read once, costed here)

- **Adopted:** the read-only loader with `NO_DATA`/`UNREADABLE`; the cohort rule; two rate
  measures plus the gap direction; the `kpi.py` queue boundaries and 0.10 price bands;
  `--min-orders` guarding the gap; caps + `overfilled`; dual `--json`/text output and the
  `1` exit code; most of its test list.
- **Rejected:** (a) its PR-note wording that asks the operator to *expect exit code 1 on
  failure* as a verify step — that is a fact to state, not an action to perform; (b) its
  `test_completion_buy_does_not_change_shadow_rate` as a second mechanism — the cohort rule
  already covers it, so it is one assertion inside the cohort test rather than extra code;
  (c) its prompt block's "seed both databases through the real `OrderRegistry` write
  methods" for anything other than a `tmp_path` fixture — `CONSTRAINTS.md` now forbids
  pointing `OrderRegistry` at an input path.
- **Stayed `[UNVERIFIED]` → resolved:** `shadow_fills.py:83` (→ 72), whether the completions
  marker is needed (→ not needed), and whether live queue data exists (→ it does not).
  Nothing remains unresolved.

## Interface contract (frozen; Station III builds to this)

```python
# core_brain/fill_calibration.py — stdlib only, imports nothing from core_brain
OK, NO_DATA, UNREADABLE = "OK", "NO_DATA", "UNREADABLE"
INSUFFICIENT = "insufficient"
SIGN_RULE = "positive gap = shadow fills more than live"
PRICE_BUCKET_WIDTH = 0.10
QUEUE_BUCKETS = ((0.0, 1.0), (1.0, 50.0), (50.0, 150.0), (150.0, 400.0), (400.0, math.inf))
QUEUE_UNKNOWN = "unknown"
DEFAULT_MIN_ORDERS = 10
FILL_EPSILON = 1e-9

@dataclass(frozen=True)
class OrderSample:
    local_id: str
    price: float
    posted_shares: float
    queue_ahead: float | None
    status: str
    filled_shares: float          # capped at posted_shares
    overfilled: bool
    @property
    def has_fill(self) -> bool: ...   # filled_shares > FILL_EPSILON

@dataclass(frozen=True)
class StoreLoad:
    label: str                    # "shadow" | "live"
    path: str
    run_id: str | None
    status: str                   # OK | NO_DATA | UNREADABLE
    detail: str                   # "" for OK, else the reason / sqlite error text
    samples: tuple[OrderSample, ...] = ()

@dataclass(frozen=True)
class BucketSummary:
    bucket: str
    orders: int
    posted_shares: float
    filled_shares: float
    filled_orders: int
    status_counts: dict[str, int]
    share_fill_rate: float | None      # None when posted_shares == 0
    order_fill_rate: float | None      # None when orders == 0

@dataclass(frozen=True)
class CalibrationRow:
    dimension: str                # "overall" | "price" | "queue"
    bucket: str
    shadow: BucketSummary
    live: BucketSummary
    share_gap: float | None       # shadow - live; None when not measurable
    order_gap: float | None
    status: str                   # OK | INSUFFICIENT

@dataclass(frozen=True)
class CalibrationReport:
    shadow: StoreLoad
    live: StoreLoad
    min_orders: int
    overall: CalibrationRow
    price_rows: tuple[CalibrationRow, ...]
    queue_rows: tuple[CalibrationRow, ...]
    sign_rule: str = SIGN_RULE
    def to_dict(self) -> dict: ...

def load_store(path: Path | str, *, run_id: str | None = None,
               label: str = "shadow") -> StoreLoad
def price_bucket(price: float) -> str                 # "0.40-0.50"
def queue_bucket(queue_ahead: float | None) -> str    # "0-1" | ... | "400+" | "unknown"
def summarize(samples: Sequence[OrderSample], bucket: str) -> BucketSummary
def bucket_summaries(samples, key) -> dict[str, BucketSummary]
def compare(shadow, live, *, min_orders: int
            ) -> tuple[CalibrationRow, list[CalibrationRow], list[CalibrationRow]]
def build_report(shadow: StoreLoad, live: StoreLoad, *, min_orders: int) -> CalibrationReport
def render_text(report: CalibrationReport) -> str
def main(argv: Sequence[str] | None = None) -> int
```

**Locked query** (one per store, no per-order queries):

```sql
SELECT o.id AS local_id, o.price, o.original_size, o.status,
       COALESCE(f.filled, 0.0) AS filled,
       q.queue_ahead
FROM orders o
JOIN (SELECT local_id, MIN(id) AS first_id FROM quotes
      WHERE local_id IS NOT NULL GROUP BY local_id) qf ON qf.local_id = o.id
JOIN quotes q ON q.id = qf.first_id
LEFT JOIN (SELECT order_uuid, SUM(size) AS filled FROM fills
           GROUP BY order_uuid) f ON f.order_uuid = o.id
WHERE o.side = 'BUY' AND o.original_size > 0 AND o.price > 0 AND o.price < 1
  /* AND o.run_id = ?  — only when a run id is given */
ORDER BY o.id
```

`MIN(id)` = first-logged quote row (lowest rowid); `quotes` declares
`id INTEGER PRIMARY KEY AUTOINCREMENT`. `quotes.filled` is never selected.

## Dependency graph

```
T1 (loader)  ──►  T2 (buckets + gap math)  ──►  T3 (render + CLI)
```

Riskiest first: T1 owns the trust boundary (the read-only claim and the cohort rule), so a
mistake there invalidates every number downstream.

## Tasks

### T1 — Read-only loader · size S · `[Backend/Logic]` · Depends on: —
**Files:** `core_brain/fill_calibration.py` (new), `tests/test_fill_calibration.py` (new)
**Build:** the constants, `OrderSample`, `StoreLoad`, `load_store` with the locked query,
`mode=ro` connect inside `try/finally`, `NO_DATA`/`UNREADABLE` handling, cap + `overfilled`.
**Verification (automated):** the T1 test group below, seeded through `OrderRegistry` on
`tmp_path`.

### T2 — Buckets and signed gap math · size S · `[Backend/Logic]` · Depends on: T1
**Files:** `core_brain/fill_calibration.py`
**Build:** `price_bucket`, `queue_bucket`, `summarize`, `bucket_summaries`, `compare`,
`build_report`. Pure functions over `OrderSample`; no database access, so every number is
testable without a file.
**Verification (automated):** the T2 test group below.

### T3 — Report rendering and CLI · size S · `[Backend/Logic]` · Depends on: T2
**Files:** `core_brain/fill_calibration.py`, `tests/test_fill_calibration.py`
**Build:** `render_text` (fixed-width, `--` for `None`, rates as one-decimal percentages
and gaps in `pp` so a signed gap reads as `+30.9pp` at a glance, footer with the sign rule and
the honesty points), `to_dict`, `main` with `--shadow-db`/`--live-db`
(required), `--shadow-run-id`/`--live-run-id`, `--min-orders` (default 10), `--json`;
exit `0` when both sides are `OK`, else `1`. `python -m core_brain.fill_calibration` runs it.
**Verification (automated):** the T3 test group, then a real run against a **copy** of
`01_shadow_21-10_11-39.db` (1,252 cohort orders — a real, long cohort) and a copy of
`data/orders.db`, plus the same against `i608_tournament-1_the-accountant-1.db` (793 orders):
the table prints with real shadow rates, price/queue gaps read `--` while the live side is
below `min_orders`, the overall gap prints, and the copies' SHA-256 is unchanged afterwards.

**Checkpoints:** after T1 (`load_store` returns `OK` with the expected 4/4 cohort on the
fixture); after T3 (a full text table and `--json` on the copies). In Mode A each checkpoint
is a one-line progress note, not a pause.

## Test list (fixture numbers chosen so every expected gap is exact)

**Fixture** — both stores seeded on `tmp_path` through `OrderRegistry`; every order is
`BUY` with `original_size = 10`.

| Shadow `shadow-A` | price | queue_ahead | fills | status |
| --- | --- | --- | --- | --- |
| S1 | 0.42 | 0 | 10 | filled |
| S2 | 0.42 | 20 | 2 + 3 | partial |
| S3 | 0.57 | 100 | 10 | filled |
| S4 | 0.57 | 200 | — | cancelled |
| C1 | 0.50 | no quote row | 10 | filled (taker completion) |

| Live `live-A` | price | queue_ahead | fills | status |
| --- | --- | --- | --- | --- |
| L1 | 0.42 | NULL | 10 (`run_id=live-B`) | filled |
| L2 | 0.42 | NULL | — | cancelled |
| L3, L4 | 0.57 | NULL | — | open |
| L5 (`run_id=live-other`) | 0.42 | NULL | — | open |

Resulting truths: shadow `0.40-0.50` → 2 orders, 2 filled, posted 20, filled 15 (order 1.0,
share 0.75); shadow `0.50-0.60` → 2 orders, 1 filled, posted 20, filled 10 (0.5 / 0.5);
shadow overall → 4 orders, 25/40 = 0.625 share, 3/4 = 0.75 order. Live `0.40-0.50` → 2
orders, filled 10/20 (0.5 / 0.5); live `0.50-0.60` → 2 orders, 0.0 / 0.0; live overall → 4
orders, 0.25 / 0.25.

**T1**
- `test_cohort_excludes_completion_buy` — shadow returns 4 records; `C1` absent.
- `test_partial_fills_are_summed` — S2 `filled_shares == 5`.
- `test_cancelled_unfilled_order_stays_in` — S4 present, status `cancelled`, filled 0.
- `test_duplicate_quote_counts_once` — live returns 4 records; L1 filled 10, not 20.
- `test_cross_run_fill_counts_for_owning_order` — with run `live-A`, L1 filled 10 though its
  fill carries `live-B`.
- `test_run_filter_excludes_other_runs` — filter `live-A` → 4 records; no filter → 5.
- `test_missing_queue_is_none_not_zero` — every live record has `queue_ahead is None`.
- `test_overfill_is_capped_and_flagged` — a 12-share fill on a 10-share order → 10, `True`.
- `test_inputs_are_not_modified` — SHA-256 of both files identical before and after.
- `test_missing_path_is_no_data` — status `NO_DATA`, and the path is still absent.
- `test_missing_quotes_table_is_unreadable` — a bare `orders`+`fills` file → `UNREADABLE`
  with non-empty detail text.
- `test_foreign_schema_is_unreadable` — a file whose only table is `snapshots` (the shape the
  real `stats_21-10_*_shadow-01.db` files have) → `UNREADABLE`, not an empty `OK` cohort.

**T2**
- `test_price_bucket_edges` — 0.40 → `0.40-0.50`; 0.3999 → `0.30-0.40`; 1.00 → `0.90-1.00`.
- `test_queue_bucket_edges` — 0 → `0-1`; 1 → `1-50`; 400 → `400+`; `None` → `unknown`.
- `test_overall_gap` — order gap +0.50, share gap +0.375.
- `test_gap_sign_is_shadow_minus_live` — swapping the sides flips both gaps' sign.
- `test_price_bucket_gaps` — `0.40-0.50` order +0.50 / share +0.25; `0.50-0.60` +0.50 / +0.50.
- `test_live_queue_rows_are_unknown` — live `0-1` has 0 orders, live `unknown` has 4; every
  queue row's gaps are `None`.
- `test_min_orders_blocks_thin_buckets` — `min_orders=3` → `insufficient`, both gaps `None`,
  counts still printed.
- `test_zero_posted_shares_gives_none` — an empty sample set → both rates `None`, no
  `ZeroDivisionError`.
- `test_bucket_gap_ok_status` — sides above `min_orders` → `status == OK`.

**T3**
- `test_main_json` — `--min-orders 1 --json` → returns 0; parsed `overall.order_gap == 0.5`;
  `sign_rule` present.
- `test_main_text` — output contains `gap`, `positive gap = shadow fills more than live`,
  `unknown`, and `--`.
- `test_main_missing_live_db` — returns 1, output names `NO_DATA`, and the path is not
  created.

Assert **exact equality**, never `pytest.approx`: every expected value is an exact binary
fraction (0.625 = 5/8, 0.375 = 3/8, 0.75, 0.5), so a tolerance could only mask a wrong
denominator.

## How to verify (operator, hands-on — no test output)

1. From the **main checkout** (the worktree has no `data/`), copy both stores somewhere safe
   so the tool never opens the originals:
   `Copy-Item 'data\i608_tournament-1_the-accountant-1.db' "$env:TEMP\sh.db"; Copy-Item 'data\orders.db' "$env:TEMP\live.db"`
2. Run `python -m core_brain.fill_calibration --shadow-db "$env:TEMP\sh.db" --live-db "$env:TEMP\live.db"`.
3. Expect a table whose **gap** column carries a `+`/`−` sign, and a footer stating
   *positive gap = shadow fills more than live*. The shadow side shows a real rate (roughly a
   third of orders fill); the live column reads `0.0%` because the live registry holds no
   fills, so the overall gap is one-sided (`+30.9pp` on the accountant store) and every
   per-price and per-queue gap reads `--` — the honest answer today, not a failure.
4. Run it again with `--json` and check the same `order_gap` / `share_gap` values appear.
5. Point `--shadow-db` at a `stats_21-10_*_shadow-01.db` file: expect `UNREADABLE` naming the
   missing table, not a silent empty report. Point `--live-db` at a missing path: expect
   `NO_DATA`, exit code `1`, no file created.

## Follow-up (deferred, not part of this PR)

The **real calibration run** — the numbers that actually matter — waits on #471 producing at
least 60 closes of live data. Until then this PR ships the instrument, not the reading. Once
#471 closes, the run is one command against the real stores and the measured gap can be
recorded on #470. The split is documented on the issue itself.

## NOTICED-BUT-NOT-TOUCHING

- `data/orders.db` holds 0 fills, so the live side is a denominator with no numerator.
  Populating it is #471's job.
- `shadow_fills.credit_fills` keeps `queue_ahead` as "descriptive telemetry but gates
  nothing" (`shadow_fills.py:101-102`), yet this report buckets by it. The buckets describe
  the cohort; they do not explain the fill. The footer says so.
- `kpi.py` computes its own queue-bucket fill rate from `quotes.filled`
  (`kpi.py:1726-1735`), which double-counts on duplicate quote rows. Out of scope here; a
  future issue could align the two.
- `CONSTRAINTS.md`, `SPEC.md` and `tasks/` are a **single shared slot** at the repo root,
  not per-issue paths. At plan time the slot held #748's artifacts
  (`git show HEAD:CONSTRAINTS.md` → "Constraints — #748 One category label…"). Overwriting is
  the documented convention (`docs/agents/architecture.md:145` describes them as archived per
  issue; commit `d6e7c75` is a past sweep), so this plan takes the slot and #748's content
  stays readable in git history at `1074ea2`. Flagged, not fixed: two worktrees planning at
  once will collide here.

