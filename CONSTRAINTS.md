# CONSTRAINTS.md — Issue #872: Unify currency settings to honest units

Branch: `i872/unify-all-currency-settings-to-integer-cents` | Issue: #872

Locked during Station II planning. Stations III–V must hold every line below.
Tighten only with operator approval; never loosen silently.

## The one invariant this change exists to protect

**Every numeric value the engine applies is bit-identical before and after.**

`bot_order_requote_dead_band_usd` stays `0.03`, `bot_risk_stop_adverse_drop_usd` stays `0.05`,
the `hybrid` preset stays `0.060`, `bot_quote_spread_offset_min_cents`/`_max_cents` stay `1`/`20`.
The 4.5¢ champion stop (`tests/test_champion_lane_parity.py`, `docs/live-lane-audit.md`,
`scripts/tournament_cockpit_server.py`) keeps producing the same trigger cents at every entry
price. If any test expectation has to move for a value to pass, the change is wrong — re-read
the unit, do not edit the expectation.

## Zero regressions

- Targeted suites covering the touched modules must pass cleanly:
  - `python -m pytest -q tests/test_config.py`
  - `python -m pytest -q tests/test_config_single_source.py tests/test_config_divergence_guard.py`
  - `python -m pytest -q tests/test_stop_loss_config.py tests/test_single_leg_lifecycle.py tests/test_dual_stop_loss.py tests/test_risk_guardian.py tests/test_single_buy_saver.py`
  - `python -m pytest -q tests/test_trader_loop.py tests/test_completable_pair_gate.py tests/test_dynamic_offset.py`
  - `python -m pytest -q tests/test_champion_lane_parity.py tests/test_shadow_tournament.py tests/test_tournament_cockpit_arms.py tests/test_tournament_arms_i843.py`
  - `python -m pytest -q tests/test_dashboard_server.py`
- Full `python -m pytest -q` is the GitHub CI merge gate on Ubuntu and Windows; local runs are
  targeted only.
- Every new behavior needs a test that fails without it:
  - old `*_CENTS` name still loads when the new name is unset, with exactly one `WARNING`;
  - one warning per key per process, not one per `from_env()` call;
  - new name wins when both are set; blank new name falls through to the old one;
  - offset keys reject `19.9` / `6.0` / `abc` / `nan` / `inf` instead of truncating;
  - arms files use whole-cent text only, and load with no deprecation warning.

## Scope fences (hard)

- **Only these areas change:**
  - `core_brain/config.py` — the two field names, their loader blocks, the `hybrid` preset key,
    the alias reader, `_whole_cents()`.
  - `core_brain/single_leg_lifecycle.py` — `DROP_CENTS_MIN/MAX` → `DROP_USD_MIN/MAX` and the
    parameter names that carry the same dollar value.
  - The readers listed in `tasks/plan.md` §T2 (trader_loop, risk_guardian, single_buy_saver,
    shadow_tournament, tournament_hub, stream_market_decisions, verify_loose_baseline_tape).
  - `dashboard/server.py` — the two `get_parameters` cards, `name`/`key`/`env_var` only.
  - `.env.example`, `configs/tournaments/*.json`, `scripts/tournament_cockpit_server.py` copy,
    `docs/agents/strategy.md`, `docs/live-lane-audit.md`.
  - `tests/**` — mechanical renames plus the new `tests/test_config.py`.
  - `tasks/plan.md`, `tasks/todo.md`, `CONSTRAINTS.md`, `docs/backlog/i872-currency-unit-inventory.md`.
- **Must NOT be modified:**
  - `data/orders.db` (production registry, strictly read-only) and `data/tournaments/*/plan.json`.
  - Any engine arithmetic: `evaluate_single_leg_lifecycle` trigger math, merge/settlement,
    pair-cost gates.
  - Risk ratios (`*_pct` fields). Only the names of money amounts change.
  - The other 13 `_cents` fields — inventoried, not converted.
  - `dashboard/static/**` (currency formatting is out of scope for #872).
  - `core_brain/venue.py`, `core_brain/env_file.py`, `core_brain/order_registry.py`.

## Anti-cheat & safety rules

- **Anti-cheat:** no skipped/xfailed tests, no deleted assertions, no weakened bounds, no
  silent `int()` coercion left in place of the strict parser, no `# type: ignore`/`noqa` added
  to get green.
- **No new dependencies.** Standard library only; do not introduce `Decimal` into money math.
- **A retired name is not silently ignored.** The two old `_CENTS` spellings must warn loudly
  once per process. "Inert" would silently change the dead band from 10¢ to 3¢.
- **No live venue contact.** Planning, build, tests and review never run `quote`, `complete`,
  the Trader loop, or the dashboard START button.
- **No production database in any manual check.** Any `status` run points at a disposable DB.
