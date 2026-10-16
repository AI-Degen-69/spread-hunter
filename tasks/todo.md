# Todo - #624 [DEFECT] Strip un-set parent HUNTER_* env vars when spawning shadow tournament workers

Branch: i624/strip-parent-hunter-env-shadow-workers | Issue: #624

- [ ] T1 - Add regression test `test_worker_environment_strips_parent_hunter_env` in `tests/test_shadow_tournament.py` (TDD RED)
- [ ] T2 - Strip parent `HUNTER_*` env vars in `scripts/shadow_tournament.py:516` (TDD GREEN)
- [ ] T3 - Mark N1 as resolved in `docs/issues/608-noticed-but-not-touching.md` and record out-of-scope in `docs/issues/624-noticed-but-not-touching.md`
