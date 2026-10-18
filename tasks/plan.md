# Plan — Issue #672: Dashboard parameter cards still read legacy names and label legacy env vars

Branch: plan_issue_672 | Issue: #672
Size Tier: Standard | Task Types: Code, Refactor

## CodeRabbit Plan Intake (Station II Step 0A)
- **Adopted:**
  1. Replace all 19 `getattr(scfg, ...)` lines with direct `scfg.<canonical>` reads in `dashboard/server.py:get_parameters`.
  2. Replace all 41 plain alias reads (`cfg.<legacy>`) in `dashboard/server.py:get_parameters` with canonical field names from `_LEGACY_TO_CANONICAL` in `core_brain/config.py`.
  3. Replace all 16 `HUNTER_*` and `SPREAD_HUNTER_*` environment variable badges with canonical `FILTER_*` and `BOT_*` names from `.env.example`, keep `LIVE_SWEEP_INTERVAL` for `sweep_interval`, and set non-overridden badges to `""`.
  4. Add two focused tests in `tests/test_dashboard_server.py`: a `DeprecationWarning` absence guard scoped to `dashboard/server.py` and a full badge mapping test asserting all badges match `.env.example` without legacy prefixes.
  5. Compare pre-edit vs post-edit `/api/parameters` JSON to ensure only `env_var` changes while all values, raw values, units, defaults, and triggers remain identical.
- **Rejected:**
  1. Modifying `dashboard/static/app.js` — unnecessary as frontend already handles `""` natively (hides card badge, shows `—` in table, preserves sorting).
  2. Altering `derive_dynamic_caps` or loaders in `core_brain/config.py` — out of scope for parameter card refactor.
  3. Switching volume card value sources from `cfg` to `scfg` — out of scope and tracked for a follow-up issue.
  4. CodeRabbit's over-split subtask phases — consolidated into 3 vertical atomic tasks.
- **Resolved Open Questions:**
  1. *Authoritative mapping:* `core_brain.config._LEGACY_TO_CANONICAL` is the sole source of truth for converting old names.
  2. *Card keys/names:* All 59 card `key` and `name` attributes must remain untouched to avoid breaking frontend table sorting and anchor references.
  3. *Unset badge format:* Use empty string `""` uniformly instead of `None` for all cards without an env override.

## Improvement Proposal (Station II Step 5)
- **Evidence:** `dashboard/server.py:4459`, `:4476`, and `:5321` currently set `"env_var": None`, whereas other cards with no override or missing badges either omit it or use `None` inconsistently.
- **Proposal (Simplification / edge-case hardening — adopted):** Normalize all unset `env_var` fields across the entire 59-card list in `dashboard/server.py` to empty string `""`, guaranteeing a uniform `str` type contract across all parameter cards so frontend rendering and test assertions never face `null` vs `""` ambiguity.

---

## Tasks

### [x] T1 — Direct canonical attribute reads in `dashboard/server.py:get_parameters`
- **Size:** M | **Domain:** `[Backend/Logic]` | **Skill:** `code-simplification`
- **Depends on:** none
- **Target files:** `dashboard/server.py`
- **What is built:**
  1. In `dashboard/server.py:get_parameters`, replace all 19 `getattr(scfg, "<old_name>", <literal>)` calls across lines ~4319–4508 with direct attribute access on `scfg`:
     - `select_movement_window_sec` -> `scfg.filter_live_vel_lookback_sec`
     - `select_min_movement_usd` -> `scfg.filter_live_vel_min_usd`
     - `select_min_activity_ratio` -> `scfg.filter_live_activity_min_ratio`
     - `select_min_range_cents` -> `scfg.filter_live_move_min_range_cents`
     - `select_volatility_window_sec` -> `scfg.filter_live_move_lookback_sec`
     - `select_velocity_gate_enabled` -> `scfg.filter_live_gate_velocity_on`
     - `select_max_queue_minutes` -> `scfg.filter_std_queue_max_clear_min`
     - `enforce_max_queue_minutes` -> `scfg.filter_std_gate_queue_max_on`
  2. Replace all 41 plain alias attribute reads on `cfg` inside the `params` list with their canonical counterparts from `_LEGACY_TO_CANONICAL`:
     - Screener: `cfg.select_min_volume_24h_usd_live` -> `cfg.filter_live_vol_min_24h_usd`, `cfg.select_min_volume_24h_usd` -> `cfg.filter_std_vol_min_24h_usd`, `cfg.select_min_top3_depth_usd` -> `cfg.filter_common_vol_min_top3_depth_usd`, `cfg.select_max_days_to_resolve` -> `cfg.filter_std_time_max_days_to_resolve`, `cfg.select_max_book_spread` -> `cfg.filter_common_spread_max_cents`.
     - Bot Engine (Quoting, Positioning, Risk, Lifecycle): `cfg.tick_size` -> `cfg.bot_quote_tick_size`, `cfg.dynamic_offset_*` -> `cfg.bot_quote_*`, `cfg.price_band_*` -> `cfg.bot_quote_*`, `cfg.max_completable_pair_cost` -> `cfg.bot_pos_price_max_complete_usd`, `cfg.requote_*` -> `cfg.bot_order_*`, `cfg.stop_loss_mode` -> `cfg.bot_risk_stop_mode`, `cfg.drop_cents` -> `cfg.bot_risk_stop_adverse_drop_cents`, `cfg.drawdown_pct` -> `cfg.bot_risk_stop_max_drawdown_pct`, `cfg.emergency_drawdown_pct` -> `cfg.bot_risk_circuit_max_drawdown_pct`, `cfg.emergency_hedge_pct` -> `cfg.bot_risk_hedge_emergency_pct`, etc.
  3. Ensure all format specifiers, units (e.g. `¢`, `days`, `s (30m)`, `m`), and trigger f-strings preserve identical values.
- **Verification:** Save baseline `/api/parameters` response and verify no changes to `value`, `raw_value`, `default`, `trigger`, or `action`.

### [ ] T2 — Canonical environment variable badges in `dashboard/server.py:get_parameters`
- **Size:** S | **Domain:** `[Backend/Logic]` | **Skill:** `api-and-interface-design`
- **Depends on:** T1
- **Target files:** `dashboard/server.py`
- **What is built:**
  1. Purge all 16 `HUNTER_*` and `SPREAD_HUNTER_*` strings from `env_var` across all cards.
  2. Map cards with active overrides to canonical names from `.env.example`:
     - Screener: `FILTER_LIVE_VOL_MIN_24H_USD`, `FILTER_LIVE_VEL_LOOKBACK_SEC`, `FILTER_LIVE_VEL_MIN_USD`, `FILTER_LIVE_ACTIVITY_MIN_RATIO`, `FILTER_LIVE_MOVE_MIN_RANGE_CENTS`, `FILTER_LIVE_MOVE_LOOKBACK_SEC`, `FILTER_LIVE_GATE_VELOCITY_ON`, `FILTER_STD_VOL_MIN_24H_USD`.
     - Bot Engine: `BOT_QUOTE_BANK_BASELINE_USD`, `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID`, `BOT_POS_PRICE_MAX_PAIR_COST_USD`, `BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON`, `BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER`, `BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS`, `BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS`, `BOT_POS_PRICE_MAX_COMPLETE_USD`, `BOT_ORDER_REQUOTE_DEAD_BAND_CENTS`, `BOT_ORDER_REQUOTE_HOLD_BELOW_TARGET_CENTS`, `BOT_ORDER_REQUOTE_HOLD_QUEUE_SHARES`, `LIVE_SWEEP_INTERVAL`, `BOT_RISK_GATE_PAIRS_RULE_ON`, `BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC`, `BOT_RISK_STOP_MODE`, `BOT_RISK_STOP_ADVERSE_DROP_CENTS`, `BOT_RISK_STOP_MAX_DRAWDOWN_PCT`, `BOT_RISK_CIRCUIT_MAX_DRAWDOWN_PCT`.
  3. Set `env_var` to `""` for all remaining 33 cards (including `emergency_hedge_pct`, `select_min_top3_depth_usd`, `tick_size`, derived caps, etc.).
- **Verification:** Script check confirming every card's `env_var` is a string, no `HUNTER_` substrings exist, and every non-empty badge exists in `.env.example`.

### [ ] T3 — Automated tests in `tests/test_dashboard_server.py` & regression verification
- **Size:** S | **Domain:** `[Backend/Logic]` | **Skill:** `test-driven-development`
- **Depends on:** T1, T2
- **Target files:** `tests/test_dashboard_server.py`
- **What is built:**
  1. Add `test_parameters_endpoint_emits_no_deprecation_warnings(client)` in `tests/test_dashboard_server.py`:
     - Calls `/api/parameters` with `warnings.catch_warnings(record=True)` under `warnings.simplefilter("always")`.
     - Asserts no `DeprecationWarning` points to `dashboard/server.py` (ignoring out-of-scope warnings from `core_brain/config.py`).
  2. Add `test_parameters_cards_canonical_env_var_badges(client)` in `tests/test_dashboard_server.py`:
     - Compares all 59 cards against expected canonical badges dictionary.
     - Asserts `env_var` is `str` for every card.
     - Asserts no badge starts with `HUNTER_` or `SPREAD_HUNTER_`.
     - Asserts every non-empty badge appears in root `.env.example`.
  3. Validate JSON diff against saved baseline: confirm that `raw_value`, `value`, `default`, `trigger`, and `action` are 100% identical.
  4. Run targeted suites to verify zero regressions.
- **Verification:** `python -m pytest -q tests/test_dashboard_server.py tests/test_config_single_source.py`

---

## Checkpoints
- **Checkpoint 1 (after T1):** Baseline JSON comparison passes with zero diff on `raw_value`, `value`, `default`, `trigger`.
- **Checkpoint 2 (after T2):** Badge inventory script confirms 26 canonical badges, 33 empty badges, 0 `HUNTER_` badges.
- **Checkpoint 3 (after T3):** Deprecation warning test passes, badge map test passes, and full targeted test suite is green.
