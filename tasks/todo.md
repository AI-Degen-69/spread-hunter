# Todo — #487 First 10-arm tournament from the current setup

Branch: `i487/Plan-the-first-10-arm-tournament-from-the-current-setup` | Issue: #487
Tier: Standard | Task type: Code (+ Docs) | 9 tasks, 2 checkpoints

- [x] **T1** `XS` `[Docs]` Replace the stale `#749` planning set — `CONSTRAINTS.md`, `SPEC.md`
- [ ] **T2** `S` `[Code]` Arms-spec test (**RED**) — `tests/test_shadow_tournament.py`
- [ ] **T3** `S` `[Code]` Arm spec (GREEN) — `configs/tournaments/i487_current_setup_10arm_arms.json`
- [ ] **▶ Checkpoint 1** — the spec exists and validates: print the ten arm/env rows
- [ ] **T4** `S` `[Code]` Rewrite `ARMS_CONFIG_487` — `scripts/tournament_cockpit_server.py`
- [ ] **T5** `S` `[Code]` `TOURNAMENTS["487"]` metadata + named-stamp resolution
- [ ] **T6** `S` `[Code]` Cockpit test — `tests/test_tournament_cockpit_arms.py` (new)
- [ ] **▶ Checkpoint 2** — cockpit at `http://127.0.0.1:8800/487` shows ten arms + hypotheses
- [ ] **T7** `S` `[Docs]` Runbook — `docs/runs/2026-10-21-487-current-setup-tournament.md`
- [ ] **T8** `XS` `[Code]` Refresh the superseded-premise comment on `I487_TWEAKS`
- [ ] **T9** `XS` `[Code]` Dry-run validation of the exact launch command

## Not this issue

- Launching the 12-hour run — the operator's command (T7 documents it).
- Any verdict or comparison — after the run.
- Removing the `🚧 Blocked` label — the operator's call.
- Any change to `core_brain/config.py`, `TOURNAMENT_PRESETS`, or strategy code.
