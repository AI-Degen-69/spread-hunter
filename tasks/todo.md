# Todo — #515 queue realism analytics (trade-through depth, fill latency)

Branch: `i515/add-queue-realism-analytics` | Issue: #515
Plan: `tasks/plan.md` · Guardrails: `CONSTRAINTS.md`

- [ ] T1: `core_brain/statistical_analytics.py` — `ClearedFill`,
      `TRADE_THROUGH_GOOD_BELOW_CENTS`, `TRADE_THROUGH_AVERAGE_AT_OR_BELOW_CENTS`,
      `_percentile`, `trade_through_depth_band()`, `trade_through_depth()`; new
      `tests/test_queue_realism_analytics.py` with the cleared / missing / not-trade-through /
      empty / single / determinism / no-mutation cases
- [ ] T2: same files — `fill_latency()`; wait, missing-timestamp, clock-skew, equal-timestamp,
      independence, partition, determinism, no-mutation and strict-JSON cases
- [ ] CHKPT 1: both metrics measured; focused suite green; nothing outside the two files touched
- [ ] T3: `docs/issues/515-noticed-but-not-touching.md` (missing clearing-print data path,
      recognition-time caveat, no-clamp divergence, ms→s follow-up) and the focused runs over
      `test_statistical_analytics.py`, `test_fill_path_analytics.py`,
      `test_rescue_friction_analytics.py`, `test_shadow_fills.py`
- [ ] CHKPT 2: focused suites green; operator verify block posted (direct-call steps, no pytest)
- [ ] Handoff to `/iv-review-build-and-pr`
