Branch: i646/unified-reset-path | Issue: #646

# Constraints — #646 unified reset path with filter re-qualification

One guarded reset that live merges, shadow merges, and stop exits all go
through: prove flat from real reads, clear the lifecycle row, mark the market
awaiting re-qualification. No new entry quotes until a newer published
universe includes the market.

## The bar

- Reset proves flat from **real reads only**: net ledger inventory
  (`inventory_from_registry`, fills minus closes) **and** both token balances
  at or below `SIZE_EPS`. A `venue_positions is None` read never counts as
  zero — it yields `UNAVAILABLE`/`REFUSED` and the market stays managed.
- `PAIR_LOCKED`, `route_to_merge`, and `exited` alone are **never** proof of
  flat. Only the reads count.
- `finish` is atomic: delete of all `single_leg_lifecycle` rows for the
  condition + `awaiting` mark land in **one `BEGIN IMMEDIATE` transaction**,
  conditional on the generation. Stale generations clear nothing.
- Begin (`resetting`) blocks fresh entries **before** cancellation starts; any
  non-`RESET` outcome abandons the block and leaves lifecycle rows untouched.
- Live merge resets only after `EXECUTED` **and** a successful `log_close`
  write. Shadow merge resets only after close + leg writes commit. Stop exit
  resets only after `_record_exit_close` on `action="exited"`.
- Partial/unequal merges, partial stops, and late fills after cancel
  **block** the reset; the market stays under the existing manager.
- Entry gate fails closed: a `sqlite3.Error` from `requalification_marks()`
  blocks fresh entries for the cycle. Hedges, exits, and restores keep
  working. Legacy no-metadata `markets_fn` suppliers never clear a mark.
- Only a publication **strictly newer** than `reset_ts_ms` that includes the
  market clears its mark; a newer publication that excludes it drops the
  market from trading but keeps the mark.
- The `#634` lifecycle assertions in `tests/test_single_leg_lifecycle.py`
  and `tests/test_single_buy_saver.py` stay green, or change only
  deliberately with the reason recorded in the plan.
- Every new behavior ships with a test that **fails without the change**.

## Deliberate non-changes (a later reviewer must not "fix" these back)

- Orders, fills, and closes history are never rewritten by a reset.
- `scripts/filter_markets.py` and `_publish_json` are untouched; the filter
  loop stays the only publication entry point, file stays a bare JSON list.
- No tiered poller, no second-leg repricing, no slippage measurement
  (out-of-scope rows go to `docs/issues/646-noticed-but-not-touching.md`).
- No tier list for #621 is built here; `requalification_marks()` (incl.
  exclusion records) is the documented contract #621 will read.

## Process

- No live/opening commands (`quote`, `complete`, Trader loop, START).
- Never touch `data/orders.db`; shadow work uses a fresh isolated store.
- Targeted suites locally only; GitHub CI (Ubuntu + Windows) is the merge gate.
- No `pytest` / `gh` / `git` step inside the operator's "How to verify".
- Narrow `except` clauses only (`PairExitRefused`, `ExposureUnavailable`,
  `sqlite3.Error`); unit-suffixed time names (`now_ms`, `reset_ts_ms`).
