# Spread Hunter

Paper-trading simulation of a **maker** strategy on Polymarket, run as a
**fleet**: `scripts/rank_markets.py` scores the venue's funded **reward
markets** (liquidity-reward rent for resting size) alongside liquid unfunded
**spread markets** (income from the spread on fills), and the **allocator**
sizes the winners on a water-fill over each market's projected income. The
maker mechanism itself rests bids on **both** outcomes (YES/NO) of each market,
aiming to earn the spread, claim liquidity rewards, and stay inventory-balanced,
holding to resolution or voluntary merge.

> **Simulation only:** Spread Hunter never places real orders and loads no wallet
> credentials — see [AGENTS.md](AGENTS.md).

---

## Dashboards

- **Canonical Dashboard (`:8800`):** [http://localhost:8800](http://localhost:8800)  
  Served by `server/spread_dash.py`. Provides real-time telemetry, capital curve, interactive market drawer, risk utilization gauges, markout drift distributions, split-flap verdict signals, and statistical confidence tracking.
- **Market Scan Funnel (`:8801`):** [http://localhost:8801/?view=scan](http://localhost:8801/?view=scan)  
  Served by `server/fleet_dash.py` (accessible from the header of the main dashboard). Displays the multi-stage market selection funnel (`RAW → FILTERS → FINAL → GRADUATED`) and near-miss trackers.

---

## Strategy Thesis & Core Controls

1. **Zero Maker Fees & Spread Capture:**  
   Resting limit orders on both outcomes captures spread and maker rebates without incurring taker fees.
2. **Strict Pair-Cost Discipline:**  
   Every pair acquisition is constrained by a hard ceiling (`max_pair_cost <= 0.995`). Because resolution pays exactly $1.00, hedged pairs have a guaranteed positive payoff.
3. **Queue-Aware Fill Modeling:**  
   Simulation fills are driven by observed order-book deltas and queue depth precedence rather than naive trade tape assumptions (documented in [`strategy/fills.py`](strategy/fills.py)).
4. **Dollar-Denominated Risk Controls:**  
   Real-time risk gates enforce `max_naked_usd` limits to bound unhedged inventory exposure.
5. **Pairs-Only EV & Defensive Exits:**  
   One-sided fills evaluate completion EV dynamically. Uncompletable pairs trigger an immediate defensive exit to protect against adverse selection.

---

## Layout

```text
strategy/   Core engine: fleet sweep, allocator, quoting, queue-aware fills, risk gates, store & stats
server/     spread_dash.py (canonical dashboard, :8800), fleet_dash.py (market scan funnel, :8801)
scripts/    Market ranker, pairs EV report, bankroll sensitivity, replay harnesses, process supervisors
research/   Lab notebook & experiment logs (RESEARCH_LOG.md, RESEARCH_SUMMARY.md in EN & HE)
docs/       Architecture records, data flow specs, markout horizons, and CSS documentation
```
---

## Quickstart & Local Execution

### Setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
bash scripts/setup-hooks.sh        # Required once: research-log enforcement hook
```

### Running the Fleet

```powershell
.\scripts\fleet-start.ps1           # Supervised paper fleet + BOTH dashboards
.\scripts\fleet-start.ps1 -FreshRun # Archive DB and start a fresh sample
```

### Standalone Dashboard & Analysis Tools

```bash
# Dashboards (standalone development):
.venv/bin/uvicorn server.spread_dash:app --port 8800  # Canonical dashboard
.venv/bin/uvicorn server.fleet_dash:app --port 8801   # Market scan funnel (?view=scan)

# Strategy & Diagnostics:
.venv/bin/python -m scripts.rank_markets              # Score and rank candidate markets
.venv/bin/python -m scripts.pairs_ev_report           # Pairs-rule EV & execution report
.venv/bin/python -m scripts.bankroll_stats_report     # 10-tier bankroll sensitivity & CI report
.venv/bin/python -m scripts.build_tailwind_css        # Regenerate static dashboard CSS
```

---

## Statistical Readiness & Research

Go-live readiness is evaluated via statistical confidence thresholds tracked on the dashboard:

- **`READY_FOR_SMALL_LIVE_PILOT` Requirements:**
  - Settled markets: $n \ge 100$
  - Positive 90% confidence interval lower bound
  - Minimum 14 calendar days of continuous operation
  - Maximum single-category concentration $\le 50\%$

For detailed experiment logs, historical test results, and empirical findings, refer to [`research/RESEARCH_LOG.md`](research/RESEARCH_LOG.md) and [`PROGRAM.md`](PROGRAM.md).
