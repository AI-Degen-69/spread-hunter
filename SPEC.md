# SPEC: Single-Leg Trailing Peak Momentum Rider and Entry Price Ceiling

## Overview & Goals
In the Polymarket spread hunter strategy on live sports, pair-merging caps upside at 3%-6% ($1.00 pair cap) while unhedged single-leg adverse moves can lose up to 75%. Furthermore, when a single leg fills (e.g., an underdog at 28¢) and the market moves strongly in its favor (up to 50¢ or 80¢), the engine currently retains the resting order on the opposite losing side, capping gain at ~6¢ if filled or holding through the peak and dumping for a loss if unfilled. Historical tournament tick data demonstrates that 86.4% of single legs traded upward after fill, and simulation of a 2¢ trailing peak stop turned a -$13.38 loss into +$12.03 in pure profit (+23.3% average gain, up to +75% max). Additionally, 100% of -$4.59 blowout losses occurred when buying heavy favorites (> $0.60).

This specification establishes:
1. **Entry Price Ceiling:** Enforce a maximum entry price (`bot_pos_price_max_entry_usd: float = 0.60`) to prevent quoting or buying heavy favorites (> $0.60).
2. **Trailing Peak Momentum Rider:** When a single leg fills and trades upward (`best_bid > entry_price`), cancel the resting opposite leg to let the winner ride uncapped. Track rolling peak watermark (`peak_bid = max(peak_bid, best_bid)`), and when `best_bid <= peak_bid - delta`, trigger an immediate taker market sell (`exit_single_buy` with reason `trailing_rider_exit`).
3. **Execution & Simulation Support:** Wire trailing rider logic into `core_brain/single_leg_lifecycle.py`, `core_brain/single_buy_saver.py`, `core_brain/trader_loop.py`, and `core_brain/shadow_run.py`.

---

## Acceptance Criteria
- [ ] **Knob Configuration:**
  - `bot_risk_trailing_rider_on: bool = False` (ENV: `BOT_RISK_TRAILING_RIDER_ON`)
  - `bot_risk_trailing_rider_delta_usd: float = 0.02` (ENV: `BOT_RISK_TRAILING_RIDER_DELTA_USD`)
  - `bot_pos_price_max_entry_usd: float = 0.60` (ENV: `BOT_POS_PRICE_MAX_ENTRY_USD`)
  - Parsed cleanly and validated in `core_brain/config.py:MakerConfig.from_env()`.
- [ ] **Quote Price Ceiling:**
  - Maker buy quotes with `price > bot_pos_price_max_entry_usd` are blocked in `core_brain/quotes.py` (`_decide_quotes_from_mid`).
  - When flat, a pair with one side > 0.60 is completely blocked from quoting by `_require_two_sided`.
- [ ] **Trailing Peak Rider Lifecycle:**
  - `LegState.TRAILING_RIDER` added to `core_brain/single_leg_lifecycle.py`.
  - When a single leg fill exists and `best_bid > entry_price` (with `bot_risk_trailing_rider_on=True`), state transitions to `TRAILING_RIDER`.
  - Resting opposite limit orders are cancelled to prevent capping upside.
  - Rolling peak watermark `peak_bid` is tracked and saved to `single_leg_lifecycle.evidence_json`.
  - When `best_bid <= peak_bid - delta`, an immediate taker exit is dispatched with reason `trailing_rider_exit`.
- [ ] **Testing & Verification:**
  - Unit tests covering configuration parsing, quote ceiling filtering, lifecycle state transitions, peak tracking, and exit dispatch pass.
  - Standalone shadow proof-of-concept run executes cleanly with trailing rider enabled.

---

## Edge Cases
1. **Bid Drops Immediately Without Upward Move:**
   - If `best_bid <= entry_price` from the start, trailing rider does not activate; normal `PATIENT_WAIT`, `ESCALATED_HEDGE`, or `HARD_STOP` rules apply.
2. **Missing or Corrupt Bid Reads (`held_bid is None` or non-finite):**
   - Unknown bid values must never trigger an exit or advance watermark; position remains in current state without phantom actions.
3. **Peak Watermark Persistence Across Cycles:**
   - Watermark advances (even within the same `TRAILING_RIDER` state) must persist to `single_leg_lifecycle` table in SQLite so process restarts or subsequent poll cycles retain the highest recorded watermark.
4. **Opposite Leg Race Condition:**
   - If opposite leg filled before cancellation completes, both legs are held and exposure is balanced; position transitions to standard pair merge path (`PAIR_LOCKED`).

---

## Explicitly Out of Scope
- Modifying live exchange order types (Polymarket CLOB lacks native trailing stop orders; this is an engine-monitored trigger).
- Changing non-sports market rules or removing standard pair merge paths when both legs fill simultaneously.
