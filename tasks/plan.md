# Implementation Plan — #654

Branch: migrate_canonical_config_names | Issue: #654

Migrate trading lifecycle and engine consumers off legacy `MakerConfig` attribute names onto canonical `bot_*` fields implemented in `core_brain/config.py`.

## Intake Note & Open Questions Resolution
- CodeRabbit Plan Intake: Adopted 2-phase layout (Phase 1: Quotes, Position/Merge, Risk Gate; Phase 2: Lifecycle Emergency Drawdown & Requote Dead Band). Adopted inline warning guard pattern filtering by legacy name and engine source file.
- Stale proposal-doc names vs implemented names: Implemented names in `core_brain/config.py` win (`bot_pos_skew_target_balance_ratio`, `bot_pos_price_max_pair_cost_usd`, `bot_pos_gate_complete_cost_on`, `bot_pos_price_max_complete_usd`, `bot_order_requote_dead_band_cents`, `bot_risk_circuit_max_drawdown_pct`).
- `core_brain/pair_scanner.py`: Verified from code; only reads `fee_rate` (outside ticket scope) and has no scoped config reads. Left untouched.
- Venue tick snapping: Existing whole-cent price floor in `quote_resting_price` is maintained without changing pricing arithmetic (`max(offset, tick)` would alter output prices, violating zero math changes). Conflict reported to operator for separate consideration.

## Quality Guardrails
- Constraints locked in `CONSTRAINTS.md`.
- Baseline recorded: 597 passed across 15 suites (299 passed on 4 core suites).
- Anti-cheat: No warning suppression helpers; warning checks inspect specific legacy attribute warnings emitted from engine files.

## Field Translation Table
| Legacy Name | Canonical Name | Target File(s) | Fallback / Context |
|---|---|---|---|
| `dynamic_offset_enabled` | `bot_quote_gate_dynamic_offset_on` | `core_brain/quotes.py` | `False` |
| `dynamic_offset_multiplier` | `bot_quote_spread_offset_multiplier` | `core_brain/quotes.py` | `0.50` |
| `dynamic_offset_min_cents` | `bot_quote_spread_offset_min_cents` | `core_brain/quotes.py` | `2` |
| `dynamic_offset_max_cents` | `bot_quote_spread_offset_max_cents` | `core_brain/quotes.py` | `4` |
| `dynamic_offset_max_age_sec` | `bot_quote_spread_offset_max_age_sec` | `core_brain/quotes.py` | `900.0` |
| `range_cents` | `bot_quote_telemetry_range_cents` | `core_brain/quotes.py` | `None` |
| `velocity_measured_at` | `bot_quote_telemetry_velocity_measured_at` | `core_brain/quotes.py` | `None` |
| `reward_offset` | `bot_quote_spread_static_offset_cents` | `core_brain/quotes.py` | direct read |
| `enforce_price_band` | `bot_quote_gate_price_band_on` | `core_brain/quotes.py`, `core_brain/risk.py` | direct read |
| `min_reward_offset` | `bot_pos_skew_min_reward_offset_cents` | `core_brain/quotes.py` | direct read |
| `strict_paired_inventory` | `bot_pos_gate_strict_pairs_on` | `core_brain/quotes.py` | `True` |
| `max_pair_cost` | `bot_pos_price_max_pair_cost_usd` | `core_brain/quotes.py`, `core_brain/order_manager.py`, `core_brain/risk.py`, `core_brain/trader_loop.py` | reads migrated; outgoing kwargs preserved |
| `target_balance` | `bot_pos_skew_target_balance_ratio` | `core_brain/quotes.py` | direct read |
| `enable_emergency_hedge` | `bot_risk_gate_emergency_hedge_on` | `core_brain/quotes.py` | direct read |
| `enforce_completable_pair_cost` | `bot_pos_gate_complete_cost_on` | `core_brain/risk.py` | `True` |
| `max_completable_pair_cost` | `bot_pos_price_max_complete_usd` | `core_brain/risk.py` | `0.0` |
| `emergency_drawdown_pct` | `bot_risk_circuit_max_drawdown_pct` | `core_brain/single_leg_lifecycle.py`, `core_brain/trader_loop.py`, `core_brain/single_buy_saver.py`, `scripts/*.py` | parameter rename across call chain |
| `requote_dead_band` | `bot_order_requote_dead_band_cents` | `core_brain/trader_loop.py` | `0.0`, preserves stored dollar unit |

---

## Tasks

### [Backend/Logic] [x] Task 1.1: Quoting config reads migration in `core_brain/quotes.py`
- **Depends on:** None
- **Size:** S
- **Files:** `core_brain/quotes.py`, `tests/test_dynamic_offset.py`, `tests/test_quotes.py`, `tests/test_legacy_ask_quotes.py`
- **Details:**
  - In `core_brain/quotes.py`, migrate attribute reads and `getattr` strings:
    - `_in_band`: `enforce_price_band` -> `bot_quote_gate_price_band_on`
    - `dynamic_offset_for`: `dynamic_offset_enabled`, `dynamic_offset_multiplier`, `dynamic_offset_min_cents`, `dynamic_offset_max_cents`, `dynamic_offset_max_age_sec`, `range_cents`, `velocity_measured_at`, `reward_offset` -> canonical equivalents
    - `_decide_quotes_from_mid`: `dynamic_offset_enabled` -> `bot_quote_gate_dynamic_offset_on`
    - `quote_resting_price`: `min_reward_offset` -> `bot_pos_skew_min_reward_offset_cents`
    - Inventory & hedge & pair-cost reads: `strict_paired_inventory`, `enable_emergency_hedge`, `max_pair_cost`, `target_balance` -> canonical equivalents
  - In `tests/test_dynamic_offset.py`: update setups to canonical keywords, update telemetry assertion, add `test_dynamic_offset_for_reads_canonical_names_without_warning`.
  - In `tests/test_quotes.py`: add `test_in_band_reads_canonical_gate` and `test_canonical_pair_cap_refuses_from_mid_pair_without_warning`.
  - In `tests/test_legacy_ask_quotes.py`: add `test_legacy_ask_branch_reads_canonical_target_balance`.
- **Verification:** `python -m pytest tests/test_quotes.py tests/test_dynamic_offset.py tests/test_legacy_ask_quotes.py -q`

### [Backend/Logic] [x] Task 1.2: Position, pair-cost & completable gate migration
- **Depends on:** Task 1.1
- **Size:** S
- **Files:** `core_brain/order_manager.py`, `core_brain/risk.py`, `core_brain/trader_loop.py`, `tests/test_completable_pair_gate.py`, `tests/test_trader_loop.py`
- **Details:**
  - In `core_brain/order_manager.py`: migrate 4 `max_pair_cost` reads to `bot_pos_price_max_pair_cost_usd` (keep outgoing `max_pair_cost=` kwargs).
  - In `core_brain/risk.py`:
    - `completable_pair_block`: `enforce_completable_pair_cost` -> `bot_pos_gate_complete_cost_on`, `max_completable_pair_cost` -> `bot_pos_price_max_complete_usd`.
    - `hard_block`: `max_pair_cost` -> `bot_pos_price_max_pair_cost_usd`, `enforce_price_band` -> `bot_quote_gate_price_band_on`.
  - In `core_brain/trader_loop.py`:
    - `_lifecycle_quote_context`: read `bot_pos_price_max_pair_cost_usd`.
    - `_submit_intents`: `getattr(cfg, "bot_pos_price_max_pair_cost_usd", 0.995)`.
  - In `tests/test_completable_pair_gate.py`: update setups and add `test_canonical_gate_and_cap_without_warning`.
  - In `tests/test_trader_loop.py`: update setups using canonical keywords.
- **Verification:** `python -m pytest tests/test_completable_pair_gate.py tests/test_order_manager.py tests/test_trader_loop.py -q`

### [Backend/Logic] Task 2.1: Lifecycle emergency drawdown & requote dead band migration
- **Depends on:** Task 1.2
- **Size:** M
- **Files:** `core_brain/single_leg_lifecycle.py`, `core_brain/trader_loop.py`, `core_brain/single_buy_saver.py`, `scripts/stream_market_decisions.py`, `scripts/verify_loose_baseline_tape.py`, `tests/test_single_leg_lifecycle.py`, `tests/test_stop_loss_config.py`, `tests/test_dual_stop_loss.py`, `tests/test_aged_out_rescue.py`, `tests/test_single_buy_saver.py`
- **Details:**
  - In `core_brain/single_leg_lifecycle.py`: rename parameter to `bot_risk_circuit_max_drawdown_pct` throughout `_validated_stop_knobs`, `stop_trigger`, `transition`, and wrapper. Update validation error message.
  - In `core_brain/trader_loop.py`: pass `bot_risk_circuit_max_drawdown_pct=float(cfg.bot_risk_circuit_max_drawdown_pct)` in `_lifecycle_quote_context`. In `_visit_one`, read `getattr(cfg, "bot_order_requote_dead_band_cents", 0.0)`.
  - In `core_brain/single_buy_saver.py`: read `getattr(cfg, "bot_risk_circuit_max_drawdown_pct", 75.0)` and rename forwarded keyword.
  - In `scripts/stream_market_decisions.py` and `scripts/verify_loose_baseline_tape.py`: read `cfg.bot_risk_circuit_max_drawdown_pct`.
  - Update tests and add:
    - In `tests/test_single_leg_lifecycle.py`: `test_transition_accepts_canonical_drawdown_keyword` and `test_legacy_drawdown_keyword_is_rejected`.
    - In `tests/test_trader_loop.py`: `test_lifecycle_quote_context_passes_canonical_drawdown` and `test_visit_one_passes_canonical_dead_band`.
    - In `tests/test_single_buy_saver.py`: `test_saver_forwards_canonical_emergency_drawdown`.
    - In `tests/test_stop_loss_config.py`: `test_legacy_emergency_read_still_works`.
- **Verification:** `python -m pytest tests/test_single_leg_lifecycle.py tests/test_trader_loop.py tests/test_single_buy_saver.py tests/test_stop_loss_config.py tests/test_dual_stop_loss.py tests/test_aged_out_rescue.py -q`

### [Verification] Task 2.2: Full regression and baseline comparison check
- **Depends on:** Task 2.1
- **Size:** XS
- **Files:** None (read-only verification)
- **Details:**
  - Run target acceptance command:
    `python -m pytest tests/test_quotes.py tests/test_order_manager.py tests/test_single_leg_lifecycle.py tests/test_trader_loop.py -q`
  - Run full 15-suite regression command and verify counts match baseline + new tests with zero regressions.
  - Verify zero `DeprecationWarning` from engine modules for scoped attributes.
- **Verification:** Automated pytest run across target suites.
