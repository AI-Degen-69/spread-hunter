# SPEC - #632: Standardize parameter & env naming

Branch: main (planning) | Issue: #632

## Goal
One canonical lowercase name per setting with explicit unit suffix.
Zero `_frac`/`_fraction` field/param names in active code. Canonical
`HUNTER_*` first + legacy fallback + warning. Full detail lives in
`tasks/plan.md` (Spec section + Interface contract); this file is the
stable pointer so Station III never re-derives scope.

## Non-behavior contract
All defaults and math unchanged. Legacy aliases + env fallbacks kept.

## Out of scope
Rescaling, deleting aliases, `data/orders.db`, opening commands.
