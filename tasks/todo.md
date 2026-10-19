# Todo Tasks: Issue #698 (absorbing #693)

- [ ] Task 1: Update launcher interval default to `MAX_ROTATION_INTERVAL_SEC` and add interval tests
  - [ ] 1.1 Import `MAX_ROTATION_INTERVAL_SEC` in `scripts/shadow_tournament.py` and update function & CLI defaults
  - [ ] 1.2 Add tests in `tests/test_shadow_tournament.py` for default interval and warning-free parsing
- [ ] Task 2: Blueprint Modernization & Docstring Sweep
  - [ ] 2.1 Update blueprint table, commands, and `.env` block in `scripts/tournament_cockpit_server.py`
  - [ ] 2.2 Reword docstring in `scripts/grace_sweep_report.py:3`
  - [ ] 2.3 Reword comment in `core_brain/statistical_analytics.py:119-124`
  - [ ] 2.4 Add test for cockpit HTML blueprint tab assertions in `tests/test_shadow_tournament.py`
- [ ] Task 3: Regression Verification & Operator Steps
  - [ ] 3.1 Run targeted test suites (`test_shadow_tournament.py`, `test_rotation_interval_cap.py`, `test_tournament_cockpit_funnel.py`)
  - [ ] 3.2 Formulate zero-risk hands-on operator verification guide
