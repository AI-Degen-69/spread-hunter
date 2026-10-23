# CONSTRAINTS.md — #767 registry targeted queries

1. **Zero regressions + RED-first.** Every new query lands with an equivalence
   test that fails on `origin/main` before the fix (tiebreak pin, batched
   totals vs per-order helpers, fill/close narrow-vs-full comparisons).
   Targeted suites (`test_order_registry`, `test_risk_guardian`,
   `test_single_buy_saver`, plus `test_stray_guard`,
   `test_single_leg_lifecycle`, `test_lifecycle_single_executor` for other
   callers/locking) stay green. Full `pytest -q` is the CI merge gate, run
   silently by the agent — never presented as Owner verification.
2. **No silent fill/close drops.** `LEFT JOIN` in pair totals (zero-fill orders
   survive); inner join + no status/side filter in fill discovery; every
   Python conversion verbatim (`float(ts or 0.0)`, `int(float(ts)*1000.0)`
   skipping only `TypeError`/`ValueError`, `int(venue_ts)` raising on
   malformed, `up_price is not None` → UP). A wrong join or filter that hides
   a fill is the top risk — equivalence tests cover cancelled orders,
   zero-fills, replays, undated fills, every close method.
3. **Fresh reads stay fresh.** No caching across cancel, exit, or completion:
   every `load_pair`, `_token_side`, `_prior_exit_shares` call is a new query.
   Same-pass second exit must see the first exit's close (covered by test).
4. **Lock discipline.** The lifecycle pass runs under `_LIFECYCLE_LOCK`.
   One batched query per pair replaces `1+2N` connections; discovery reads run
   once per pass under the same lock. Extra round-trips inside the lock are a
   regression even when individually faster.
5. **No schema/DDL/index change.** No `CREATE INDEX`, no table/column change,
   no migration edit (the `idx_closes_cid_tx` best-effort migration stays as
   is). If a query genuinely needs an index to be fast, surface it as a
   decision with cost — never sneak DDL into a code commit. Existing indexes
   relied upon: `idx_orders_pair_id`, `idx_fills_order_uuid`, `idx_quotes_ts`,
   `idx_closes_ts`.
6. **Production registry is untouchable.** Never read/write/delete
   `data/orders.db` or any shadow DB in place. Any perf measurement copies to
   scratch first (`cp <store> /tmp/sh767_<stamp>.db`) or uses a fresh
   `data/NN_shadow_<stamp>.db` from `shadow_run`. `tests/conftest.py`
   rejects production-registry connections.
7. **Runtime compatibility.** `ROW_NUMBER()` needs SQLite ≥ 3.25.0 (dev
   interpreter here: 3.49.1). Builder checks the deployed interpreter with
   `python -c "import sqlite3; print(sqlite3.sqlite_version)"` before T1;
   below 3.25.0 uses the ordered-projection + Python-pick fallback. No
   behaviour drift either way — same tiebreak, same tests.
8. **Anti-cheat.** No skipped/disabled tests, no deleted assertions, no
   swallowed exceptions, no lint/type suppressions to get green. Guardian
   mocks must configure the new method AND make `get_all_quotes` raise —
   an unspecced mock passing on nothing fails review.
9. **No scope expansion.** `get_all_*` keep their signatures for kpi,
   settlement_sweeper, shadow_exec, statistics_report, trader_loop callers.
   No `order_manager`/`stray_guard` edits, no output/CLI change, no second
   quote reader, no `fill_calibration.py` change, no behaviour change beyond
   read narrowing. Dependencies: none new.
