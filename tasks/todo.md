# Todo — #753 Retire the dead hold-lever surface

Branch: `i753/retire-dead-hold-levers` | Issue: #753 | Plan: `tasks/plan.md`

## T1 · Config surface [Backend/Logic] · S
- [ ] Delete both `MakerConfig` fields (`config.py:197-198`)
- [ ] Delete their env parsing + bounds (`config.py:~375-383`)
- [ ] Remove both keys from all six `TOURNAMENT_PRESETS`
- [ ] Rewrite the four "... with queue hold" descriptions + hybrid's "queue stability"
- [ ] Drop both variables and the unit note from `.env.example`
- [ ] Update `test_dynamic_offset.py` preset pins + `test_completable_pair_gate.py` bound tests

## T2 · Planner surface [Backend/Logic] · M
- [ ] Drop six unread params from `plan_orders` signature
- [ ] Drop four keys from `plan_kwargs`
- [ ] Delete `hedge_asks` / `hedge_held` computation blocks
- [ ] Keep `queue_ahead` (attribution) and `visit_outcome.value` (registry write)
- [ ] Replace the two "RETIRED BY #387" docstring blocks with one paragraph
- [ ] Delete/adjust hold tests in `test_plan_orders_asymmetric_hold.py`, `test_trader_loop.py`, `test_cancel_attribution.py`

## T3 · Operator surfaces [UI] · S
- [ ] Delete the two dashboard parameter cards
- [ ] Delete the two explainer rows
- [ ] Delete the two HYBRID blueprint table rows in the cockpit
- [ ] Annotate the two #485 cockpit card tweaks as retired by #387
- [ ] Update `test_dashboard_server.py` env-key map + `test_shadow_tournament.py` blueprint keys

## T4 · Launcher, arm files, written record [Backend/Logic] · S
- [ ] Extend `_RETIRED_PRESET_KEYS` with both field names
- [ ] Reject both retired `BOT_*` names on the `arm["env"]` path, message names #387
- [ ] Clean `i492_tournament-1_arms.json` (6 arms)
- [ ] Delete the two now-empty arms from `i608_tournament-1_arms.json`
- [ ] Update `test_shadow_tournament.py` fixtures + convert the legacy-alias test
- [ ] Mark N1-N3 resolved; drop hold renames from the variable-names idea file

## T5 · Absence test [Backend/Logic] · S
- [ ] New `tests/test_requote_hold_retired.py` covering all eight acceptance bars
- [ ] Confirm it is red without the change, green with it

## Checkpoints
- [ ] After T2: focused planner/config checks green
- [ ] After T4: repo grep clean outside historical cockpit annotations
- [ ] After T5: full focused set green
