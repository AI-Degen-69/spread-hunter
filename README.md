# polymarket-maker

Paper-trading simulation of a **maker** strategy on Polymarket's 5-minute
"Bitcoin Up or Down" market. Instead of crossing the spread it rests bids on
**both** outcomes, aiming to earn the spread and stay inventory-balanced, and
holds to resolution.

**Live dashboard:** https://polymarket-maker-production.up.railway.app

> Simulation only. It never places a real order and loads no wallet
> credentials at all — see [AGENTS.md](AGENTS.md).

## Why this exists

Measured from 56,768 of @powerwinner's fills: he wins only **41.4%** of markets
against a 56.1% breakeven, so he has no directional edge. His gross is
**+$39,884/week** — but **−$32,501** if charged a taker fee. The entire
difference is that he rests orders instead of crossing. That is the mechanism
this repo tests.

## The honest caveat

A maker simulation lives or dies on its fill model. Ours is **queue-aware**,
driven by observed order-book deltas, and its optimistic biases are documented
in [`strategy/fills.py`](strategy/fills.py) rather than hidden. Treat its
output as an **upper bound**. The dashboard shows live progress toward
90/95/99% statistical confidence so the sample size cannot be quietly ignored.

## Layout

    strategy/   engine (quotes, queue-aware fills, kpi, store, net_config)
    server/     dashboard.py (API) + kanban.py (page)
    research/   lab notebook, EN + HE
    deploy/     container entrypoint + preflight

The sibling repo [`polymarket-taker`](https://github.com/AI-Degen-69/polymarket-taker)
uses the same layout.

## Running locally

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
bash scripts/setup-hooks.sh        # required once: research-log enforcement
MAKER_DB=./maker.db .venv/bin/python -m strategy.main
.venv/bin/uvicorn server.dashboard:app --port 8788
```

## Current state — post-fix run too small to judge; the old verdict was about a fixed bug

Updated 2026-08-10. **The "conclusively losing" verdict below is historical, and
it measured a bug, not the strategy.** The fix has landed, been validated, and
the fleet has run clean since — but the post-fix sample is far too small to
prove anything yet. The finish line is the dashboard's **go-live readiness**
panel, not a date.

### What changed (2026-08-05/06)

The risk gates were restated in dollars (`max_naked_usd`, new
`strategy/risk.py`), and the price-band and pair-cost caps — previously dead
code in a legacy branch that never executed on the live path — were made
reachable (research Sessions 19–22). A bid at 0.52 against a held 0.49 average
is now refused at a $1.01 pair against the $0.995 cap: the exact shape that
bought 14 pairs at $1.0200 on an instrument paying $1.00. The gates were
validated by replay against the recorded pre-fix database (Session 25): 15 of
67 fills refused (22.4%), avoiding **$729.88** of incremental naked cost against
**$26.19** of realized P&L forgone.

The pre-fix fleet's real performance was also confirmed negative — not the rosy
closes-only view (+16.5% mean, all voluntary merges), but resolution-implied
P&L on the archived sample: **−16.0% mean, 90% CI lower bound −51.5%** (Session
12). That is the bug's footprint, and it is closed.

### The live run (as of 2026-08-10)

The database was archived and the fleet restarted clean on 2026-08-06 (Session
12); the current process has been sweeping since 2026-08-09. The post-fix
sample is **28 fills across 13 markets** (7 closes, 13 resolution rows) — below
the 30-settled directional floor, let alone the 100 needed even to consider a
small live pilot. It is not growing right now either: today's ranker funnel
scored 200 markets and admitted **0** (120 depth rejects, 51 volume rejects,
against ≥$250k/24h volume, ≥$1k depth, ≤0.06 spread, ≤30d horizon), so the
fleet is running but idle. The public dashboard below is unreachable (404 as of
2026-08-10); a local instance serves on :8800.

### The finish line

The dashboard's **go-live readiness** panel is the repo's own definition of
done: `READY_FOR_SMALL_LIVE_PILOT` requires n_settled ≥ 100, a positive 90% CI
lower bound, ≥ 14 calendar days of coverage, and no single category over 50% of
the sample. The 53-market "statistically conclusive" verdict proved the bug
loses money; it says nothing about whether the fixed strategy works. Watch that
panel — and this section being rewritten again with real numbers.

### Historical baseline — the pre-fix bug run (2026-07-22)

| metric | value |
|---|---|
| settled markets | 53 (3W / 50L) |
| win rate | 5.7% |
| realized P&L | **−$1,172.07** (equity $3,827.93 from $5,000) |
| ROI on capital | −4.1% |
| median pair cost | **1.0419** (instrument pays $1.00) |
| pairs under $1.00 | **4%** |
| spread capture | +$263.81 |
| adverse selection | −$1,435.88 |
| fill rate | 37.6% (median 72 shares queued ahead) |
| avg edge vs mid | 0.48¢ (theory: 0.50¢) |

Execution was fine in that run — fill rate against real queue depth, 0.48¢
captured versus a 0.50¢ theoretical half-spread, inventory balance 0.99. The
loss was one thing: **median pair cost 1.0419 for a payout of exactly $1.00** —
a guaranteed ~4% loss on the hedged portion, matching the −4.1% observed ROI.
The balancing side had been allowed to bypass the pair-cost cap so inventory
could always be hedged; it achieved balance and broke price discipline (only 4%
of pairs cleared under $1.00). That bypass is the bug the 2026-08-05 gates
close.
