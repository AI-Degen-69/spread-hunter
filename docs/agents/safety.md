# Safety rails

This repository places real orders with real money. Read this before running anything.

## 1. LIVE is the default

`python -m core_brain.order_manager` reaches the venue on every subcommand. `--no-live`
gives a dry-run preview. There is no separate staging venue.

## 2. Closing commands are pre-approved

`exit`, `merge`, `redeem`, `cancel`, `cancel-market`, `cancel-all` reduce exposure. An
agent may run them without asking.

Cancelling pulls **resting** orders only. A leg that already filled is still open
exposure — that needs `complete` (supervised) then `merge`, or `exit`.

## 3. Opening commands require explicit supervision

Four things spend money:

- `quote` — rests new bids.
- `complete` — buys the missing side of a single buy. It removes risk, but it does so by
  spending, so it belongs here and not with the closing commands.
- The Trader loop (`core_brain.trader_loop --live`).
- The dashboard's **START** button, which calls `start_bot()` and launches the Trader. A
  click on the dashboard is a live order path exactly like a typed command.

Propose the command; the operator runs it. An agent runs one only when the operator says
so in that session.

## 3a. Shadow run is the one loop command an agent may run

`python -m core_brain.shadow_run --minutes N --db <unique-shadow-store>` rehearses the full
loop against the live book and is pre-approved, unlike every other loop command. The reason
is structural, not procedural: it builds its venue client with **no private key and no API
credentials**, wrapped in a deny-by-default proxy (`core_brain/shadow_guard.py`), so there
is nothing loaded with which a write could be signed. It requires an explicit per-run
store path; `data/orders.db` is refused outright. It normally stops itself on a wall-clock time box; explicitly passing a negative `--minutes` runs until stopped. This does not load signing credentials or permit real orders.

Two cautions:

- **Shadow numbers are rehearsal, not results.** Fills, positions and merges are modelled
  or arithmetic, never a venue execution; never quote them as performance. See below for
  what that means for each.
- The guarantee holds only for this entrypoint. If a change makes `core_brain.shadow_run`
  able to construct a signing client, it comes off the pre-approved list until reviewed.

### Watching one on the dashboard

```powershell
python -m dashboard.server --db data\04_shadow_24-09_test.db --port 8799   # terminal 1, read-only
python -m core_brain.shadow_run --minutes 10 --interval 5 --db data\04_shadow_24-09_test.db --run-id shadow-04  # terminal 2
```

The page badges itself **SHADOW** with the store it is reading, and **START is refused**
while it does: the stack it launches always writes `data/orders.db`, so its orders would
be invisible on a page reading anything else.

What a shadow run does now is more than decide: it rests simulated orders, credits fills
from the trade tape, runs the production single-buy rescue pass, and records a merge close
per balanced pair -- all inside the explicit per-run shadow store, next to the decision
path (scan state, decisions logged, skip and pass reasons, the cycle stream). So the order,
fill, position and PnL panels no longer read zero during a run -- and a zero there is no
longer proof that
nothing happened. Read it as what it is: the shadow store's honest state at that moment,
nothing more. For a manual run, use a fresh, unique path and point the dashboard at that
same path.

**What tells a simulated row from a real one is the store file it is in.** The explicit
per-run shadow store versus `data/orders.db`, and a shadow run is refused the production
registry before it constructs anything. Row-level labels are a second line on top of that,
and they do not cover every row:

- `orders.order_id` and `fills.trade_id` start `shadow-`.
- A **merge** close carries `method='shadow_merge'`.
- An **exit** close does not carry a shadow method. The pairs pass a shadow run rehearses is
  the production one, and its close is written by `core_brain/single_buy_saver.py` -- live
  money-path code -- so an exit taken during a rehearsal is labelled `single_buy_exit`,
  exactly as a live exit is. That is deliberate: relabelling it would mean editing the money
  path to serve a rehearsal. Read the store path, not the method string, when you need to
  know whether a close was real.

Three things about those numbers an operator has to hold onto:

- **The merge is arithmetic, not an on-chain transaction.** A shadow run has no key --
  `record_shadow_merges` closes a balanced pair by writing `shares * (1.00 - pair cost)` to
  the store, the same result the real merge would realize, without a wallet ever touching
  the chain.
- **Fills are modelled, not observed.** They come from tape-confirmed trade volume and
  queue position (`core_brain/shadow_fills.py`), which is the best a process with no
  resting order on the real book can do -- but it is an estimate. A fill rate out of a
  rehearsal is a model output, never a measurement of what the venue would have given.
- **A completion fills at the book's best ask.** `single_buy_saver`'s rescue pass, run
  against the shadow store, buys the missing leg at the best ask on the book at that
  moment -- the optimistic end of what a taker actually gets, not a guaranteed price.

One caveat the badge cannot fix: the cycle-stream ring (`runtime/cycle_events.jsonl`) is
one file for every process, so a shadow run's events land beside whatever a live run left
there. Each record carries the writing `pid`; that is what tells them apart.


## 4. Limits

Risk caps scale dynamically with the live Polymarket account mark via `derive_dynamic_caps`
in `core_brain/config.py`:
- **Single Order Cap (`order_risk_pct`):** `25%` of account value ($25.00 baseline at $100).
- **Single Buy / Naked Cap (`naked_risk_pct`):** `6%` of account value ($6.00 baseline at $100).
- **Total Portfolio Risk Ceiling (`bankroll_ceiling_pct`):** `90%` of account value ($90.00 baseline at $100).
- **Max Pair Cost:** `$0.99` (guarantees $\ge \$0.01$ profit per pair upon merge).

`MAX_ORDER_USD = 25.0`, `MAX_TOTAL_USD = 90.0` in `core_brain/venue.py` are independent hard
venue caps matching baseline `MakerConfig` defaults. Dynamic caps are evaluated at cycle runtime in
`core_brain/trader_loop.py`, `core_brain/order_manager.py`, and `core_brain/single_buy_saver.py`.

Real-money checks are allowed and are often the only real proof of a change. Keep them at
the venue minimum, inside these caps, and always pair them with the undo command.

## 5. `data/orders.db` is the registry, and the only one

Every module resolves it through `core_brain.order_registry.DEFAULT_DB_PATH`. Read it;
never rewrite or delete it.

## 6. Changes that always land with a test

Sizing, fill attribution, and the merge path. An under-counted position invites fresh
exposure on top of it; a pair assembled over $1.00 is a booked loss on an instrument that
pays exactly $1.00.
