# MISSION: Refactor Trading Bot & Screener Configuration Architecture

You are tasked with refactoring our trading engine configuration variables to follow an enterprise hierarchical naming convention, while fixing several logic bugs, unit mismatches, and namespace collisions identified during a system audit.

## 1. Core Architecture & Strict Naming Standard

Replace all legacy/ad-hoc variable names across `.env`, config files, and the codebase with this deterministic syntax:
`[DOMAIN]_[SCOPE]_[CATEGORY]_[METRIC/DESCRIPTOR]_[UNIT/LOGIC]`

### Levels & Token Registry:
- **DOMAIN (Level 1)**:
  - `FILTER_` : Screener & market selection (both live and standard).
  - `BOT_` : Execution engine & lifecycle (shared identically by Live and Shadow mode).
- **SCOPE (Level 2)**:
  - For Screener: `LIVE_` (in-play), `STD_` (pre-match/macro), `COMMON_` (shared screener rules).
  - For Bot Engine: `QUOTE_` (Phase 1 Entry/Pricing), `POS_` (Phase 2 Position/Inventory/Merge), `RISK_` (Phase 3 Stops/Hedges/Requotes/Cadence).
- **CATEGORY (Level 3)**:
  - `GATE_` : Boolean toggles and switches (MUST end in `_ON`).
  - `VOL_` : Volume and book depth metrics.
  - `VEL_` : Activity, turnover, and tape speed metrics.
  - `MOVE_` : Price movement, range, dispersion, volatility metrics (use `MOVE`, not `DISP`).
  - `SIZE_` : Order sizing, risk fractions, capital caps.
  - `PRICE_` : Price limits, entry ceilings, price bands.
  - `INV_` : Inventory management, skew, parity targets.
  - `STOP_` : Stop-loss and drawdown exit policies.
  - `REQUOTE_`: Quote lifecycle, dead-bands, and queue priorities.
  - `SYNC_` / `LOOP_`: Scheduling, loops, polling cadences.
- **SUFFIXES (Level 4 - Mandatory)**:
  - `_USD`, `_CENTS`, `_PCT`, `_RATIO`, `_SEC`, `_MIN`, `_DAYS`, `_SHARES`, `_ON`, `_MODE`.

---

## 2. Mandatory Bug Fixes & Audit Resolutions During Migration

When refactoring, correct the following detected discrepancies:
1. **Time Window Conversions**:
   - `FILTER_LIVE_VEL_LOOKBACK_SEC`: Resolve "1800s (5m)". If the objective is 5 minutes, enforce `300`. If 30 minutes was intended, set `1800`. Align comments to match code seconds.
   - `FILTER_LIVE_MOVE_LOOKBACK_SEC`: Resolve "7200s (15m)". If the objective is 15 minutes, enforce `900`. If 2 hours was intended, set `7200`. Align comments to match code seconds.
2. **Filter Threshold Alignments**:
   - `FILTER_LIVE_VEL_MIN_ACTIVITY_RATIO`: Fix 0.00% vs 2% trigger discrepancy. Document whether this gate is active or disabled in default configurations.
   - `FILTER_LIVE_MOVE_MIN_RANGE_CENTS`: Sync the parameter value (2.0¢) and trigger condition comment (1.0¢).
3. **Discretization & Tick Compatibility**:
   - Ensure `BOT_POS_INV_MIN_EDGE_OFFSET_CENTS` (currently 0.5¢ / $0.005) does not violate Polymarket's 1¢ tick grid (`BOT_QUOTE_VENUE_TICK_CENTS`). Ensure rounding / floor logic snaps quotes to valid venue ticks.
4. **Namespace Disambiguation**:
   - Separate the derived picking spread cap (`FILTER_COMMON_SPREAD_MAX_CENTS` = 2.05¢) from the execution book spread gate (`BOT_QUOTE_BOOK_MAX_SPREAD_CENTS` = 6¢).

---

## 3. Complete Variable Migration Map (59 Variables)

Map all legacy variables to the new canonical names across the codebase:

### SCOPE 1: MARKET SCREENER (`FILTER_`)
- `select_min_volume_24h_usd_live` -> `FILTER_LIVE_VOL_MIN_24H_USD`
- `select_velocity_gate_enabled`    -> `FILTER_LIVE_GATE_VELOCITY_ON`
- `select_movement_window_sec`       -> `FILTER_LIVE_VEL_LOOKBACK_SEC`
- `select_min_movement_usd`          -> `FILTER_LIVE_VEL_MIN_NOTIONAL_USD`
- `select_min_activity_ratio`        -> `FILTER_LIVE_VEL_MIN_ACTIVITY_RATIO`
- `select_volatility_window_sec`     -> `FILTER_LIVE_MOVE_LOOKBACK_SEC`
- `select_min_range_cents`           -> `FILTER_LIVE_MOVE_MIN_RANGE_CENTS`
- `select_min_volume_24h_usd`        -> `FILTER_STD_VOL_MIN_24H_USD`
- `select_min_top3_depth_usd`        -> `FILTER_COMMON_VOL_MIN_TOP3_DEPTH_USD`
- `select_max_book_spread`           -> `FILTER_COMMON_SPREAD_MAX_CENTS`
- `select_max_days_to_resolve`       -> `FILTER_COMMON_HORIZON_MAX_DAYS`
- `select_max_queue_minutes`         -> `FILTER_COMMON_QUEUE_MAX_WAIT_MIN`
- `enforce_max_queue_minutes`        -> `FILTER_COMMON_GATE_QUEUE_BAR_ON`

### SCOPE 2: BOT ENGINE - PHASE 1: QUOTING & SIZING (`BOT_QUOTE_`)
- `bankroll_usd`                     -> `BOT_QUOTE_BANK_BASELINE_USD`
- `max_total_usd`                    -> `BOT_QUOTE_BANK_MAX_TOTAL_USD`
- `bankroll_ceiling_pct`             -> `BOT_QUOTE_BANK_CEILING_PCT`
- `order_risk_pct`                   -> `BOT_QUOTE_SIZE_ORDER_MAX_PCT`
- `max_order_usd`                    -> `BOT_QUOTE_SIZE_ORDER_MAX_USD`
- `couple_risk_pct`                  -> `BOT_QUOTE_SIZE_COUPLE_RISK_PCT`
- `min_couple_usd`                   -> `BOT_QUOTE_SIZE_COUPLE_MIN_USD`
- `naked_risk_pct`                   -> `BOT_QUOTE_SIZE_NAKED_RISK_PCT`
- `max_naked_usd`                    -> `BOT_QUOTE_SIZE_NAKED_MAX_USD`
- `min_quote_shares`                 -> `BOT_QUOTE_VENUE_MIN_SHARES`
- `tick_size`                        -> `BOT_QUOTE_VENUE_TICK_CENTS`
- `dynamic_offset_enabled`           -> `BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON`
- `dynamic_offset_multiplier`        -> `BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER`
- `dynamic_offset_min_cents`         -> `BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS`
- `dynamic_offset_max_cents`         -> `BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS`
- `max_book_spread`                  -> `BOT_QUOTE_BOOK_MAX_SPREAD_CENTS`
- `max_spread_from_mid`              -> `BOT_QUOTE_BOOK_MAX_MID_OFFSET_CENTS`
- `require_two_sided_when_flat`      -> `BOT_QUOTE_GATE_TWO_SIDED_FLAT_ON`
- `enforce_price_band`               -> `BOT_QUOTE_GATE_PRICE_BAND_ON`
- `price_band_low`                   -> `BOT_QUOTE_PRICE_BAND_MIN_CENTS`
- `price_band_high`                  -> `BOT_QUOTE_PRICE_BAND_MAX_CENTS`

### SCOPE 2: BOT ENGINE - PHASE 2: INVENTORY & MERGE (`BOT_POS_`)
- `strict_paired_inventory`          -> `BOT_POS_GATE_STRICT_PAIRS_ON`
- `target_balance`                   -> `BOT_POS_INV_TARGET_BALANCE_PCT`
- `max_skew_offset`                  -> `BOT_POS_INV_MAX_SKEW_OFFSET_CENTS`
- `min_reward_offset`                -> `BOT_POS_INV_MIN_EDGE_OFFSET_CENTS`
- `max_pair_cost`                    -> `BOT_POS_PRICE_MAX_ENTRY_COST_USD`
- `enforce_completable_pair_cost`    -> `BOT_POS_GATE_COMPLETABLE_COST_ON`
- `max_completable_pair_cost`        -> `BOT_POS_PRICE_MAX_COMPLETE_USD`
- `merge_max_loss_per_share`         -> `BOT_POS_MERGE_MAX_LOSS_CENTS`
- `merge_velocity_hold_days`         -> `BOT_POS_MERGE_MAX_HOLD_DAYS`

### SCOPE 2: BOT ENGINE - PHASE 3: RISK, STOPS & CADENCE (`BOT_RISK_`)
- `emergency_drawdown_pct`           -> `BOT_RISK_CIRCUIT_MAX_DRAWDOWN_PCT`
- `stop_loss_mode`                   -> `BOT_RISK_STOP_MODE`
- `drawdown_pct`                     -> `BOT_RISK_STOP_MAX_DRAWDOWN_PCT`
- `drop_cents`                       -> `BOT_RISK_STOP_ADVERSE_DROP_CENTS`
- `enable_emergency_hedge`           -> `BOT_RISK_GATE_EMERGENCY_HEDGE_ON`
- `emergency_hedge_pct`              -> `BOT_RISK_EMERGENCY_TRIGGER_PCT`
- `enable_pairs_rule`                -> `BOT_RISK_GATE_PAIRS_RULE_ON`
- `pairs_exit_window_sec`            -> `BOT_RISK_PAIRS_ACTION_WINDOW_SEC`
- `enable_aged_out_rescue`           -> `BOT_RISK_GATE_AGED_RESCUE_ON`
- `aged_out_rescue_lead_sec`         -> `BOT_RISK_AGED_RESCUE_LEAD_SEC`
- `requote_dead_band`                -> `BOT_RISK_REQUOTE_DEAD_BAND_CENTS`
- `requote_hold_below_target`        -> `BOT_RISK_REQUOTE_HOLD_BELOW_CENTS`
- `requote_hold_queue_shares`        -> `BOT_RISK_REQUOTE_MIN_QUEUE_SHARES`
- `requote_interval_sec`             -> `BOT_RISK_REQUOTE_MIN_INTERVAL_SEC`
- `poll_interval_sec`                -> `BOT_RISK_LOOP_POLL_INTERVAL_SEC`
- `sweep_interval`                   -> `BOT_RISK_SYNC_SWEEP_INTERVAL_SEC`

---

## 4. Execution Directives
1. **Backward Compatibility**: If an `.env` or legacy config loader is used, support deprecation fallbacks: read `NEW_VAR_NAME` first; if absent, fall back to `OLD_VAR_NAME` and log a deprecation warning.
2. **Configuration Typing**: Parse booleans uniformly (`true`/`false`, `1`/`0`, `ON`/`OFF`).
3. **No Phantom Renames**: Update all unit tests, schemas, serialization dictionaries, and mock fixtures to match these exact identifiers.