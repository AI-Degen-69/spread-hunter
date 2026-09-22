# Spread Hunter

[![CodeRabbit Pull Request Reviews](https://img.shields.io/coderabbit/prs/github/AI-Degen-69/spread-hunter?utm_source=oss&utm_medium=github&utm_campaign=AI-Degen-69%2Fspread-hunter&labelColor=171717&color=FF570A&link=https%3A%2F%2Fcoderabbit.ai&label=CodeRabbit+Reviews)](https://coderabbit.ai)

A dedicated **real-money execution engine and operations dashboard** for the Polymarket
**spread hunter** strategy: buy a complete outcome set for less than it pays, then merge it
back to collateral.

This repo is the live trading side of the spread hunter project — not a simulation, not a
paper trader. Every command reaches the venue by default (see [Safety Rails](#safety-rails)).

## The strategy

One UP share plus one DOWN share of the same binary market always redeems for exactly
**$1.00**. The bot screens the venue for markets whose book allows it, rests two-sided bids
*under mid* on both outcomes, waits for both legs to fill, and **merges** the assembled pair
back into $1.00 of USDC. The income is the difference:

```
profit per pair = 1.00 - (avg UP price + avg DOWN price)
```

Measured over the paper run: **476 merge closes returned +$1,172.35** at an average pair cost
of **$0.96006** — roughly 4c per pair. `max_pair_cost = 0.99` is the profit condition: the
merge is the exit and the P&L event.

- **Maker rebates are extra, not the income.** Over the same run they accrued about
  $0.22/day against $566 committed — four hundredths of a percent. This is a pair-assembly
  arbitrage that happens to earn rebates while it waits, not a rebate farm.
- **The universe is not BTC 5-minute binaries.** The Market Filter's funnel (24h volume,
  top-3 bid depth on both sides, book spread, horizon ≤ 30 days) writes survivors to
  `runtime/markets.json`, and the Trader quotes *only* that list. Whatever clears the funnel
  is in scope; in the measured run the survivors were mostly tennis, baseball and esports,
  but the filter decides, not the category. The 5-minute BTC series was measured dead on
  adverse selection and survives only as a legacy field.
- **Spread capture pricing mode.** `objective = "spread_capture"` (formerly `"rewards"`)
  selects the from-mid pricing path that assembles the pair at `≈ 1.00 - 2*offset` — the
  mechanism that makes the strategy work. See [AGENTS.md](AGENTS.md) for the full statement.

## Features

**Core brain** (`core_brain/`)
- `quotes.py` — the decision layer: where to rest both legs, and why not to
- `risk.py` — sizing ladder, inventory skew, dollar caps, hard blocks
- `unhedged_stop_loss.py` — per-market markout state machine + trader posture (HALTED etc.)
- `trader_loop.py` — multi-market rotation: decide → plan → submit/cancel (5s cadence)
- `order_manager.py` — CLI: `status`, `balance`, `decide`, `quote`, `poll`, `merge`, `redeem`,
  `complete`, `exit`, `cancel`, `cancel-market`, `cancel-all`, `pairs`, `kpi`, `audit`
- `single_buy_saver.py` — single-buy rescue: complete the pair, or exit the buy
- `merge_pairs.py` — gasless merge & redemption (ABI, alt-bn128, EIP-712)
- `order_registry.py` — SQLite order/fill tracking + reconcile (`data/orders.db`)
- `audit.py`, `kpi.py`, `account.py`, `cycle_stream.py`, `market_feed.py`, `markets.py`

**Operations dashboard** (`dashboard/`)
- `server.py` — ops dashboard on `:8799` with a browser SPA (`dashboard/static/`)
- System start/stop endpoints drive the bot stack through
  the same code path as the dashboard buttons (Market Filter + Order Manager + Trader)
- Service cards, guardrail health, cycle telemetry ring, account/exposure tiles

**Ops tooling** (`scripts/`)
- `filter_markets.py` — the filter funnel that graduates the universe
- `filter_loop.py` — continuous market filter loop feeding `runtime/markets.json`
- `global_stop_loss.py` — watchdog: over-cap pairs & repeat single-buy exits
- `spread-hunter-menu.ps1` — PowerShell control center: start/stop/status for the
  dashboard + bot stack, with a themed, color-coded status view

## Repository layout

```text
spread-hunter/
  core_brain/             Core trading & execution engine
  dashboard/              Operations dashboard (:8799) + SPA
  scripts/                Market Filter, filter loop, watchdog, PowerShell control center
  scoring/                Scoring, allocation and selection rules the Market Filter uses
  strategy/               Signal and sizing logic
  data/                   Order registry (orders.db — not committed)
  runtime/                Market universe, process file, logs and cycle telemetry (not committed)
  tests/                  Full hermetic unit & integration test suite
```

## Quick start

```bash
# 1. Install
pip install -r requirements.txt
pip install -r requirements-dev.txt

# 2. Configure credentials (never commit the real .env)
cp .env.example .env
#   POLY_PRIVATE_KEY, POLY_FUNDER, POLY_SIG_TYPE, optional RELAYER_API_KEY / POLYGON_RPC

# 3. Check wallet and account status (read-only)
python -m core_brain.order_manager status
python -m core_brain.order_manager balance

# 4. Run the operations dashboard
python -m dashboard.server          # http://127.0.0.1:8799

# 5. Tests
pytest -q
```

## TS Dashboard Bridge

The TypeScript dashboard (`server.ts`) serves the AI Studio frontend from
`dashboard/static/` on **http://127.0.0.1:8800** and reverse-proxies `/api/*`
to the live Python backend on **:8799** — real data only, no mocks.

- Start: `\scripts\spread-hunter-menu.ps1 statistical-run -Hours 24` starts the
  bridge alongside the Python stack (recorded as `ts_bridge` in
  `runtime/shadow-session.json`; visible as `TS Bridge` in `status`).
- Stop: `\scripts\spread-hunter-menu.ps1 stop-shadow` tears the bridge down
  with the rest of the shadow session.
- Read-only by default: POST control actions return 501 unless the bridge is
  started with `BRIDGE_CONTROL=1`. Do not enable this during shadow runs.
- Manual run: `PORT=8800 node server.ts` (Node ≥ 24, no npm install;
  `PY_DASH_URL` overrides the upstream, default `http://127.0.0.1:8799`).
- The bridge injects the live control token into the HTML it serves — the same
  token Python bakes in — so the frontend's control buttons carry a token the
  engine will accept once control forwarding is enabled.

TS bridge tests: `node --test tests/bridge/token-inject.test.ts tests/bridge/control-gate.test.ts`

## Operating guide

**CLI** — `python -m core_brain.order_manager <command>`. Live by default; pass `--no-live` for a
dry-run preview.

| Command | Purpose |
| --- | --- |
| `status` / `balance` | Account state, wallet balance |
| `decide` | Read-only quote decision for graduated markets |
| `quote <condition_id> --price P --size N` | Rest a maker bid (opening command) |
| `poll --interval 0.5` | Reconcile fills from the venue into `data/orders.db` |
| `merge <condition_id> --amount N` | Merge UP+DOWN pairs back to USDC (the exit) |
| `redeem <condition_id>` | Gasless redemption of resolved positions |
| `complete <pair_id>` | Cross the book to complete a one-sided pair (spends -- opening command) |
| `exit <pair_id>` | Stop-loss exit of a single buy |
| `cancel` / `cancel-market` / `cancel-all` | Pull resting orders |

**Bot stack** — the dashboard's START/STOP buttons (or the PowerShell menu) control the
Market Filter (`filter`), Query Polymarket (`query`) and Decide & Execute (`decide`).
START launches all three; STOP stops them. Decide & Execute rests real maker bids: verify
dashboard state before starting.

**PowerShell control center** — `.\scripts\spread-hunter-menu.ps1` offers
`start` / `stop` / `status` / `open`. The `status` view shows the dashboard, every stack
process (with PID and module path), the Global Stop Loss watchdog, the universe feed, and checkout
identity in one aligned, color-coded report.

## Safety rails

1. **LIVE is the default.** This is the real-money execution repo. Every subcommand reaches
   the venue by default. Use `--no-live` for dry-run preview.
2. **Closing commands are pre-approved:** `exit`, `merge`, `redeem`, `cancel`,
   `cancel-market`, `cancel-all` reduce exposure. Cancelling pulls resting orders only --
   a leg that already filled is still open exposure.
3. **Opening commands require explicit supervision:** `quote`, `complete`, the Trader loop
   and the dashboard's START button all rest or spend real funds. `complete` buys the
   missing side: it removes the risk of a single buy, but it does so by spending, so it is
   an opening command and not a closing one.
4. **Dynamic Caps:** `order_risk_pct = 25%` ($25.0 baseline), `naked_risk_pct = 6%` ($6.0 baseline), `bankroll_ceiling_pct = 90%` ($90.0 baseline), `max_pair_cost = 0.99` (`core_brain/config.py`).

## Docs

- [AGENTS.md](AGENTS.md) — the objective, universe rules, and design history
- [Dashboard redesign spec](docs/superpowers/specs/2026-08-21-dashboard-redesign-design.md)
- [Screener kanban spec](docs/superpowers/specs/2026-08-21-screener-kanban-tab.md)
