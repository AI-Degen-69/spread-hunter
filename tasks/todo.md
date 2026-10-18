# Todo — Issue #652

Branch: `i652/rename-picker-filter-bars` | Plan: [plan.md](plan.md)

## T1 — Guard repointed and written RED first  `[Backend/Logic]` · S
- [x] Delete `PICKER_ONLY`; add `CORE_OWNED_PICKER_BARS` (eight canonical names)
- [x] Add pinned `PICKER_BAR_ALIASES` (the eight old → canonical pairs, written out)
- [x] Replace `test_picker_only_fields_do_not_exist_on_core` with the affirmative core-field /
      scoring-reachability / equal-defaults / alias-table test
- [x] Narrow the class-body allow-list to `PROFILE_NAMES` only (a re-declared canonical bar must fail)
- [x] Add reverse reachability: `scoring_fields - core_fields == set()`
- [x] Add the class-body property/method check: the set is `{db_path}`
- [x] Run it: FAILED against the pre-change class body (`3 failed, 11 passed`)

## T2 + T3 — Core owns, picker inherits (one commit)  `[Backend/Logic]` · M
- [x] `core_brain/config.py`: add the eight pairs to `_LEGACY_TO_CANONICAL` in a labelled picker block
- [x] `core_brain/config.py`: add `filter_common_spread_max_cents` next to `select_max_book_spread`
- [x] `core_brain/config.py`: add that name to the `_READ_ONLY_PROPERTIES` set
- [x] Wiring loop, canonical defaults, `filter_std_spread_max_book`: untouched
- [x] `scoring/config.py`: delete the eight declarations and their local comments
- [x] `scoring/config.py`: refresh the module docstring and the section heading
- [x] `scoring/config.py`: six `load()` dict keys → canonical; variables, parsing, bounds, fallbacks unchanged
- [x] Five `PICKER_PROFILE` overrides and `db_path()` byte-identical
- [x] No new env var for the two queue bars

## T4 — Regression pins  `[Backend/Logic]` · S
- [x] `SCORING_DEFAULTS`: no value edited; no entry renamed (all 82 resolve)
- [x] Six parametrised env tests: clear the other five, set one, assert the canonical field
- [x] Each: `pytest.warns(DeprecationWarning)` on the old-name read, equal value; `"off"` → `False`
- [x] All-eight old-name warning + value test on a `scoring.config.load()` config
- [x] Pristine-load defaults: 200.0 / 1800.0 / True / 0.0 / 2.0 / 7200.0 / 15.0 / False
- [x] Derived-cap pins: 0.0205 default, `== select_max_book_spread`, 0.1025 at `max_book_spread=0.30`,
      0.15 at `wide_book_trial=0.15`, `TypeError` from the constructor, absent from fields,
      `AttributeError` on assignment — on both classes
- [x] Confirm each new test fails without T2+T3 (17 of 27 new cases fail with the config reverted; the
      10 that pass either way are the values and refusal pins)

## T4b — coverage from the `tdd-guide` persona review
- [x] Dashboard cards: overrides show `$777.00` / `900s` / `False`; defaults show `15.0m` +
      `False (Telemetry Only)` — on the real `/api/parameters`
- [x] Mutation check: deleting one alias entry makes the card test fail on the silent fallback
- [x] Refused values (`abc`, `nan`, `inf`, `0`, `-1`, `-0.5`, `no`) keep the shipped bar

## Checks (agent-run) — all green
- [x] RED: `python -m pytest -q tests/test_config_divergence_guard.py` before T2
- [x] GREEN 66: `divergence_guard` + `single_source` + `market_selection_bars`
- [x] GREEN 215: `velocity_gate`, `movement_gate`, `maker_queue_bar`, `filter_loop`,
      `config_for_display`, `queue_clear_gate`, `wide_book_trial`, `loose_baseline`
- [x] GREEN 186: `dashboard_server` + `stop_loss_config`, `tsc_gate_config`, `dynamic_offset`,
      `dynamic_risk_caps`, `dynamic_caps_venue_floor`
- [x] `git diff --stat 01157a3..HEAD` shows no edit to `dashboard/`, `scripts/`, `data/`, or any
      pinned number

## Handoff
- [x] Config modules, tests and plan artifacts committed on the branch; tree clean
- [ ] Operator "How to verify" block (dashboard picker cards, with an override set) — Station IV's PR body
