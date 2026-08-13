# Bankroll Sensitivity Experimentation & Statistical Verification Design

**Date:** 2026-08-13  
**Status:** APPROVED DESIGN  
**Topic:** Concurrent 10-Tier Bankroll Experimentation Framework ($100 to $1,000)  
**Branch:** `feat/bankroll-sensitivity-framework`  

---

## 1. Executive Summary & Objective

The objective is to run a controlled, statistically rigorous simultaneous paper-trading experiment across 10 distinct bankroll tiers ($100, $200, $300, $400, $500, $600, $700, $800, $900, $1,000). Every instance will launch simultaneously on real Polymarket market feeds, write to isolated database stores, expose individual and aggregate telemetry, and evaluate statistical confidence intervals (95% and 98%) to determine the optimal capital allocation tier for live trading.

---

## 2. Quantitative Analyst Review & Risk Framework

### Statistical Estimation & Fat-Tailed Distribution Adjustment
- **Student's t-Distribution:** Financial percentage returns ($\Delta\%$) exhibit fat tails (excess kurtosis). Instead of assuming standard normal $z$-scores ($1.96$), confidence intervals use exact Student's $t$ critical values with $df = n - 1$ degrees of freedom:
  $$\text{CI}_{95\%} = \bar{x} \pm t_{n-1, 0.025} \cdot \frac{s}{\sqrt{n}}$$
  $$\text{CI}_{98\%} = \bar{x} \pm t_{n-1, 0.010} \cdot \frac{s}{\sqrt{n}}$$
- **Sharpe & Downside Sortino Ratios:**
  - **Sharpe Ratio ($S$):** Annualized mean return divided by total standard deviation $s$.
  - **Sortino Ratio ($S_{sortino}$):** Mean return divided by downside semi-deviation $\sigma_{down} = \sqrt{\frac{1}{n}\sum \min(0, x_i)^2}$.
- **Family-Wise Error Rate (FWER) Protection:** When evaluating 10 simultaneous hypothesis tests, Bonferroni significance threshold adjustment is applied to prevent false positive optimal recommendations ($\alpha_{adj} = 0.05 / 10 = 0.005$).

---

## 3. Architecture & Multi-Instance Isolation

To strictly comply with repo safety rules ("Concurrent bots writing one database sum their independent inventories into silently invalid data"), each bankroll instance operates in total filesystem isolation.

```
run/
├── bankroll_100/
│   ├── fleet.db
│   ├── fleet_state.json
│   └── status.json
├── bankroll_200/
│   ├── fleet.db
│   └── ...
...
└── bankroll_1000/
    ├── fleet.db
    └── ...
```

### Key Components

1. **Multi-Instance Orchestrator (`scripts/launch_bankroll_experiments.py`):**
   - Spawns 10 child processes simultaneously using `SPREAD_HUNTER_DB=run/bankroll_X/fleet.db`.
   - Tracks PIDs in `run/bankroll_experiments.pids.json`.

2. **Multi-Dashboard Grid UI (`server/spread_dash.py` / `server/spread_dash_html.py`):**
   - **Master Grid View (`?view=bankroll_matrix`):** Displays all 10 active dashboards simultaneously in a responsive grid.
   - **Card Elements per Tier:**
     - Bankroll Badge (`$100`..`$1,000`) & Status Pill (`RUNNING`, `COMPLETED`, `INVALIDATED`).
     - SVG Equity Curve Sparkline (live cumulative P&L trajectory).
     - Live 95% & 98% Confidence Interval range bars.
     - Sharpe Ratio & Sortino Ratio badges.
     - Win Rate % & Active Exposure ($).

---

## 4. Early Invalidation & Stopping Protocol

An experiment tier is automatically halted and flagged as **INVALIDATED** if any of the following statistical circuit breakers trigger:

1. **Upper-Bound Loss Failure (Statistically Significant Negative P&L):**
   - Condition: At $n \ge 30$, the **upper bound of the 95% Confidence Interval** falls below $0.0\%$ ($\bar{x} + t_{df, 0.025} \cdot SE < 0$).
2. **Win Rate Collapse:**
   - Condition: At $n \ge 25$, trade win rate drops below $35.0\%$.
3. **Catastrophic Drawdown / Inventory Skew:**
   - Condition: Cumulative drawdown exceeds $15.0\%$ of initial tier capital or unhedged toxic exposure breaches threshold for 3 consecutive sweeps.

---

## 5. Optimal Bankroll Decision Rule

The recommended bankroll for live trading is selected by picking the tier that maximizes the **Lower Bound of the 95% Confidence Interval of Annualized % ROI**:
$$\text{Optimal Tier} = \arg\max_k \left( \text{Lower Bound of 95\% CI for Tier } k \right)$$
subject to:
- Tier state is `COMPLETED` (not `INVALIDATED`).
- Sortino Ratio $\ge 1.5$.
- Inventory hedging balance $\ge +0.50$.
