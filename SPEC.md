# SPEC - #652: Rename the picker's legacy filter bars to the canonical config names

Branch: `i652/rename-picker-filter-bars` | Issue: #652

## Goal
The market picker's last eight legacy bars (`select_*`, `enforce_max_queue_minutes`) stop being declared
on `scoring.config.MakerConfig` and are inherited from `core_brain.config.MakerConfig`. Old names stay
readable through `_LEGACY_TO_CANONICAL`, and `filter_common_spread_max_cents` is added as a read-only
alias for the derived picking cap. Full detail lives in `tasks/plan.md` (Spec + Interface contract); this
file is the stable pointer so Station III never re-derives scope.

## Non-behavior contract
No value changes: 200.0 / 1800.0 / True / 0.0 / 2.0 / 7200.0 / 15.0 / False and all 82 pinned
`SCORING_DEFAULTS` entries hold. Parsing, clamping, finite checks and fallbacks are untouched. Legacy
aliases and their `DeprecationWarning` are kept.

## Sequencing
The ticket's own Open Question 4 is stale: PR #651 is merged into `main` (`214d196`, an ancestor of
`01157a3`), so this work branches from `main`, not from a stacked branch.

## Out of scope
Value changes, the `bot_*` side, dashboard keys, `scripts/filter_markets.py`, the class name, the
constructor's `replace()` precedence rule, `docs/ideas/variable-names-refactor.md`, `data/orders.db`,
opening commands.
