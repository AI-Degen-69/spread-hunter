# Noticed but not touching — #492

Scope guard for #492. Every row is a defect or drift this station noticed and deliberately
left unchanged. Status stays `open` and Resolved-by stays empty until a follow-up claims the
row. Owner is `you` (the operator) when no existing issue covers it, otherwise the issue
number. Dates are when the row was written.

## Discovered while planning and building (owner: planner, 2026-10-18)

| Noticed | Why out of scope | Owner | When | Status | Resolved-by |
|---|---|---|---|---|---|
| `runtime/tournaments/484_12hr.json` and `runtime/tournaments/485_12hr.json` — the files #492 names as the source of the arm definitions — do not exist in the working tree or in git history. The first-run values survive only in `configs/tournaments/i492_tournament-1_arms.json` (committed in `ba96f94`) and in cockpit copy | Recreating them would be a second copy of the same definitions and the rerun reads the i492 file; the missing provenance is a record-keeping gap, not a blocker for this run | you | 2026-10-18 | open | |
| `configs/tournaments/i608_tournament-1_arms.json` and `configs/tournaments/i622_tournament-1_arms.json` still carry retired knobs (`HUNTER_STOP_LOSS_TRIGGER_USD_CENTS`, `HUNTER_SINGLE_BUY_GRACE_SEC`, `HUNTER_REFUSAL_HOLD_POLICY`). `core_brain/config.py` warns and ignores them, and `tests/test_i608_character_arms_plan` pins their presence | Those files define other issues' runs; editing them would rewrite recorded definitions #492 does not own. The warning makes the dead knob visible at every launch | you | 2026-10-18 | open | |
| `scripts/shadow_tournament.py:_preset_to_env` still maps `stop_loss_trigger_usd_cents` onto `HUNTER_STOP_LOSS_TRIGGER_USD_CENTS` although no preset carries that key any more, so the branch is unreachable | Guarded by `if ... in preset`, so it is safe. This change touches `build_tournament_plan` and `main()` only; deleting a dead branch is cleanup, already noted by #619 | you | 2026-10-18 | open | |
| `scripts/tournament_cockpit_server.py` still presents the retired knobs as tunable copy ("Single-Buy Stop Cap $0.045", "Single-Buy Grace Window 45.0s") | For the #484/#485 stores those values are historically true and the cockpit is frozen on them — it is the "before" side of the comparison. Rewriting the presentation is a separate call, already noted by #619 and #622 | you | 2026-10-18 | open | |
| The new per-issue arms lookup applies to every issue, so `--issue 622 --run-number 1` now auto-loads `i622_tournament-1_arms.json`, whose retired `HUNTER_REFUSAL_HOLD_POLICY` key the plan-time guard does not check (it validates `HUNTER_TOURNAMENT_PRESET` only) | The lookup is exactly what #492's acceptance command needs; a shared retired-knob list in production code is scope expansion and would duplicate the guard the tests already carry for i492 | you | 2026-10-18 | open | |
