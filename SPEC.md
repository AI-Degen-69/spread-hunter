# SPEC — #603: Delete the _route_pair exit helper that no live code calls

## Goal
Remove the dead `_route_pair` helper from `core_brain/single_buy_saver.py`
(last production caller went with the #589 ladder removal) after moving its
real test coverage — completion, adverse-drift exit, grace-hold, trigger-bid
recording (#525) — onto the unified lifecycle (`manage_single_leg_positions`).

## Scope
Covers: deleting `_route_pair`, re-homing its tests, rewording the two
`core_brain` docstrings that name it.
Does NOT cover: lifecycle/exit-semantics changes, the `ladder_exit` taxonomy
string (#558–560 chain), any other dead-code removal.

## Acceptance criteria
- `grep -rn "_route_pair" core_brain --include="*.py"` returns nothing.
- The moved coverage passes on the unified lifecycle:
  `tests/test_single_buy_saver.py tests/test_refill_after_exit.py
  tests/test_exit_price_realism.py tests/test_grace_sweep_report.py`.
