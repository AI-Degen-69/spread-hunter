# Todo — #753 Retire the dead hold-lever surface

Branch: `i753/retire-dead-hold-levers` | Issue: #753 | Plan: `tasks/plan.md`

## T1 · Config surface [Backend/Logic] · S
- [x] Delete both `MakerConfig` fields (`config.py:197-198`)
- [x] Delete their env parsing + bounds (`config.py:~375-383`)
- [x] Remove both keys from all six `TOURNAMENT_PRESETS`
- [x] Rewrite the four "... with queue hold" descriptions + hybrid's "queue stability"
- [x] Drop both variables and the unit note from `.env.example`
- [x] Update `test_dynamic_offset.py` preset pins + `test_completable_pair_gate.py` bound tests

## T2 · Planner surface [Backend/Logic] · M
- [x] Drop six unread params from `plan_orders` signature
- [x] Drop four keys from `plan_kwargs`
- [x] Delete `hedge_asks` / `hedge_held` computation blocks
- [x] Keep `queue_ahead` (attribution) and `visit_outcome.value` (registry write)
- [x] Replace the two "RETIRED BY #387" docstring blocks with one paragraph
- [x] Delete/adjust hold tests in `test_plan_orders_asymmetric_hold.py`, `test_trader_loop.py`, `test_cancel_attribution.py`

## T3 · Operator surfaces [UI] · S
- [x] Delete the two dashboard parameter cards
- [x] Delete the two explainer rows
- [x] Delete the two HYBRID blueprint table rows in the cockpit
- [x] Annotate the two #485 cockpit card tweaks as retired by #387
- [x] Update `test_dashboard_server.py` env-key map + `test_shadow_tournament.py` blueprint keys

## T4 · Launcher, arm files, written record [Backend/Logic] · S
- [x] Extend `_RETIRED_PRESET_KEYS` with both field names
- [x] Reject both retired `BOT_*` names on the `arm["env"]` path, message names #387
- [x] Clean `i492_tournament-1_arms.json` (6 arms)
- [x] Delete the two now-empty arms from `i608_tournament-1_arms.json`
- [x] Update `test_shadow_tournament.py` fixtures + convert the legacy-alias test
- [x] Mark N1-N3 resolved; drop hold renames from the variable-names idea file

## T5 · Absence test [Backend/Logic] · S
- [x] New `tests/test_requote_hold_retired.py` covering all eight acceptance bars
- [x] Confirm it is red without the change, green with it

## Checkpoints
- [x] After T2: focused planner/config checks green
- [x] After T4: repo grep clean outside historical cockpit annotations
- [x] After T5: full focused set green
