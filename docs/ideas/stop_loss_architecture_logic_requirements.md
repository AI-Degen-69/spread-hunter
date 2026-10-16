# Polymarket Bot: Stop-Loss & Emergency Floor Architecture Specification

## 1. Background & Execution Context

Polymarket does not support native stop-loss orders on-chain or via its Central Limit Order Book (CLOB). 

* **No Resting Exit Orders:** Posting a limit sell order below the current market price will cross the spread and execute immediately as a taker order against resting bids.
* **Client-Side Triggering:** The bot must monitor order book updates client-side. When an exit condition is met, it fires an immediate, aggressive sell order (IOC / FOK market order) to liquidate the position.

---

## 2. Dynamic Stop-Loss Modes (Strategy-Level)

The dynamic stop loss monitors active positions and triggers an exit based on drawdown from the position's volume-weighted average entry price (`avg_entry_price`).

The engine supports three mutually exclusive operational modes:

* `NONE` (Default): Dynamic trading stop loss is disabled. The position is protected exclusively by the universal emergency floor.
* `ABSOLUTE_DROP`: Triggers after a fixed cent drop from entry price.
* `PERCENTAGE_DROP`: Triggers after a specified percentage drawdown from entry price.

> **Rule:** `ABSOLUTE_DROP` and `PERCENTAGE_DROP` cannot be active at the same time. The user selects either one or neither.

### Trigger Formulas

* **Mode A: Absolute Drop (`stop_loss_mode = "ABSOLUTE"`)**
  * Parameter: `drop_cents` (Range: $0.01 to $0.90)
  * Formula:
    $$\text{strategy\_trigger\_price} = \text{avg\_entry\_price} - \text{drop\_cents}$$
  * *Example:* Entry at $0.50 with `drop_cents = 0.05` $\rightarrow$ Trigger at **$0.45**.
  * *Example:* Entry at $0.12 with `drop_cents = 0.03` $\rightarrow$ Trigger at **$0.09**.

* **Mode B: Percentage Drawdown (`stop_loss_mode = "PERCENTAGE"`)**
  * Parameter: `drawdown_pct` (e.g., 10%, 20%)
  * Formula:
    $$\text{strategy\_trigger\_price} = \text{avg\_entry\_price} \times \left(1 - \frac{\text{drawdown\_pct}}{100}\right)$$
  * *Example:* Entry at $0.50 with `drawdown_pct = 20%` $\rightarrow$ Trigger at **$0.40**.
  * *Example:* Entry at $0.12 with `drawdown_pct = 25%` $\rightarrow$ Trigger at **$0.09**.

---

## 3. Universal Failsafe: Relative Emergency Floor (`emergency_drawdown_pct`)

Because active quotes span between **$0.10 and $0.90**, a static hard stop (such as $0.15) cannot be used; it would instantly liquidate positions entered at $0.10–$0.14 upon fill.

To provide a guaranteed catastrophe backstop without interfering with lower-range quotes, the bot uses a **relative percentage floor** calculated directly from entry price:

* **Parameter:** `emergency_drawdown_pct`
* **Default Value:** `75.0%` (user-modifiable)
* **Formula:**
  $$\text{emergency\_floor\_price} = \text{avg\_entry\_price} \times \left(1 - \frac{\text{emergency\_drawdown\_pct}}{100}\right)$$
* **Absolute Lower Bound:**
  $$\text{effective\_emergency\_floor} = \max(\text{emergency\_floor\_price},\, 0.01)$$

### Coverage Behavior Across Quote Ranges (at 75% Drawdown)

| Entry Price | 75% Drawdown Amount | Emergency Trigger Floor |
| :--- | :--- | :--- |
| **$0.80** | $0.60 | **$0.20** |
| **$0.50** | $0.375 | **$0.125** (~$0.12) |
| **$0.20** | $0.15 | **$0.05** |
| **$0.12** | $0.09 | **$0.03** |
| **$0.10** | $0.075 | **$0.025** (~$0.02 - $0.03) |

---

## 4. Trigger Hierarchy & Evaluation Logic

On every incoming order book tick or best bid price update, the bot evaluates the effective trigger level:

1. **When a dynamic stop loss is active (`ABSOLUTE` or `PERCENTAGE`):**
   $$\text{active\_exit\_trigger} = \max(\text{strategy\_trigger\_price},\, \text{effective\_emergency\_floor})$$
2. **When dynamic stop loss is disabled (`stop_loss_mode = "NONE"`):**
   $$\text{active\_exit\_trigger} = \text{effective\_emergency\_floor}$$

---

## 5. Execution Rules

* **Evaluation Condition:**
  $$\text{Current Best Bid} \le \text{active\_exit\_trigger}$$
* **Order Type:** Immediate-Or-Cancel (IOC) or Fill-Or-Kill (FOK) aggressive taker sell.
* **Size:** 100% of the active token position.
* **Order Book Safety:** Under no circumstances should limit sell orders be placed ahead of time below the market.