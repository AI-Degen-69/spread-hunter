# Todo — Issue #856: Active strategy profile badge

Branch: `i856/feat-display-active-strategy-profile-name-in-dashb` | Plan: `tasks/plan.md` | Spec: `SPEC.md`

## T1 — Shadow profile → status field [Backend/Logic] (M)

- [ ] `describe_active_profile(cfg, preset_name)` in `core_brain/config.py` (no `MakerConfig` change)
- [ ] Startup computation + `profile` heartbeat field in `core_brain/shadow_run.py` (fail-soft, best-effort write)
- [ ] Shadow reader + `active_profile` in `get_system_status()` (`dashboard/server.py`, shadow source)
- [ ] `tests/test_active_profile.py` (new): 6 name-grammar cases
- [ ] `tests/test_shadow_run_stopwatch.py`: heartbeat profile cases
- [ ] `tests/test_dashboard_server.py`: 7 server cases; existing status + db-identity asserts untouched
- [ ] Checkpoint 1: `active_profile` observable in status JSON

## T2 — PROFILE pill in top nav [Design/UI] (S)

- [ ] `#profile-badge` markup in `dashboard/static/index.html` (after `#db-mode-badge`, no 4th group)
- [ ] `renderProfileBadge(status)` in `dashboard/static/app.js` (esc, full className replace, tooltip)
- [ ] HTML structure tests (`test_ui_top_nav_consistency.py`, `test_ui_top_nav_groups.py`)
- [ ] `profile` harness scenario + `@requires_node` wrapper; narrow-viewport test green
- [ ] Checkpoint 2: badge visible, refreshes per poll

## T3 — Live writer + fallback [Backend/Logic] (M)

- [ ] `trader_profile.json` constant (`runtime_paths.py`, no legacy entry)
- [ ] `write_trader_profile(cfg, db_path)` + call in `trader_loop.main()` (nonfatal write)
- [ ] Live reader + fallback in `get_system_status()` (trust checks, shadow priority)
- [ ] Writer + server-matrix tests; runtime files under `tmp_path` only
- [ ] Checkpoint 3: full targeted matrix green

## Close-out

- [ ] No frozen file touched (launcher, cockpit, arms JSON, risk, env_file, MakerConfig, styles.css, .gitignore)
- [ ] Targeted suites green; full suite left to CI
- [ ] No opening command run, no store rewritten
