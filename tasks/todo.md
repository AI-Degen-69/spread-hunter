Branch: i474/fill-model-calibration-cli | Issue: #474

# Todo — fill-model calibration

## T1 — Read-only loader
- [ ] Constants: `OK`/`NO_DATA`/`UNREADABLE`/`INSUFFICIENT`, `SIGN_RULE`, `PRICE_BUCKET_WIDTH`,
      `QUEUE_BUCKETS`, `QUEUE_UNKNOWN`, `DEFAULT_MIN_ORDERS`, `FILL_EPSILON`
- [ ] `OrderSample` (frozen) with `has_fill`
- [ ] `StoreLoad` (frozen)
- [ ] `load_store`: `mode=ro` URI + `uri=True`, `try/finally`, `NO_DATA` for a missing path,
      `UNREADABLE` for a SQLite error (with detail text)
- [ ] Locked cohort query — one grouped query, first quote row per `local_id`, `fills.size` sum
- [ ] Cap at `original_size`; set `overfilled`; `queue_ahead` NULL → `None`
- [ ] T1 tests (12)
- [ ] Checkpoint: fixture yields 4 shadow / 4 live records, `OK`

## T2 — Buckets and gap math
- [ ] `price_bucket` (0.10 bands, `[0.90,1.00]` closes at 1.00)
- [ ] `queue_bucket` (`kpi.py` labels + `unknown`)
- [ ] `summarize` — counts, shares, both rates, `None` on zero denominator, no `ZeroDivision`
- [ ] `bucket_summaries`
- [ ] `compare` — union of buckets, `shadow − live`, `insufficient` below `min_orders`
- [ ] `build_report`
- [ ] T2 tests (9)

## T3 — Rendering and CLI
- [ ] `render_text` — fixed-width, `--` for `None`, 3 decimals, header, footer (sign rule +
      one-print-fills-all + live-queue-absent + `min_orders`)
- [ ] `CalibrationReport.to_dict`
- [ ] `main` — argparse (`--shadow-db`, `--live-db`, run ids, `--min-orders`, `--json`),
      exit `0`/`1`
- [ ] `if __name__ == "__main__": raise SystemExit(main())`
- [ ] T3 tests (3)
- [ ] Live run on copies of `data/i608_tournament-1_the-accountant-1.db` + `data/orders.db`
      (main checkout — the worktree has no `data/`)
- [ ] Checkpoint: table prints, gaps `--`, `data/` untouched

## Wrap-up
- [ ] Focused regressions green (`shadow_fills`, `order_registry`, `quote_fill_attribution`,
      `run_attribution`, `run_scorer`, `stat_validation_artifacts`)
- [ ] `git diff --stat` confined to the two new files (+ planning docs)
- [ ] Commit, push, open PR linking #474, note the #471 deferral
