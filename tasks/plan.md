# Plan — #531: Persist the clearing print at fill-credit time

## Why (issue summary)

`trade_through_depth` reads `missing` on every real shadow run because the print
that cleared a resting order's level is dropped at credit time: `credit_fills`
proves the trade-through (`any(price < own ...)`) but credits only our own price,
`_credit_market` writes only that price, and `cleared_fills` builds every
`ClearedFill` with `clearing_price=None`. This ticket records the closest
strictly-below print per credited fill — a telemetry write; which orders fill
must not change.

## Tier: Small (one recorded decision chain, 4 tasks, ~30 min build)

Single writer chain (select → carry → store → read); no UI, no strategy change.

## Locked interfaces (frozen for this branch)

- `credit_fills(orders, traded) -> (fills, queues)` — return type unchanged.
  `ShadowFill` gains `clearing_price: Optional[float] = None, compare=False`.
- `FillRecord` gains optional `clearing_price`; `record_fill` INSERT unchanged.
- `get_all_fills()` enriches with the print via optional join + existence check.
- `cleared_fills(quotes, fills)` — print read from the earliest-timestamp row.
- Storage: shadow-only companion table in `ensure_shadow_tables`, keyed by
  `trade_id`. `SCHEMA` / `_apply_migrations()` untouched.

## Dependency graph

T1 (select+carry, pure) → T2 (store+enrich) → T3 (read in cleared_fills) → T4
(end-to-end regression + acceptance)

## Tasks

### T1 [S] `shadow_fills` — select the closest below-print, carry it on the fill
- Files: `core_brain/shadow_fills.py`, `tests/test_shadow_fills.py`
- Build: inside the crediting branch, `below = max(price for (t,price) in swept
  if t == token and price < own)`; `ShadowFill(..., clearing_price=below)`.
  `ShadowFill` gains the field with default `None`, `compare=False`.
- Verify: closest-not-deepest (prints 0.46+0.44 below 0.47 → 0.46); at-or-above
  prints credit nothing (existing tests stay green unchanged); rounding
  0.46999 vs 0.47 still no fill. RED first: new tests fail on unmodified code.
- Depends on: nothing.

### T2 [S] `shadow_exec` + `order_registry` — store the print, enrich the read
- Files: `core_brain/shadow_exec.py`, `core_brain/order_registry.py`
- Build: companion table DDL in `ensure_shadow_tables`
  (`trade_id TEXT PRIMARY KEY, clearing_price REAL`); one write per credited
  fill in `_credit_market` (uses the `trade_id` it already generates);
  `FillRecord.clearing_price` optional; `get_all_fills()` LEFT JOIN guarded by
  `PRAGMA table_info` existence check (old stores without the table read as
  today — print absent → `None`).
- Verify: write+read round-trip on `tmp_path` registry; store without the
  table still reads (no crash, print `None`).
- Depends on: T1.

### T3 [S] `statistical_analytics` — read the print in `cleared_fills`
- Files: `core_brain/statistical_analytics.py`,
  `tests/test_queue_realism_analytics.py`
- Build: track the row supplying the earliest usable timestamp per quote;
  `clearing_price` comes from that same row. Update the two tests pinning
  `None` to the present-print expectation; add missing-print-stays-`None`
  and tie/fallback row-selection cases.
- Verify: depth `count > 0` + `median_cents` on present-print rows; latency
  figures byte-identical to before (same rows drive both).
- Depends on: T2.

### T4 [S] End-to-end regression + acceptance
- Files: `tests/test_queue_realism_analytics.py`
- Build: registry-level test — credit fills with below-prints through
  `_credit_market`, build `cleared_fills` from `get_all_*`, assert depth
  measures; then the same run with the write removed asserts depth `missing`.
  Covers acceptance bullets 1–3 and 5.
- Verify: the issue's acceptance command passes
  (`test_queue_realism_analytics` + `test_shadow_fills`); boundary
  `test_shadow_run` + `test_statistical_analytics` green.
- Depends on: T3.

## Checkpoints

- After T1: pure selection proven, fill decisions untouched.
- After T3: depth measures on hand-built rows.
- After T4: full acceptance green.

## Proposal (adopted — simplification, evidence-based)

> Evidence (issue Scope): "either a new column on `fills` or a small table
> beside `queue_marks`".

Adopt the companion table over the `fills` column: a `fills` column rides the
shared migration and touches production stores; the table beside `queue_marks`
is the repo's own shadow-only pattern (`shadow_queue`, `shadow_merge_legs`)
and keeps `data/orders.db` read-only. Recorded in CONSTRAINTS.md, not optional.

## Assumptions & risks

- `_credit_market` generates one `trade_id` per fill already — the companion
  write reuses it (verified `shadow_exec.py:678-682`).
- `get_all_fills()` joins `fills`→`orders`; the enrichment is one more LEFT
  JOIN on `trade_id` — no shape change for callers.
- Risk: old shadow stores lack the table → guarded by existence check;
  those rows read `None` exactly as today.
- `data/orders.db` is never opened for writing; tests use `tmp_path`.

## Out of scope (binding)

Fill model, taker path, bands/thresholds/card, latency half, LIVE, quoting,
sizing, #520 tuples, #504 nav.
