# CONSTRAINTS — Issue #652 (locked at Station II, 2026-10-18)

Scope: the two config modules and the tests that police them. Branch `i652/rename-picker-filter-bars`.

## 1. Zero value drift — the hard contract

Every one of the eight canonical defaults stays exactly `200.0 / 1800.0 / True / 0.0 / 2.0 / 7200.0 /
15.0 / False`. No figure in `tests/test_config_single_source.py::SCORING_DEFAULTS` (82 entries) is edited,
and no entry is renamed — all 82 resolve after the change (measured pre-change at Station II). A value
change is a different ticket (#572 / #573 precedent).

## 2. Zero regressions

- Targeted suites covering the touched files pass before review and again after review fixes:
  `python -m pytest -q tests/test_config_divergence_guard.py tests/test_config_single_source.py tests/test_market_selection_bars.py`
- The reader/regression selection stays green and **unmodified**: `tests/test_velocity_gate.py`,
  `tests/test_movement_gate.py`, `tests/test_maker_queue_bar.py`, `tests/test_filter_loop.py`,
  `tests/test_config_for_display.py`, `tests/test_queue_clear_gate.py`, `tests/test_wide_book_trial.py`,
  `tests/test_loose_baseline.py`. Their call sites are not edited to make anything pass.
- Every changed behaviour carries a test that fails without the change: the guard repoint (A5), the
  six env overrides end to end (A4), old-name reads + warnings (A2), the derived-cap alias (A1).
- The full `python -m pytest -q` suite runs in GitHub CI on Ubuntu and Windows; it is not repeated locally
  for this ticket.

## 3. Guard integrity — no passing because empty

The divergence guard's class-body allow-list is `PROFILE_NAMES` **only**. Re-declaring a canonical bar in
the scoring class body fails even when the value is identical. `CORE_OWNED_PICKER_BARS` may never be used
as an excuse list for the class body, and the guard may never be satisfied by an empty set without an
affirmative counterpart assertion.

## 4. Untouched surfaces

`dashboard/server.py`, `dashboard/static/strategy_explainer.html`, `scripts/filter_markets.py`: **zero
edits** — correct aliases keep their reads right. The `bot_*` execution side, the class name,
`filter_std_spread_max_book`, the `#632` `_frac`/`_fraction` properties, the constructor wrapper's
precedence rule and `docs/ideas/variable-names-refactor.md`: out of scope.

## 5. Anti-cheat

No skipping, xfailing, deleting or weakening a test, no removed assertion, no `filterwarnings` rule added to
`pytest.ini` to silence `DeprecationWarning`, no linter/type suppression, no stub or placeholder. The
deprecation warnings are the designed behaviour of the alias mechanism — tests assert them, they do not
suppress them.

## 6. Dependencies and performance

No new external dependency, no new env var (specifically none for the two queue bars, which had none before),
no new mechanism: the alias table, its descriptor and its synthetic-field wiring are reused as shipped.

## 7. Interface freeze

The eight old names, `filter_common_spread_max_cents`, and every existing canonical name keep the contract
in `tasks/plan.md` §4. Any change to that contract is a scope change and needs the operator.
