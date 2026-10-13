# Todo — #515 queue realism analytics (trade-through depth, fill latency)

Branch: `i515/add-queue-realism-analytics` | Issue: #515
Plan: `tasks/plan.md` · Guardrails: `CONSTRAINTS.md`

- [x] T1: `core_brain/statistical_analytics.py` — `ClearedFill`,
      `TRADE_THROUGH_GOOD_BELOW_CENTS`, `TRADE_THROUGH_AVERAGE_AT_OR_BELOW_CENTS`,
      `_percentile`, `trade_through_depth_band()`, `trade_through_depth()`; new
      `tests/test_queue_realism_analytics.py` (RED: `ImportError: cannot import name
      'ClearedFill'`; GREEN: 20 passed). Deviation: the plan's `p75` expectation was
      corrected `4.75 -> 4.25` in the test before implementing.
- [x] T2: same files — `fill_latency()` (RED: `ImportError: cannot import name
      'fill_latency'`, pytest exit 2; GREEN: 32 passed with the T1 cases)
- [x] CHKPT 1: both metrics measured; `git status --short` limited to the module and the new
      test file; no other file touched
- [x] T3: `docs/issues/515-noticed-but-not-touching.md` (N1 missing clearing-print data path,
      N2 recognition-time caveat, N3 no-clamp divergence, N4 ms→s conversion for the follow-up)
- [x] REPAIR: `test_empty_run_is_none_not_zero_and_never_divides_by_zero` was defined
twice (T1 depth + T2 latency), so the later definition shadowed the earlier and the depth
empty-run case never ran. Renamed both; the file now collects 33 cases, and the focused set
was re-run after the repair.
- [x] CHKPT 2: focused suites green — 180 passed across
      `tests/test_queue_realism_analytics.py`, `tests/test_statistical_analytics.py`,
      `tests/test_fill_path_analytics.py`, `tests/test_rescue_friction_analytics.py`,
      `tests/test_shadow_fills.py`; the operator block's exact command run and its printed
      values matched the plan (`median_cents 2.5`, `p75_cents 2.75`, `band average`,
      `median_sec 10.0`, `p90_sec 14.0`; `bad` at 15c, `good` at 0.01c)
- [ ] Handoff to `/iv-review-build-and-pr`
