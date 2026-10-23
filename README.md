# Spread Hunter

[![CodeRabbit Pull Request Reviews](https://img.shields.io/coderabbit/prs/github/AI-Degen-69/spread-hunter?utm_source=oss&utm_medium=github&utm_campaign=AI-Degen-69%2Fspread-hunter&labelColor=171717&color=FF570A&link=https%3A%2F%2Fcoderabbit.ai&label=CodeRabbit+Reviews)](https://coderabbit.ai)

A dedicated **real-money execution engine and operations dashboard** for the Polymarket
**spread hunter** strategy: buy a complete outcome set for less than it pays, then merge it
back to collateral.

This repo is the live trading side of the spread hunter project — not a simulation, not a
paper trader. Every command reaches the venue by default (see [Safety rails](#safety-rails)).

## The strategy

One UP share plus one DOWN share of the same binary market always redeems for exactly
**$1.00**. The bot screens the venue for markets whose book allows it, rests two-sided bids
*under mid* on both outcomes, waits for both legs to fill, and **merges** the assembled pair
back into $1.00 of USDC. The income is the difference:

```
profit per pair = 1.00 - (avg UP price + avg DOWN price)
```

`bot_pos_price_max_pair_cost_usd = 0.99` is the profit condition: the merge is the exit and the P&L event.
Graduated markets pay $0.00 in maker rewards and the KPI report does not estimate them;
a real venue credit would be a side accrual, never blended into trading PnL.

The two failure modes everything else exists to prevent: a pair assembled **over $1.00**
is a booked loss, and a **single buy** (one leg filled, one not) is a directional bet
nobody decided to take. The single-leg lifecycle (`DUAL_RESTING` → `PATIENT_WAIT` →
`ESCALATED_HEDGE` / `HARD_STOP` → `PAIR_LOCKED`) manages the one-sided position; it does
not taker-complete during ordinary management. `HARD_STOP` fires on an **entry-relative**
trigger — an optional dynamic drop from that leg's own fill average, always backed by an
emergency floor (75% by default) that never falls below `$0.01` — so a quote filled at
`$0.10–$0.14` is not liquidated the moment it fills.

Full statement: [docs/agents/strategy.md](docs/agents/strategy.md).

## Features

**Core brain** (`core_brain/`)
- `quotes.py` — the decision layer: where to rest both legs, and why not to
- `risk.py` — sizing ladder, inventory skew, dollar caps, hard blocks
- `single_leg_lifecycle.py` — the persisted per-pair policy for one-sided fills (#413)
- `single_buy_saver.py` — guarded completion and exit execution (not a second policy)
- `trader_loop.py` — multi-market rotation: decide → plan → submit/cancel
- `order_manager.py` — CLI: `status`, `balance`, `quote`, `poll`, `probe`, `merge`,
  `redeem`, `complete`, `exit`, `pairs`, `decide`, `audit`, `kpi`, `stray-guard`,
  `account-sweep`, `api-creds`, `cancel`, `cancel-market`, `cancel-all`
- `shadow_run.py` (+ `shadow_exec.py`, `shadow_pairs.py`, `shadow_fills.py`,
  `paired_shadow.py`) — isolated full-loop rehearsal against the live book; no signer,
  spends nothing
- `merge_pairs.py` — gasless merge & redemption (ABI, alt-bn128, EIP-712)
- `order_registry.py` (+ `registry_state.py`) — SQLite order/fill tracking + reconcile
  (`data/orders.db`)
- `market_feed.py` / `markets.py` / `market_meta.py` / `live_marks.py` — graduated
  universe feed, venue lookup, market metadata, live marks
- `pair_scanner.py`, `markout.py`, `unhedged_stop_loss.py` — scan state, post-fill
  mark-to-market, per-market markout gate
- `kpi.py`, `audit.py`, `account.py`, `cycle_stream.py` — live metrics, 3-way
  reconciliation, wallet balance, append-only cycle telemetry
- `statistics_*` / `statistical_analytics.py` — per-run statistics stores and reports
- `data_retention.py` — storage audit and pruning (`data/`, `runtime/`, `reports/`)
- `config.py` — live tuning configuration (dynamic caps scale with account value)

**Operations dashboard** (`dashboard/`)
- `server.py` — ops dashboard on `:8799` (FastAPI + uvicorn) with a browser SPA
  (`dashboard/static/`); master START/STOP drives the bot stack, per-service controls,
  run switcher across live and shadow stores

**Ops tooling** (`scripts/`)
- `filter_markets.py` — the filter funnel that graduates the universe
- `filter_loop.py` — continuous market filter loop feeding `runtime/markets.json`
- `global_stop_loss.py` — watchdog: over-cap pairs & repeat single-buy exits
- `audit_settlement.py` — settlement & balance verification
- `shadow_tournament.py` / `tournament_cockpit_server.py` / `tournament_hub.py` —
  paired rehearsal tournaments and cockpit
- `spread-hunter-menu.ps1` — PowerShell control center for the dashboard + bot stack
  (live, shadow, clean, status, statistical run, tournament cockpit)

## Repository layout

```text
spread-hunter/
  core_brain/             Core trading & execution engine
  dashboard/              Operations dashboard (:8799) + SPA
  scripts/                Market Filter, filter loop, watchdog, tournaments, PowerShell control center
  scoring/                Scoring, allocation and selection rules the Market Filter uses
  strategy/               Signal and sizing logic
  server.ts               TS dashboard bridge (:8800, read-only reverse proxy)
  configs/                Runner / preset configuration
  data/                   Order registry (orders.db — not committed) + per-run stores
  runtime/                Market universe, process registry, logs, cycle telemetry (not committed)
  reports/                Generated statistics reports (not committed)
  docs/agents/            Live agent contracts (safety, architecture, strategy, first-run, ...)
  docs/issues/            Issue showcases (<id>-presentation-<slug>.html)
  docs/runs/              Run notes and experiment records
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

# 4. Run the operations dashboard
python -m dashboard.server          # http://127.0.0.1:8799

# 5. Rehearse the full loop without spending (needs a fresh, unique store every run)
python -m core_brain.shadow_run --minutes 5 --db data/NN_shadow_<stamp>.db
```

Windows operators use the control center instead (`.\scripts\spread-hunter-menu.ps1 status`
to inspect, `start` / `shadow-run` to launch). Full run anatomy, every command and its
arguments: [docs/agents/first-run.md](docs/agents/first-run.md).

## TS dashboard bridge

`server.ts` serves the dashboard frontend on **http://127.0.0.1:8800** and reverse-proxies
`GET /api/*` to the live Python backend on **:8799** — real data only, no mocks.

- Read-only by default: control actions return 501 unless the bridge is started with
  `BRIDGE_CONTROL=1`. Do not enable this during shadow runs.
- Manual run: `PORT=8800 node server.ts` (Node ≥ 24; `PY_DASH_URL` overrides the
  upstream, default `http://127.0.0.1:8799`).
- The menu's `statistical-run` action and `status` view manage and show the bridge
  (`TS Bridge`); `stop-shadow` stops the shadow session.

Bridge tests: `node --test tests/bridge/token-inject.test.ts tests/bridge/control-gate.test.ts`

## Operating guide

**CLI** — `python -m core_brain.order_manager <command>`. Live by default; pass `--no-live` for a
dry-run preview.

| Command | Purpose |
| --- | --- |
| `status` / `balance` | Account state, wallet balance (read-only) |
| `decide` | Read-only quote decision for graduated markets |
| `quote <condition_id> --price P --size N` | Rest a maker bid (opening command) |
| `poll --interval 0.5` | Reconcile fills from the venue into `data/orders.db` |
| `probe` | Live latency probe across series windows |
| `merge <condition_id> --amount N` | Merge UP+DOWN pairs back to USDC (the exit) |
| `redeem <condition_id>` | Gasless redemption of resolved positions |
| `complete <pair_id>` | Cross the book to complete a one-sided pair (spends — opening command) |
| `exit <pair_id>` | Guarded stop-loss exit of a single buy |
| `pairs` / `kpi` / `audit` | Pair inventory, live performance report, 3-way reconciliation (read-only) |
| `stray-guard` / `account-sweep` | Adopt detached pairs / cancel strays; balance sync |
| `cancel` / `cancel-market` / `cancel-all` | Pull resting orders |

**Bot stack** — a live run is the dashboard plus four processes: Market Filter
(`scripts/filter_loop.py` → `runtime/markets.json`), Order Manager poll
(`order_manager poll`), Trader (`trader_loop --live`, rests REAL maker bids), and the
Global Stop Loss watchdog (`scripts/global_stop_loss.py`). The dashboard's master
START/STOP button (or the PowerShell menu) drives the stack; START refuses a duplicate
stack and preserves the captured `starting_account_value`.

**PowerShell control center** — `.\scripts\spread-hunter-menu.ps1` with
`start` / `stop` / `host` (`open`) / `shadow-run` / `stop-shadow` / `open-shadow` /
`clean` / `status` / `statistical-run` / `tournament-cockpit`. See
[docs/agents/first-run.md](docs/agents/first-run.md) for what each option covers.

## Safety rails

1. **LIVE is the default.** This is the real-money execution repo. Every subcommand reaches
   the venue by default. Use `--no-live` for dry-run preview; use `shadow_run` (no signer,
   explicit per-run store, `data/orders.db` refused) for full-loop rehearsal.
2. **Closing commands are pre-approved:** `exit`, `merge`, `redeem`, `cancel`,
   `cancel-market`, `cancel-all` reduce exposure. Cancelling pulls resting orders only —
   a leg that already filled is still open exposure.
3. **Opening commands require explicit supervision:** `quote`, `complete`, the Trader loop
   and the dashboard's START button all rest or spend real funds. `complete` buys the
   missing side: it removes the risk of a single buy, but it does so by spending, so it is
   an opening command and not a closing one.
4. **Dynamic caps** (`core_brain/config.py`): `bot_risk_exposure_max_order_pct = 0.25` (25%, $25.00 at
   $100 baseline), `bot_risk_exposure_max_naked_pct = 0.06` (6%, $6.00 at $100 baseline),
   `bot_risk_exposure_bankroll_ceiling_pct = 0.90` (90%, $90.00 at $100 baseline),
   `bot_pos_price_max_pair_cost_usd = 0.99`. The `_pct` fields are ratios in `0.0..1.0`, not
   percentage points. Caps scale with the live account mark.
5. **`data/orders.db` is the production registry.** Read it; never rewrite or delete it.

Full rules: [docs/agents/safety.md](docs/agents/safety.md).

## Docs

- [AGENTS.md](AGENTS.md) — operating rules, commands, verification and merge discipline
- [docs/agents/safety.md](docs/agents/safety.md) — safety rails, shadow-run contract
- [docs/agents/strategy.md](docs/agents/strategy.md) — the strategy, universe, pricing, lifecycle
- [docs/agents/architecture.md](docs/agents/architecture.md) — module layout, lifecycle, retention
- [docs/agents/first-run.md](docs/agents/first-run.md) — run anatomy, every command and argument
- [docs/agents/verifying.md](docs/agents/verifying.md) — what "done" means
- [docs/agents/git-workflow.md](docs/agents/git-workflow.md) — branching, PRs, review rounds
