# SPEC — #453: Inventory and order exposure guardian to reconcile unhedged positions and resting legs

Scope note: this file covers issue #453 (branch `i453/inventory-and-order-exposure-guardian`).
It supersedes the #448 spec.

## Problem (operator words)

In a live / shadow market, when one leg of a pair fills while the opposing hedge order is resting, a subsequent price move or lifecycle replace can cancel the resting order into "thin air" without immediately placing a replacement. This leaves the held position completely naked with zero active orders on the book, exposing the account to directional sports/market outcome risk for extended periods (e.g., 75+ minutes in issue #453's shadow run). Furthermore, `stray_guard` and `single_buy_saver` operate in silos: `stray_guard` does not evaluate held inventory, risking treating a legitimate waiting hedge as an orphaned stray; quantity imbalances (e.g., 10 YES vs 9 NO) are not automatically topped up; and aged-out unhedged legs drop out of active management rather than taking profitable taker crosses ($\le \$0.99$) or disciplined stop losses.

## Goals

1. **Unified Net Exposure Evaluation:**
   - Define pure exposure calculation function in `core_brain/single_leg_lifecycle.py` evaluating `(held_shares + working_order_shares)` per token for a condition.
   - Categorize status into `balanced`, `covered`, `under_covered`, or `stranded`.
   - Calculate held deficit, non-negative coverage shortfall, and over-coverage using `SIZE_EPS`.
   - Provide an exposure loader in `core_brain/single_buy_saver.py` using `inventory_from_registry` and venue confirmed open orders.

2. **Stray Guard Hedge Protection:**
   - In `core_brain/stray_guard.py`, integrate exposure calculation before classifying orders.
   - Add `protected_order_ids` to `classify_market_orders`. Never mark a working hedge for an existing held leg as hopeless or stray.

3. **Guardian Unhedged Position Resolution & Completion:**
   - In `core_brain/single_buy_saver.py`, when a held leg is stranded or under-covered below `min_quote_shares`:
     - If `stranded_completion_enabled` is active and `held_avg + opposing_ask < max_pair_cost`, execute a taker completion, persist pair linkage, and trigger guarded merge.
     - If maker spread permits and age is within `stranded_max_wait_sec`, allow maker hedge quoting (`stranded_awaiting_maker`).
     - Otherwise, trigger disciplined stop-loss exit via `exit_single_buy(force=True)`.

4. **Hedge-Preserving Replacement in Trader Loop:**
   - In `core_brain/trader_loop.py:_visit_one`, do not cancel an active hedge under `lifecycle_replace` if no replacement intent survives placement admission filters.
   - Re-check resting state after cancellation and re-verify shortfall.
   - If replacement submission fails or is rejected, attempt to restore the original hedge.

5. **Delta Top-Up Maker Intents:**
   - In `core_brain/trader_loop.py:plan_orders`, when a preserved hedge leaves an under-covered shortfall $\ge min\_quote\_shares$, generate a supplemental maker intent for the missing delta shares on the same token and pair ID.

## Acceptance Criteria

- [ ] Pure exposure function correctly identifies heavy/light tokens, deficit, and state (`balanced`, `covered`, `under_covered`, `stranded`) across edge cases.
- [ ] `stray_guard` never cancels a resting hedge that covers an inventory deficit.
- [ ] If opposing ask allows combined cost strictly below `max_pair_cost`, completion is attempted when enabled.
- [ ] Quantity mismatches $\ge min\_quote\_shares$ generate supplemental delta maker intents.
- [ ] Replacement cancellations are aborted if no valid replacement order survives gating, preventing cancellation into thin air.
- [ ] Targeted tests pass:
  - `tests/test_single_leg_lifecycle.py`
  - `tests/test_single_buy_saver.py`
  - `tests/test_stray_guard.py`
  - `tests/test_trader_loop.py`
  - `tests/test_plan_orders_asymmetric_hold.py`

## Explicit Out of Scope

- Directional speculative positioning.
- Modifying underlying CLOB API transport or SDK primitives.
- Modifying `core_brain/risk.py` or risk cap formulas.
- Adding maker hedges for markets outside the Trader universe.
