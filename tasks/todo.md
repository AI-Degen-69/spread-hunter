# Issue #254 — Execution checklist

- [ ] Task 1 — Measure: full suite with `--durations=20`; record baseline wall time + slowest files
- [ ] Task 2 — Split `.github/workflows/tests.yml` into `pytest-fast` + `pytest-slow` parallel jobs; collection check sums to 2193
- [ ] Task 3 — Push, open PR, confirm `gh pr checks` green on both OSes and Windows under ~4 min
- [ ] Review — Run targeted checks after all review fixes
- [ ] CI — Confirm full regression suite through GitHub CI before merge
