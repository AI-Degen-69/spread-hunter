# RUNBOOK — Stage 4.5 Supervised Live Execution Cycle

Operational guide for the Owner during a single supervised live maker cycle on Polymarket.

---

## 0. Choosing the Market and the Size

Two constraints decide this before any command runs, and both were discovered the hard way in
the Milestone 2 dry run.

**The market must pay maker rewards.** `quote` refuses any market whose `rewards.rates` is
empty, at `live/engine/markets.py` via `require_rewards=True`. There is no CLI override. A BTC
5-minute market was tried first and refused for exactly this reason. Resting on an unrewarded
market earns nothing, which is why the guard exists -- do not route around it.

**The market must outlive the cycle.** The 5-minute BTC market resolved between two commands
issued 16 seconds apart during the dry run: `merge` read `resolved no`, `redeem` read
`resolved yes`. A market that can resolve mid-cycle turns a mechanics test into a settlement
race. Pick one with a horizon of hours, not minutes -- the measured traded universe is tennis,
baseball and esports, not crypto.

**Size is the venue's own per-market minimum, per leg. Not a share more.**

| Constraint | Value | Source |
| --- | --- | --- |
| Code floor | 1.0 shares | `live/engine/live_pairs.py:59` |
| Venue floor | market's `min_size` -- read it, never assume | `live/engine/markets.py:188` |
| Per-order ceiling | $25.00 | `MAX_ORDER_USD`, `live/engine/live_exec.py:97` |
| Total open ceiling | $100.00 | `MAX_TOTAL_USD` |

Among eligible markets, choose the one with the **lowest `min_size`**. Cheapest complete cycle
wins; better economics with a larger minimum is the wrong trade here, because this cycle is a
mechanics test and its PnL is not a result anyone is measuring.

Record before going live -- market, `min_size`, both leg prices, cost per leg, total pair cost,
and the worst-case loss if one leg fills and the other never completes. If the minimum breaches
either ceiling, pick the next-cheapest market. Never raise a ceiling to fit a market.

---

## 1. Pre-Flight Verification & Dashboard Startup

Run all commands from `live/` directory with `POLY_SIG_TYPE=3`.

```bash
cd live

# 1. Verify credentials and wallet balance
python -m engine.live_exec status
python -m engine.live_exec balance

# 2. Start the telemetry dashboard in a dedicated terminal
python -m dash.live_dash --port 8799
```
Open `http://localhost:8799` in your browser. Verify the dashboard header reports `DB: .../live/run/live.db` and telemetry status is green/idle.

---

## 2. Order Placement (Opening Command)

> **CRITICAL GATE: EXPLICIT OWNER APPROVAL REQUIRED**
>
> `quote` is the **ONLY** command in the entire cycle that opens capital exposure and can create a loss.
> Every subsequent closing command (`complete`, `exit`, `merge`, `redeem`, `cancel`) is pre-approved.
> **Do not run `quote ... --live` without explicit human Owner approval.**

```bash
# 1. Run dry-run quote first to verify prices, ticks, and condition parameters
python -m engine.live_exec quote <condition_id> --price <bid_price> --size <shares>

# 2. ONLY AFTER OWNER APPROVAL: Post live resting bids on the CLOB
python -m engine.live_exec quote <condition_id> --price <bid_price> --size <shares> --live
```

---

## 3. Telemetry & Reconciliation Monitoring

Start the live poll loop to sync order fills from the venue into `live/run/live.db`.

> **Not yet exercised.** Every other command in this runbook was run in dry form during
> the Milestone 2 verification. `poll` was not. Run it once against the chosen market
> with nothing resting, and confirm it starts, prints a cycle, and exits cleanly on
> Ctrl-C, before it is relied on to detect a real fill.

```bash
# In a separate terminal under live/:
python -m engine.live_exec poll --interval 5.0
```

---

## 4. Normal Settlement Flow (Both Legs Filled)

When both legs fill on the CLOB, the position is balanced:

```bash
# 1. Merge complete outcome share sets back to USDC collateral (Gasless via Relayer)
python -m engine.live_exec merge <condition_id> --amount <shares> --live

# 2. (Alternative if held to resolution) Redeem winning outcome tokens (Gasless via Relayer)
python -m engine.live_exec redeem <condition_id> --live
```

---

## 5. Abort & Emergency Sequences (One-Sided Fill / Adverse Selection)

If one leg fills while the complement leg remains unfilled:

### Scenario A: Attempt Cross Completion (Target combined cost < $1.00)
```bash
# Attempt to cross the opposing book to lock in complete pair
python -m engine.live_exec complete <pair_id> --live
```

### Scenario B: Stop-Loss Exit (Opposing book drifted / combined cost >= $1.00)
If `complete` refuses or adverse selection occurs, immediately execute the stop-loss exit:
```bash
# Cancels the resting leg and immediately sells filled shares back to the CLOB
python -m engine.live_exec exit <pair_id> --live
```

### Scenario C: Emergency Global Pull
If unexpected venue behavior, supervisor disconnect, or rapid market disruption occurs:
```bash
# Cancel all active orders for the specific market
python -m engine.live_exec cancel-market <condition_id> --live

# OR cancel ALL open orders across the entire account immediately
python -m engine.live_exec cancel-all --live
```

---

## 6. Dashboard Inspection Guide (:8799)

The Owner monitors `http://localhost:8799` throughout the cycle.

| Component | Healthy / Normal State | Unhealthy / Alert State | Action Required |
|---|---|---|---|
| **Hero Card** | `⏳ RESTING BIDS ON CLOB` (bids resting) or `⚖️ BALANCED POSITION` (both legs filled) | `⚠️ UNHEDGED NAKED LEG` (pulsing red/amber banner with timer) | If naked leg persists > 10s: trigger `complete <pair_id> --live` or `exit <pair_id> --live`. |
| **Telemetry Pill** | Green `POLL OK (<5s)` | Red `STALE (>30s)` or `OFFLINE` | Poll loop stalled. Restart `python -m engine.live_exec poll`. |
| **Reconcile Lock** | `Idle (no pass in flight)` or momentary `HELD` during sync | `HELD` by same process for > 30s | Stale lock. Investigate or clear lock row if process crashed. |
| **Capital Panel** | Resting / Filled notional matches intended order size (< $25.00 limit) | Total committed exceeds max budget limit ($100.00) | Abort immediately via `cancel-all --live`. |
| **Orders Table** | Status `open` / `filled` / `cancelled` | Status `⚠️ UNATTRIBUTED` | Fill detected without local order attribution. Inspect venue web UI. |

---

## 7. Immediate Stop Conditions

Halt execution and pull all quotes (`cancel-all --live`) if:
1. **Unhedged leg exceeds time threshold:** A single leg remains filled with no complement fill for > 30 seconds and `complete` fails to fill.
2. **Telemetry Staleness:** Dashboard or poll loop reports `STALE (>30s)` or venue Data API becomes unreachable.
3. **Unexpected fills or balance mismatch:** Venue balances deviate from `live.db` registry tracking by > $0.05.
4. **Adverse Price Movement:** Opposing ask rises beyond `max_pair_cost` threshold ($0.985), making pair completion unprofitable.
