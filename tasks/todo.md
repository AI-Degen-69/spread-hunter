# Todo — Issue #787 (branch `i787/decide-whether-to-raise-the-ev-edge-floor`)

## T1 — analysis script + tests [Backend/Logic]
- [x] `scripts/edge_floor_analysis.py` on top of `load_store` / `build_report` /
      `edge_bucket_for` / `score_ab` (no second loader, no second bucketing)
- [x] bookkeeping closes excluded via a filtered `StoreLoad`
- [x] position table (bucket by lowest leg edge, `negative` / `unknown` handled)
- [x] crossover + floor sweep (0.02 / 0.03 / 0.04 / 0.06) with exact label lists
- [x] confound splits: median shares, median implied pair cost, category
- [x] data-quality counts + rehearsal banner + JSON/markdown output
- [x] `tests/test_edge_floor_analysis.py` (reuses the existing fixture kit)
- [x] focused file green; demo-store run prints a table

## T2 — pin the single-buy consequence [Backend/Logic]
- [x] `tests/test_fill_quality_gate.py`: UP leg blocked → DOWN still posts, resting UP
      cancelled `not_quoted`
- [x] focused file green

## T3 — numbers + decision record [Docs]
- [x] run the script on `shadow-01` and `shadow-01-prudent` (read-only, in place)
- [x] `score_ab` per candidate floor per tape
- [x] one 5-minute shadow run at the proposed settings; record exit code / errors /
      heartbeat / open inventory
- [x] apply the locked decision rule
- [x] `docs/audits/edge-floor-decision.md` with tables, limits, proposal, operator live
      step, undo, $0 added risk, follow-ups, hands-on "How to verify"
