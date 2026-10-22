# CONSTRAINTS — #753 Retire the dead hold-lever surface

Branch: `i753/retire-dead-hold-levers` | Issue: #753

## Zero regressions

- Focused checks per task, run by the agent (never handed to the operator):
  - `python -m pytest -q tests/test_requote_hold_retired.py tests/test_trader_loop.py tests/test_cancel_attribution.py`
  - `python -m pytest -q tests/test_dynamic_offset.py tests/test_completable_pair_gate.py tests/test_loose_baseline.py tests/test_screener_preset.py tests/test_config_single_source.py`
  - `python -m pytest -q tests/test_dashboard_server.py tests/test_ui_276_explainer_tokens.py`
  - `python -m pytest -q tests/test_shadow_tournament.py`
- `python -m pytest -q` stays with GitHub CI (Ubuntu + Windows) — the merge gate.
- Any test that passes a removed parameter, or asserts a field that no longer
  exists, is updated or deleted **in the task that removes the parameter** — never
  left red between tasks.

## Behaviour contract (must not move)

- `plan_orders` keeps its lifecycle decisions: preserve / replace / cancel by id,
  keep-wanted-token-at-its-own-price, duplicate-intent suppression via
  `held_tokens`, delta top-up, `not_quoted` cancels, `reasons` out-parameter.
- `cancel_report` output contracts: read-only SQLite, `summary` keys `db_path`,
  `readable`, `cancelled`, `unattributed`, `reasons`; `REASON_ORDER` first then
  sorted unknowns; explicit `queue unmeasured`; distinct UNREAD message;
  `price_moved` share uses `summary["cancelled"]` (all cancels) as denominator;
  `--db`; exit 0 readable / 1 unreadable.
- `TOURNAMENT_PRESETS` keeps its six names — `test_dynamic_offset.py:138` and
  `test_screener_preset.py:92` pin the name set against `SCREENER_PRESETS`.
- The dashboard keeps the requote-cadence and dead-band cards unchanged.
- The explainer keeps its design tokens (`test_ui_276_explainer_tokens.py`).

## Anti-cheat

- No deleting a test without replacing its intent (removal → absence assertion),
  no `xfail`, no `skip`, no widened `pytest.raises`, no exception swallowing.
- No weakening of `_RETIRED_PRESET_KEYS` or of the arm-env validation to make an
  arm file load — the arm files are edited instead.
- No removal of assertions that still verify live behaviour (delta top-up,
  dead band, refusal hold, unattributed cancels).

## Dependencies

- No new external dependency. Standard library + existing pytest only.

## Deliberate states (do not "fix" in review)

- `_hold_state()` is deleted, not re-implemented: it read the *reporting*
  process's config, not the config of the database being reported.
- `hedge_asks` / `hedge_held` are deleted from `_run_one_cycle` even though they
  are not literally a "hold lever" — their only consumer was `plan_kwargs`.
  Leaving them computed would be new dead code.
- The #485 cockpit cards keep their `worst_catalyst` text (a real forensic
  finding); only `hypothesized_tweak` gains a retired marker. Deleting the whole
  card would erase the only record that the experiment ran.
- `docs/runs/*` keeps every mention. Dated run records are evidence, not docs to
  rewrite.
