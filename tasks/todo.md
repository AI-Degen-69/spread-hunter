# #767 todo — Station II (plan) → handoff to III

- [x] Map hot paths on `origin/main`: guardian 23-43/185-215, saver 635-747,
      816-842, 1811-1901, 2092-2180, registry 924-961/1379-1386/1524-1541/
      1994-2069, lock `_LIFECYCLE_LOCK` ~108-136
- [x] Confirm absent helpers: no `get_latest_quote`/`get_quotes_for`/
      `get_token_side`/`get_pair_fill_totals` (add-then-rewire)
- [x] Pin tiebreak: `(COALESCE(ts,0.0) DESC, rowid DESC)` + `-1.0` floor for Q2;
      RED test with A/B conflict + timestamp ties
- [x] Freeze 6 query contracts (Q1–Q6 SQL shapes, batched `load_pair` 1-conn
      vs 1+2N, chunked ≤500 close reads, no DDL/index)
- [x] List `get_all_*` survivors: kpi, settlement_sweeper, shadow_exec,
      statistics_report, trader_loop, audit/stray_guard per-order callers
- [x] Record rebase point: #764 hunks (+PRICE_EPS, +ask_depth_at_or_below,
      complete_pair line) shift saver lines; builder re-derives via grep
- [x] Write `tasks/plan.md` (header `Branch: i767/registry-targeted-queries |
      Issue: #767`), `tasks/todo.md`, `CONSTRAINTS.md`
- [ ] Station III (builder, after #764 merges): T1 quote queries → T2 guardian
      → T3 lifecycle quotes → T4 pair totals + fill discovery → T5 bounded
      closes; each RED-first, independently revertible, targeted pytest green
- [ ] Station III perf note: tick-timing on a **copy** in scratch
      (`cp` to `/tmp`/fresh shadow DB), never `data/orders.db`
- [ ] Station IV: single focused CodeRabbit round, one push, targeted checks
- [ ] Station VI: closeout with hands-on verify (menu/stack + dashboard
      `http://127.0.0.1:8799` + shadow rehearsal), never pytest counts as proof
