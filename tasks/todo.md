# Todo — #754 Cockpit: stop hiding arm-record parse failures, drop dead badge metadata

Branch: `i754/cockpit-record-parse-warning-and-drop-dead-badge` | Issue: #754

- [x] **T1** — `TournamentRecordError` + `_read_arm_record` shape check; `resolve_tournament_arms_with_status` returns `(arms, record_error)`; `resolve_tournament_arms` stays a `list[dict]` wrapper
- [x] **T1** — tests: broken record reports path + reason, five shape errors name the arm at fault, missing is not an error, valid reports `None`; five existing resolver tests green
- [x] **T2** — `collect_all_stats` carries `record_error`; page always emits `#record-warning` (hidden by default); `renderAll()` syncs it from `/api/stats`
- [x] **T2** — tests: warning visible with the path + reason for a broken record, hidden for a missing one
- [x] **T3** — delete `badge` / `badge_cls` from all four `TOURNAMENTS` entries
- [x] **T3** — tests: no entry carries either key; nav renders exactly one `CURRENT SETUP` badge
- [x] **T4** — targeted suite green: `test_tournament_cockpit_arms.py`, `test_tournament_cockpit_funnel.py`, `test_tournament_dashboard_hub.py` (30 passed)
- [x] Manual smoke — cockpit on :8801 (worktree copy): no record → warning hidden; corrupt record → warning shows path + parser reason on screen and in `/api/stats`; record removed → hidden again
