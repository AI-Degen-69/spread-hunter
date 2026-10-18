# Todo — Issue #652

Branch: `i652/rename-picker-filter-bars` | Plan: [tasks/plan.md](tasks/plan.md)

## T1 — Guard repointed and written RED first  `[Backend/Logic]` · S
- [ ] Delete `PICKER_ONLY`; add `CORE_OWNED_PICKER_BARS` (eight canonical names)
- [ ] Add pinned `PICKER_BAR_ALIASES` (the eight old → canonical pairs, written out)
- [ ] Replace `test_picker_only_fields_do_not_exist_on_core` with the affirmative core-field /
      scoring-reachability / equal-defaults / alias-table test
- [ ] Narrow the class-body allow-list to `PROFILE_NAMES` only (a re-declared canonical bar must fail)
- [ ] Add reverse reachability: `scoring_fields - core_fields == set()`
- [ ] Add the class-body property/method check: the set is `{db_path}`
- [ ] Run it: must FAIL against the pre-change class body; keep the failure output

## T2 + T3 — Core owns, picker inherits (one commit)  `[Backend/Logic]` · M
- [ ] `core_brain/config.py`: add the eight pairs to `_LEGACY_TO_CANONICAL` in a labelled picker block
- [ ] `core_brain/config.py`: add `filter_common_spread_max_cents` next to `select_max_book_spread`
- [ ] `core_brain/config.py`: add that name to the `_READ_ONLY_PROPERTIES` set
- [ ] Wiring loop (`core_brain/config.py:766-774`), canonical defaults, `filter_std_spread_max_book`: untouched
- [ ] `scoring/config.py`: delete the eight declarations and their local comments
- [ ] `scoring/config.py`: refresh the module docstring and the "bars only the ranker gates on" heading
- [ ] `scoring/config.py`: six `load()` dict keys → canonical; variables, parsing, bounds, fallbacks unchanged
- [ ] Five `PICKER_PROFILE` overrides and `db_path()` byte-identical
- [ ] No new env var for the two queue bars

## T4 — Regression pins  `[Backend/Logic]` · S
- [ ] `SCORING_DEFAULTS`: no value edited; no entry renamed (all 82 already resolve)
- [ ] Six parametrised env tests: clear the other five, set one, assert the canonical field
- [ ] Each: `pytest.warns(DeprecationWarning)` on the old-name read, equal value; `"off"` → `False`
- [ ] All-eight old-name warning + value test on a `scoring.config.load()` config
- [ ] Pristine-load defaults: 200.0 / 1800.0 / True / 0.0 / 2.0 / 7200.0 / 15.0 / False
- [ ] Derived-cap pins: 0.0205 default, `== select_max_book_spread`, 0.1025 at `max_book_spread=0.30`,
      0.15 at `wide_book_trial=0.15`, `TypeError` from the constructor, absent from fields,
      `AttributeError` on assignment — on both classes
- [ ] Confirm each new test fails without T2+T3

## Checks (agent-run)
- [ ] RED: `python -m pytest -q tests/test_config_divergence_guard.py` before T2
- [ ] GREEN: `python -m pytest -q tests/test_config_divergence_guard.py tests/test_config_single_source.py tests/test_market_selection_bars.py`
- [ ] GREEN: `python -m pytest -q tests/test_velocity_gate.py tests/test_movement_gate.py tests/test_maker_queue_bar.py tests/test_filter_loop.py tests/test_config_for_display.py tests/test_queue_clear_gate.py tests/test_wide_book_trial.py tests/test_loose_baseline.py`
- [ ] `git diff --stat` shows no edit to `dashboard/`, `scripts/filter_markets.py`, or any pinned number

## Handoff
- [ ] Operator "How to verify" block (dashboard picker cards) included in the PR body — no pytest steps
- [ ] PR body: no value changed; base is `main` (#651 already merged); no `SCORING_DEFAULTS` entry renamed;
      the `_cents` suffix note; the unchanged `replace()` limitation
