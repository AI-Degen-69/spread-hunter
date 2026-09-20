# Issue #254 — Execution checklist

- [x] Task 1 — Measure: per-file durations; baseline ~156s locally, flat profile, 2193 tests
- [x] Task 2 — Shard `.github/workflows/tests.yml` (ubuntu full + 4 windows shards via `scripts/ci/pytest_shard.py`); collection check sums to 2193
- [x] Task 3 — Push, open PR, confirm `gh pr checks` green on both OSes and Windows under ~4 min
- [ ] Review — Run targeted checks after all review fixes
- [ ] CI — Confirm full regression suite through GitHub CI before merge
