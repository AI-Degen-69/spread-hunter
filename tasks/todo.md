Branch: i474/fill-model-calibration-cli | Issue: #474

# Todo — fill-model calibration

## T1 — Read-only loader
- [x] Constants: `OK`/`NO_DATA`/`UNREADABLE`/`INSUFFICIENT`, `SIGN_RULE`, `PRICE_BUCKET_WIDTH`,
      `QUEUE_BUCKETS`, `QUEUE_UNKNOWN`, `DEFAULT_MIN_ORDERS`, `FILL_EPSILON`
- [x] `OrderSample` (frozen) with `has_fill`
- [x] `StoreLoad` (frozen)
- [x] `load_store`: `mode=ro` URI + `uri=True`, `try/finally`, `NO_DATA` for a missing path,
      `UNREADABLE` for a SQLite error (with detail text)
- [x] Locked cohort query — one grouped query, first quote row per `local_id`, `fills.size` sum
- [x] Cap at `original_size`; set `overfilled`; `queue_ahead` NULL → `None`
- [x] T1 tests (12)
- [x] Checkpoint: fixture yields 4 shadow / 4 live records, `OK`

## T2 — Buckets and gap math
- [x] `price_bucket` (0.10 bands, `[0.90,1.00]` closes at 1.00; `round(..., 9)` before the floor
      so a 0.30 bid cannot land in 0.20-0.30)
- [x] `queue_bucket` (`kpi.py` labels + `unknown`)
- [x] `summarize` — counts, shares, both rates, `None` on zero denominator, no `ZeroDivision`
- [x] `bucket_summaries`
- [x] `compare` — union of buckets, `shadow − live`, `insufficient` below `min_orders`
- [x] `build_report`
- [x] T2 tests (9)

## T3 — Rendering and CLI
- [x] `render_text` — fixed-width, `--` for `None`, percentages with one decimal / gaps in `pp`,
      per-bucket blocks, footer (sign rule + one-print-fills-all + `min_orders`)
- [x] `CalibrationReport.to_dict`
- [x] `main` — argparse (`--shadow-db`, `--live-db`, run ids, `--min-orders`, `--json`),
      exit `0` only when both sides are `OK`, else `1`
- [x] `if __name__ == "__main__": raise SystemExit(main())`
- [x] T3 tests (3)
- [x] Live run on copies of `data/01_shadow_21-10_11-39.db` (1,252 cohort orders) and
      `data/i608_tournament-1_the-accountant-1.db` (793) against a copy of `data/orders.db`
      (14 cohort orders, 0 fills) — main checkout copied into `$env:TEMP`, never opened in place
- [x] Checkpoint: table prints (`shadow 31.8% / live 0.0%`, overall `+31.8pp`), per-bucket gaps
      `--` under `insufficient`, `--json` parses, copy SHA-256 identical before and after

## Wrap-up
- [x] Focused regressions green (`shadow_fills`, `order_registry` — 79 passed; full suite is CI)
- [x] `ruff check` clean on both new files
- [x] `git diff --stat` confined to the two new files (+ planning docs)
- [x] Commit, push, open PR linking #474, note the #471 deferral
