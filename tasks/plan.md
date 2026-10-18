# Plan — Issue #653: Move the screener scripts, environment overrides and tests onto the canonical config names

Branch: freebuff/canonical-filter-environment-variable-migration-56052569-b2ae-4f2f-b1cb-faf1732f99b3 | Issue: #653
Size Tier: Standard | Task Types: Code, Docs

## CodeRabbit Plan Intake (Station II Step 0A)
- **Adopted:** Reuse `core_brain.config._env_get` in `scoring/config.py:load()` for the 8 `FILTER_*` bars + 2 `FILTER_*_TRIAL` bars; safe finite `>= 0` conversion for `FILTER_LIVE_VOL_MIN_24H_USD`; remove `getattr` fallbacks in `scripts/filter_markets.py`; scrub `FILTER_*` and `BOT_*` in `tests/conftest.py` and `tests/test_ranker_reads_env_file.py`; document canonical vars in `.env.example` and `docs/agents/python-conventions.md`.
- **Rejected / Already Done:** PR #668 (`978687f`) already landed #652/#662 prerequisite and partial script/test attribute renames; `dashboard/server.py` edits are excluded per operator Job Guards (avoids conflict with Agent 1).
- **Resolved Open Questions:**
  1. `dashboard/server.py` collision: deferred/excluded per Job Guard (Agent 1 owns dashboard).
  2. Where canonical env reads live: reuse `_env_get` imported from `core_brain.config`.
  3. Vocabulary: shipped field names uppercased (`FILTER_*`, `BOT_*`) are authoritative.
  4. Sequencing: #652 (#662) and #668 are already in `HEAD` (`978687f`).

## Improvement Proposal (Station II Step 5)
- **Evidence:** `scripts/filter_markets.py:97` has `MIN_VOLUME_24H_LIVE = getattr(_CFG, "filter_live_vol_min_24h_usd", 10_000.0)` and `:2788-2790` has `if not getattr(cfg, "enforce_max_queue_minutes", False): ... bar = float(getattr(cfg, "select_max_queue_minutes", 0.0) or 0.0)`.
- **Proposal (Simplification / edge-case hardening — adopted):** Replace all remaining `getattr(_CFG, ..., <literal>)` and `getattr(cfg, ...)` in `scripts/filter_markets.py` (`MIN_VOLUME_24H_LIVE`, `MIN_RANGE_CENTS`, `VOLATILITY_WINDOW_SEC`, `VELOCITY_GATE_ENABLED`, `MIN_ACTIVITY_RATIO`, `band_low`, `band_high`, `max_pair_cost`, and `resolve_queue_bar`) with direct canonical attribute access (`cfg.filter_std_gate_queue_max_on`, `cfg.filter_std_queue_max_clear_min`, etc.) so any missing setting fails loudly with `AttributeError` rather than silently substituting a stale literal like `10_000.0`.

---

## Tasks

### [x] T1 — Canonical `FILTER_*` environment overrides in `scoring/config.py` and precedence tests
- **Size:** M | **Domain:** `[Backend/Logic]` | **Skill:** `api-and-interface-design`
- **Depends on:** none
- **Target files:** `scoring/config.py`, `tests/conftest.py`, `tests/test_ranker_reads_env_file.py`, `tests/test_config_single_source.py`
- **What is built:**
  1. Import `_env_get` from `core_brain.config` in `scoring/config.py`.
  2. Route all 8 screener filter bars and 2 trial bars in `scoring.config.load()` through `_env_get(canonical, *legacy)`:
     - `FILTER_COMMON_VOL_MIN_TOP3_DEPTH_USD_TRIAL` -> `HUNTER_DEPTH_TRIAL_USD` -> `kw["filter_common_vol_min_top3_depth_usd_trial"]`
     - `FILTER_STD_VOL_MIN_24H_USD_TRIAL` -> `HUNTER_VOLUME_TRIAL_USD` -> `kw["filter_std_vol_min_24h_usd_trial"]`
     - `FILTER_LIVE_VEL_MIN_USD` -> `HUNTER_MIN_MOVEMENT_USD` -> `kw["filter_live_vel_min_usd"]`
     - `FILTER_LIVE_VEL_LOOKBACK_SEC` -> `HUNTER_MOVEMENT_WINDOW_SEC` -> `kw["filter_live_vel_lookback_sec"]`
     - `FILTER_LIVE_GATE_VELOCITY_ON` -> `HUNTER_VELOCITY_GATE` -> `kw["filter_live_gate_velocity_on"]`
     - `FILTER_LIVE_MOVE_MIN_RANGE_CENTS` -> `HUNTER_MIN_RANGE_CENTS` -> `kw["filter_live_move_min_range_cents"]`
     - `FILTER_LIVE_MOVE_LOOKBACK_SEC` -> `HUNTER_VOLATILITY_WINDOW_SEC` -> `kw["filter_live_move_lookback_sec"]`
     - `FILTER_LIVE_ACTIVITY_MIN_RATIO` -> `HUNTER_MIN_ACTIVITY_RATIO` -> `kw["filter_live_activity_min_ratio"]`
     - `FILTER_STD_VOL_MIN_24H_USD` -> `HUNTER_MIN_VOLUME_24H_USD` -> `kw["filter_std_vol_min_24h_usd"]`
     - `FILTER_LIVE_VOL_MIN_24H_USD` (canonical only; finite `>= 0` sets `kw["filter_live_vol_min_24h_usd"]`, invalid/negative retains `35_000.0`).
  3. Update `tests/conftest.py` (`scrub_tuning_env`) and `tests/test_ranker_reads_env_file.py` subprocess env filters to scrub `FILTER_*` and `BOT_*` alongside `HUNTER_*`, plus pop `FILTER_STD_VOL_MIN_24H_USD_TRIAL` in `_bar_with_generated_env`.
  4. Add parametrized tests in `tests/test_config_single_source.py` covering canonical `FILTER_*` overrides, canonical precedence over `HUNTER_*`, blank/whitespace canonical fallthrough to `HUNTER_*`, and refused invalid inputs for `FILTER_LIVE_VOL_MIN_24H_USD`.
- **Verification:** `python -m pytest tests/test_config_single_source.py tests/test_ranker_reads_env_file.py tests/test_config_divergence_guard.py -q`

### [x] T2 — Direct canonical attribute reads in screener scripts and test fixture migration
- **Size:** M | **Domain:** `[Backend/Logic]` | **Skill:** `code-simplification`
- **Depends on:** T1
- **Target files:** `scripts/filter_markets.py`, `scripts/filter_loop.py`, `tests/test_movement_gate.py`, `tests/test_velocity_gate.py`, `tests/test_maker_queue_bar.py`
- **What is built:**
  1. In `scripts/filter_markets.py`:
     - Replace `getattr(_CFG, ...)` at lines 97, 102–105, 1598–1599, 2677 with direct attribute reads (`_CFG.filter_live_vol_min_24h_usd`, `_CFG.filter_live_move_min_range_cents`, `_CFG.filter_live_move_lookback_sec`, `_CFG.filter_live_gate_velocity_on`, `_CFG.filter_live_activity_min_ratio`, `_CFG.bot_quote_price_band_min_cents`, `_CFG.bot_quote_price_band_max_cents`, `_CFG.max_pair_cost`).
     - Replace `_CFG.select_max_book_spread` at line 101 with `_CFG.filter_common_spread_max_cents`.
     - In `resolve_queue_bar(cfg)` (:2788–2790), read `cfg.filter_std_gate_queue_max_on` and `cfg.filter_std_queue_max_clear_min` directly without `getattr`.
     - Update docstrings/comments referencing retired names (`select_min_volume_24h_usd`, `select_min_top3_depth_usd`, `enforce_max_queue_minutes`, `select_min_movement_usd`, `price_band_low/high`) in `scripts/filter_markets.py` and `rerank_interval_sec` in `scripts/filter_loop.py:7`.
  2. Migrate test fixtures:
     - `tests/test_movement_gate.py`: use `FILTER_LIVE_VEL_MIN_USD` / `FILTER_LIVE_VEL_LOOKBACK_SEC` and assert `cfg.filter_live_vel_min_usd` / `cfg.filter_live_vel_lookback_sec`.
     - `tests/test_velocity_gate.py`: use `FILTER_LIVE_MOVE_LOOKBACK_SEC`, `FILTER_LIVE_MOVE_MIN_RANGE_CENTS`, `FILTER_LIVE_GATE_VELOCITY_ON` and assert `filter_live_move_min_range_cents`, `filter_live_move_lookback_sec`, `filter_live_gate_velocity_on`.
     - `tests/test_maker_queue_bar.py`: update `TestConfigDefaults`, stand-in `Cfg` classes in `TestTheBarClimbsTheWholeStack`, and `dc_replace(fm._CFG, ...)` to use `filter_std_queue_max_clear_min` and `filter_std_gate_queue_max_on`.
- **Verification:** `python -m pytest tests/test_movement_gate.py tests/test_velocity_gate.py tests/test_maker_queue_bar.py tests/test_filter_loop.py -q`

### [x] T3 — Document canonical `FILTER_*` and `BOT_*` variables in `.env.example` and `docs/agents/python-conventions.md`
- **Size:** S | **Domain:** `[Docs]` | **Skill:** `documentation-and-adrs`
- **Depends on:** T1, T2
- **Target files:** `.env.example`, `docs/agents/python-conventions.md`, `docs/issues/653-noticed-but-not-touching.md`
- **What is built:**
  1. Add commented sections in `.env.example` for the Market Screener (`FILTER_*` canonical variables + trial variables, noting defaults and deprecated `HUNTER_*` aliases, and noting `FILTER_LIVE_VOL_MIN_24H_USD` has no legacy alias) and Bot Engine (`BOT_*` canonical variables read by `core_brain/config.py:from_env` and their deprecated `HUNTER_*` aliases).
  2. Update the "Canonical environment variables" bullet in `docs/agents/python-conventions.md` to state that canonical environment variables are the uppercase canonical field names (`FILTER_*` and `BOT_*`), with `HUNTER_*` supported as deprecated fallback aliases.
  3. Record any out-of-scope observations in `docs/issues/653-noticed-but-not-touching.md`.
- **Verification:** Verify every env var in `.env.example` is actually read by `scoring/config.py` or `core_brain/config.py`, and run all 7 targeted test suites.
