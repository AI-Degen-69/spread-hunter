# Live execution lane audit — #840

How the live lane (`core_brain/order_manager.py`, `core_brain/trader_loop.py`,
`core_brain/venue.py`) differs from the shadow lane (`core_brain/shadow_run.py`,
`core_brain/shadow_exec.py`, `core_brain/shadow_guard.py`), measured 2026-10-27.
Every claim below names the file and line-range it was read from.

## 0. Ticket corrections

1. The venue adapter is `core_brain/venue.py`. `core_brain/polymarket_client.py`
   does not exist (verified: no such file in `core_brain/`).
2. Shadow stores are explicit, separate database files passed per run
   (`--db data/NN_shadow_<stamp>.db`), never the production registry. The only
   sense in which they are "ephemeral" is that they are gitignored rehearsal
   artifacts.
3. A resting post-only bid 15¢ below the best bid can still fill if the market
   moves. Fill probability is small but **not** 0.0% — hence the $2 exposure
   ceiling and undo steps on the reachability check.
4. `cancel-all` / `cancel-market` cancel orders the check did not create. The
   check cancels by exact order ID; `cancel-market` is only a fallback when no
   venue ID was returned (pre-flight already proved zero other account orders
   on that market).
5. `balance` shows the CLOB collateral balance + allowance only
   (`core_brain/order_manager.py:376-413`). It does not prove the on-chain
   conditional-token approval (`is_approved_for_all`) that selling and merging
   need. No read-only command reporting `is_approved_for_all` was found — this
   is a **gap**, not a step.

## 1. The champion, exactly

`prudent` preset (`core_brain/config.py:596-602`) + five per-field overrides.
Loader order is preset-first, per-field-env-wins (`MakerConfig.from_env`,
`core_brain/config.py:313-315` then overrides). `shadow_cfg()`
(`core_brain/shadow_run.py:525-543`) is a straight alias for `config.load()`,
so both lanes read the same map — pinned by
`tests/test_champion_lane_parity.py`.

| Knob | Value | Notes |
|---|---|---|
| `BOT_QUOTE_TOURNAMENT_PRESET` | `prudent` | dynamic offset ON, 0.60x, clamp 2¢–4¢; carries no deadband/stop/queue gate of its own |
| `BOT_ORDER_REQUOTE_DEAD_BAND_USD` | `0.06` | USD per share (6¢), so no ÷100 anywhere (`config.py:404-408`, renamed by #872) |
| `BOT_RISK_STOP_MODE` | `ABSOLUTE_DROP` | the drop below is inert unless this mode is set (shipped default `NONE`) |
| `BOT_RISK_STOP_ADVERSE_DROP_USD` | `0.045` | USD per share (4.5¢), bounded to `DROP_USD_MIN..MAX` (`config.py:465-470`, renamed by #872) |
| `BOT_ORDER_GATE_QUEUE_CLEAR_ON` | `1` | without it the gate is record-only |
| `BOT_ORDER_QUEUE_MAX_CLEAR_MIN` | `3` | minutes |

Retired settings nobody may use: `stop_loss_trigger_usd_cents`,
`single_buy_grace_sec`, the old `usd`/`pct` stop settings, `HUNTER_*` aliases
(`shadow_cfg` docstring notes #619 retired the grace knob).

The 6¢ tournament-arm offset ceiling (tournament cockpit) and the 6¢ requote
deadband are different settings — do not conflate them.

## 2. Planned simulation choices (by design, not bugs)

- **Unsigned client.** Shadow builds its venue client with no private key and
  no API credentials behind a deny-by-default proxy (`shadow_guard.py`); it
  cannot sign, so nothing it does can reach the venue.
- **Synthetic order IDs / local cancels.** Shadow rest/cancel/fill transitions
  rewrite local rows only (`shadow_exec.py`); venue reconciliation is off by
  design. Row-level shadow labels (`shadow-` id prefix, `shadow_merge`) sit on
  top of the real discriminator, which is the store file itself.
- **Rehearsal pair-cost override.** `resolve_pair_cost_trial` loosens
  `bot_pos_price_max_pair_cost_usd` only inside a declared rehearsal
  (`config.py:716-734`); live always enforces `0.99`.
- **Rehearsal bankroll.** A shadow run may pin `starting_bankroll_usd`, which
  replaces only `bot_quote_bank_baseline_usd` (`shadow_run.py:1299-1319`);
  pinned by parity test 4.

## 3. Unexplained divergences (follow-ups, NOT fixed here)

Each is filed to `docs/backlog/i840-live-reachability.md`. Evidence:

1. **Rollback marks survivors cancelled on failed cancel.**
   `trader_loop.py:2736-2752` — on partial post, each survivor's cancel is
   attempted; if it raises, only a warning is logged, yet **all** legs are then
   marked `cancelled` and their venue IDs are never attached. A cancel that
   failed at the venue reads as cancelled locally.
2. **Lenient cancel-response handling.** `order_execution.py:57-62`
   (`cancel_all_orders`) and `trader_loop.py:2793-2817` (`_cancel_orders`):
   any response that does not raise is success; `cancel_all_orders` returns
   `ok=True` without inspecting the receipt.
3. **No order readback in `_submit_intents`.** The CLI `quote()` reads every
   accepted order back with `get_order` and verifies the token
   (`order_manager.py:640-678`); `_submit_intents` attaches venue IDs from the
   post response with only an `asset_id` field check (`trader_loop.py:2703-2724`)
   and never calls `get_order` (no `get_order` call exists in `trader_loop.py`).
4. **Pair-ID handling differs per path.** CLI mints `pair-<uuid>` per quote;
   the loop and shadow executor follow their own pairing — same column, three
   writers, no shared mint.
5. **Launcher preset loss.** `scripts/spread-hunter-menu.ps1:109-140` strips
   `BOT_QUOTE_TOURNAMENT_PRESET` (and the rehearsal pair-cost cap) from the
   menu process and re-injects them **only** inside shadow runs
   (`Invoke-WithRehearsalTrialEnv`). A fresh live Trader/dashboard STARTed from
   the menu therefore runs **without** `prudent` — live may quote on shipped
   defaults while shadow rehearses on the champion.
6. **Inherited `BOT_*` shell values.** Both lanes read the same environment, so
   a stray exported `BOT_*` overrides the champion in either lane with no log
   line naming it.
7. **Dropped dynamic-offset telemetry in production.** `_market_specs`
   (`trader_loop.py:2446-2505`) builds per-market dicts carrying `min_size`,
   `shares`, `max_spread`, `tick`, `daily`, `title` — no `range_cents` /
   `velocity_measured_at` — so the `prudent` dynamic offset
   (`quotes.py:117-137`) falls back to the static offset in both lanes
   whenever the spec path (not the `LiveMarket` path at `trader_loop.py:400+`)
   feeds the quoter.
8. **No client timeouts.** CLI venue calls (`fetch_pinned_market`,
   `full_book`, post/cancel) pass fixed timeouts or none; a hung post can
   delay the cancel past the 5s bound the check promises.
9. **Reconciliation guess.** Matching a pending row to an unknown venue order
   compares token/price/size (`order_registry.py` orphan adoption) and can
   confuse two similar orders; an order that fills completely before its first
   sighting becomes `UNMATCHED_TRADE` while its row stays pending.

## 4. What each check proves

- **An accepted order** proves signing works, the venue issued an order ID,
  and balance + allowance covered it at that moment. It does **not** prove
  conditional-token approval, relayer health (merges run through the gas-free
  relayer, a separate service), or that the order will rest unfilled.
- **`status`** proves the venue answers `get_open_orders` and the registry
  reads; after Task 2, exit 0 additionally means the read succeeded.
- **`balance`** proves the CLOB collateral read; it says nothing about
  on-chain approvals.
- **`decide --all --db <scratch>`** proves book parsing + tick sizes for
  graduated markets. It **writes rows**, so it must point at a scratch DB,
  never `data/orders.db`.
- **`fetch_live_balance`** (`core_brain/account.py:270-287`) returns `None`
  on any failure — callers must read the bank line in the output, not assume a
  number means a fresh read.
- A trader **dry run still builds a signing client**; it proves flag plumbing,
  not venue reachability. Only shadow mode (no key loaded) gives the stronger
  guarantee, and it guarantees the opposite direction (cannot send).

## 5. Pre-flight commands (operator)

```powershell
python -m core_brain.order_manager status
python -m core_brain.order_manager balance
python -m core_brain.order_manager decide --all --db data\i840_decide_scratch.db
```
