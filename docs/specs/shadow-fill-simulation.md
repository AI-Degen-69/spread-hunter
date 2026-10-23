# Shadow fill simulation — spec

**Date:** 2026-08-24
**Status:** proposed
**Owner:** operator (ai.degen420@gmail.com), implemented by agent

## The problem

`python -m core_brain.shadow_run` rehearses the Trader against the live book and spends
nothing. It is pre-approved for exactly that reason ([docs/agents/safety.md](../../agents/safety.md)
§3a). But it stops one step short of what an operator needs before going live: it decides,
and then nothing happens.

`build_shadow_seam` wires `record_submit`, which appends decided intents to an in-process
list and returns a count. No order row is written, so nothing rests; nothing rests, so
nothing fills; nothing fills, so `inventory_from_registry` returns a flat `Inventory` on
every rotation forever. Every stage that keys off a fill therefore never runs:

| Stage | Module | Runs in a shadow run today |
| --- | --- | --- |
| Decide (gates, sizing, caps) | `core_brain/quotes.py`, `core_brain/risk.py` | Yes — unchanged from live |
| Rest an order | `core_brain/trader_loop.py:_submit_intents` | No — recorder only |
| Fill attribution | `core_brain/order_registry.py` | No |
| Single buy rescue (U35) | `core_brain/single_buy_saver.py` | No |
| Markout / stop-loss posture | `core_brain/markout.py`, `core_brain/unhedged_stop_loss.py` | No — `fleet_stats` has no sample |
| Merge close | `core_brain/merge_pairs.py` | No |
| Dashboard Performance tab | `core_brain/kpi.py` | Reads zeros, correctly |

Observed on 2026-08-24: a ten-minute run logged the identical decision for the same market
on every one of ~120 rotations, because the inventory it decided against never moved.

## Goal

A shadow run reproduces the whole live lifecycle — decide, rest, fill, single buy, exit or
merge, PnL — against the live book, writing only `data/shadow.db`, holding nothing it
could sign with.

## Non-goals

- No on-chain merge. Merging is a signed, gasless EIP-712 transaction; a shadow run has no
  key and must not grow one. The merge is *recorded*, not performed.
- No change to any live path. `core_brain/live_fill_engine.py` and its invariant ("live
  fills come ONLY from the venue") stay exactly as they are.
- Shadow numbers never become a performance claim. They are rehearsal.

## Invariants

1. **Nothing that can sign.** The venue object stays `shadow_guard.ReadOnlyVenue` over an
   unauthenticated client. New code adds no credential read and no new client construction.
2. **Store guard unchanged.** `assert_not_production_registry(db_path)` still runs before
   anything is constructed, and every simulated row lands in the shadow store only.
3. **Simulated rows are labelled at the row level, except where labelling one would mean
   editing a live module.** `orders.order_id` starts `shadow-`, `fills.trade_id` starts
   `shadow-`, and a merge close carries `method='shadow_merge'`. There is no
   `'shadow_exit'`, and there will not be one: an exit close is written by
   `core_brain/single_buy_saver.py:_record_exit_close`, live money-path code, so an exit
   taken during a rehearsal carries the same `method='single_buy_exit'` a live exit does.
   Relabelling it would mean editing the money path to serve a rehearsal, which costs more
   than the label is worth. **The store file is the boundary** — `data/shadow.db` versus
   `data/orders.db`, enforced by `shadow_guard.assert_not_production_registry` before
   anything is constructed. Row-level labels are a second line, not the only one.
4. **The live fill engine is not touched, and never imports the shadow model.** Inference
   from book and tape is correct for a rehearsal and forbidden live. A test enforces the
   one-way dependency.
5. **The fill model is conservative.** Only tape-confirmed volume at the order's own price
   credits a fill. The book-only rule ("level emptied, credit the remainder") is what
   reported a 50% fill rate against a tape-confirmed 3% in the paper run — see the
   docstring of `core_brain/markets.py:recent_trades`. It is not used here.
6. **Caps are the live caps.** `MAX_ORDER_USD`, `MAX_TOTAL_USD` and `max_pair_cost` apply
   to simulated orders exactly as `_submit_intents` applies them.

## Design

### New modules

- `core_brain/shadow_fills.py` — the model. Pure functions over plain values: resting
  orders in, tape volume in, credited fills out. No network, no SQLite, no clock.
- `core_brain/shadow_exec.py` — the recorders that own the shadow store: submit intents as
  order rows, apply credited fills as fill rows, and a `ShadowExecutionClient` that gives
  `single_buy_saver` the four client methods it calls, backed by the shadow store instead
  of the venue.

`core_brain/shadow_run.py` wires them; no other module changes.

### Fill model

A resting BUY at price `p` on token `t`:

- `queue_ahead` is the size already resting at exactly `p` on the bids side when the order
  was posted. Better-priced bids are ahead of the book, not ahead of this order at its own
  level, and are not counted.
- Each cycle, `markets.recent_trades(condition_id, seen)` returns volume that actually
  traded, per token, per price, since the previous look, de-duplicated by trade identity.
- Volume at `p` consumes `queue_ahead` first, then credits this order:
  `consumed = min(v, queue_ahead)`; `v -= consumed`; `fill = min(v, remaining)`.
- A credited fill is recorded at the order's own price. A maker fill executes at the limit.
- Partial credit sets the row `partial`; full credit sets it `filled`.

### Rotation shape

The settle step runs at the start of each market visit, before `decide`, so the decision
sees an inventory that already includes anything that filled since the last visit. It is
wired by wrapping the seam's `inventory_fn` — the one port `trader_loop.run` calls per
market before deciding — so `core_brain/trader_loop.py` needs no edit, the same way the
time box is driven entirely from the injected `sleep_fn`.

1. **settle** — read the tape once per market, credit fills, write rows.
2. **pairs pass** — `single_buy_saver.auto_manage_pairs(client, registry, cfg, venue_positions=...)`
   with the shadow execution client and positions read from the shadow store, so the
   Data API is never called and the pass cannot fail closed on a missing read.
3. **merge** — a balanced pair records a close: `method='shadow_merge'`, proceeds
   `shares * 1.00`, cost basis from its own fills.
4. **decide / submit** — unchanged, now against a moving inventory.

### What the operator sees

- Terminal: the existing per-visit line grows fill and inventory context.
- Dashboard on `--db data/shadow.db`: open orders, fills, pair cost, single buys and the
  Performance tab all move, under the SHADOW badge that already gates START.

## Acceptance

1. A ten-minute shadow run on a market with traded volume produces at least one fill row,
   and the decision text for that market changes from the flat-inventory branch.
2. `data/orders.db` is byte-identical before and after (guarded by `assert_not_production_registry`
   plus the hermetic test suite).
3. Every simulated row carries its `shadow-` label.
4. No live module imports `core_brain.shadow_fills` or `core_brain.shadow_exec`.
5. `python -m pytest -q` green, including a fill-model suite driven by recorded book and
   tape fixtures rather than the network.
