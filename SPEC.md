# SPEC - #653: Move the screener scripts, environment overrides and tests onto the canonical config names

Branch: freebuff/canonical-filter-environment-variable-migration-56052569-b2ae-4f2f-b1cb-faf1732f99b3 | Issue: #653

## Goal
Complete the migration of the market screener onto the canonical configuration vocabulary introduced in #570 and #652:
1. `scoring/config.py:load()` supports canonical `FILTER_*` uppercase environment variable overrides with seamless fallback to legacy `HUNTER_*` aliases via `core_brain.config._env_get`.
2. `scripts/filter_markets.py` and `scripts/filter_loop.py` read canonical field names directly with no retired names and no `getattr(_CFG, <name>, <literal>)` silent fallbacks.
3. `.env.example` and `docs/agents/python-conventions.md` document the canonical `FILTER_*` and `BOT_*` variables and note `HUNTER_*` as deprecated aliases.
4. Screener test suites (`tests/test_movement_gate.py`, `tests/test_velocity_gate.py`, `tests/test_maker_queue_bar.py`, `tests/test_ranker_reads_env_file.py`, `tests/conftest.py`, `tests/test_config_single_source.py`) use canonical names, scrub `FILTER_*` / `BOT_*` in test isolation, and verify canonical-over-legacy env precedence and blank-value fallthrough.

## Acceptance Criteria
- `scripts/filter_markets.py` and `scripts/filter_loop.py` read no retired config name; no `getattr(cfg, <name>, <literal>)` fallback remains on config bars.
- All eight canonical `FILTER_*` variables (`FILTER_LIVE_VEL_MIN_USD`, `FILTER_LIVE_VEL_LOOKBACK_SEC`, `FILTER_LIVE_GATE_VELOCITY_ON`, `FILTER_LIVE_MOVE_MIN_RANGE_CENTS`, `FILTER_LIVE_MOVE_LOOKBACK_SEC`, `FILTER_LIVE_ACTIVITY_MIN_RATIO`, `FILTER_LIVE_VOL_MIN_24H_USD`, `FILTER_STD_VOL_MIN_24H_USD`) plus the two trial variables (`FILTER_COMMON_VOL_MIN_TOP3_DEPTH_USD_TRIAL`, `FILTER_STD_VOL_MIN_24H_USD_TRIAL`) override their canonical fields through `scoring.config.load()`, with legacy `HUNTER_*` fallback.
- Tests prove canonical-wins-over-legacy, legacy-alone works, and blank/whitespace canonical values fall through to the legacy alias.
- Numeric defaults remain unchanged: `200.0 / 1800.0 / True / 0.0 / 2.0 / 7200.0 / 15.0 / False` (and `35_000.0 / 125_000.0`).
- `.env.example` documents every readable canonical variable and no variable the code does not read.
- All 7 targeted suites pass green.

## Out of Scope
- Any numeric default changes.
- `dashboard/server.py` or frontend UI files (handled separately to avoid conflict with Agent 1).
- `core_brain/quotes.py` or engine lifecycle files (handled separately to avoid conflict with Agent 2).
