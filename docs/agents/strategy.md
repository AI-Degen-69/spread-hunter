# The strategy

**Buy a complete outcome set for less than it pays, then merge it back to collateral.**

One UP share plus one DOWN share of the same binary market always redeems for exactly
$1.00. The bot screens the venue for markets whose book allows it, rests two-sided bids
*under mid* on both outcomes, waits for both legs to fill, and **merges** the assembled
pair back into $1.00 of USDC. The income is the difference:

```
profit per pair = 1.00 - (avg UP price + avg DOWN price)
```

Measured over the paper run: 476 merge closes returned **+$1,172.35** at an average pair
cost of **$0.96006** — roughly 4c per pair. That is the strategy, and it is what the name
"spread hunter" refers to.

`max_pair_cost = 0.99` is therefore the **profit condition**, not a side constraint. The
merge is the exit and the P&L event, not housekeeping.

## Maker rebates are extra, not the income

Polymarket's liquidity-reward program pays for resting size, and the bot qualifies for it.
Over the same measured run it accrued about **$0.22/day against $566 committed** — four
hundredths of a percent. Rebates are a side accrual, estimated and reported separately,
never blended into trading PnL (`config.maker_fee`, `config.rebate_rate`).

Describe this bot as a **pair-assembly arbitrage** that earns rebates while it waits.

## The universe: liquid markets inside a 30-day horizon

The **Market Filter** (`scripts/filter_markets.py`, run continuously by
`scripts/filter_loop.py`) funnels the venue — 24h volume, top-3 bid depth on both sides,
book spread, horizon — and writes the survivors to `runtime/markets.json`. The **Trader**
(`core_brain/trader_loop.py`) quotes **only** that list, via `core_brain/market_feed.py`.

- Horizon cap is **30 days** (`config.select_max_days_to_resolve`), which admits liquid
  sports, esports, macro and political markets.
- **The filter defines the universe, not a category list.** Anything that clears the
  funnel is fair game. In the measured run the survivors happened to be mostly tennis,
  baseball and esports, but that is an observation about one period's book quality, not
  a rule — do not hard-code a sport list or reject a market for its category.
- Graduated markets carry `source: "spread"` with `daily: 0.00`. They pay no rewards at
  all, and they are the markets that actually trade.
- BTC 5-minute binaries are out of scope: `config.series_slug = "btc-up-or-down-5m"` is a
  **legacy field**, read only by `core_brain/markets.py` and the `probe` latency harness.
  The 5-minute BTC series was measured dead: *"Adverse selection, not fee level, is what
  killed 5-min BTC."*

## Pricing mode: spread_capture

`config.objective` defaults to the string `"spread_capture"` (formerly `"rewards"`) and
`trader_loop._market_cfg` sets it on every market. It selects
`quotes._decide_quotes_from_mid`, which rests both legs at `mid - offset`. Because
`mid_UP + mid_DOWN ≈ 1.00`, that construction assembles the pair at `≈ 1.00 - 2*offset` —
precisely how a sub-$1.00 pair gets built.

The alternative branch (`objective="pair"`) prices off the **ask** and was measured dead:
it puts every quote half a spread *above* mid, so the pair costs `1.00 + spread` by
construction. It is unreachable in production and must stay that way.

No reward-economics knob reaches live code. `reward_min_payout_usd`,
`reward_floor_multiple`, `est_reward_pool_usd`, `rebate_rate`, `marginal_return_floor`,
`allocation_budget` and `max_market_frac` belong to the allocator in `scoring/allocate.py`
and are read by nothing in `core_brain/` or `dashboard/`. `quotes.reward_score()` formats
a log string and feeds no sizing decision.

## The two failure modes

A pair assembled **over $1.00** is a booked loss on an instrument that pays exactly $1.00.
A **one-sided fill** is a directional bet nobody decided to take. `risk.py` protects pair
cost and exposure limits; the single-leg lifecycle manages the one-sided position;
`unhedged_stop_loss.py` remains a separate per-market markout gate.

## One-sided fill lifecycle (#413)

The policy lives in `core_brain/single_leg_lifecycle.py` and persists its state per pair.
`DUAL_RESTING` becomes `PATIENT_WAIT` after one leg fills: leave the opposite maker at its
target and do not taker-complete during ordinary management. Once the held-leg drawdown
(fill average minus best bid) reaches `2 × dynamic_offset_for(cfg)[0]`, transition
persistently to `ESCALATED_HEDGE`. The Trader raises the opposite maker to the highest
tick-aligned bid fitting `min($0.99, cfg.max_pair_cost) - held average`; once escalated,
the state remains escalated until the pair locks or another terminal route applies.

`HARD_STOP` takes precedence when the held best bid is at or below `$0.15` and routes
through `single_buy_saver.py`'s guarded exit. Balanced inventory within the configured
pair cap becomes `PAIR_LOCKED`. The only automatic taker-completion exception is the
existing market-end-aware settlement fallback: it makes one capped attempt when due,
then uses the guarded exit if completion is refused. `single_buy_saver.py` executes these
routes; it is not an independent lifecycle policy.

## The completable-cost gate and the re-quote dead band

Two execution rules from shadow run `run-2809a7161de1` (209 orders, zero fills, 2026-08-25):

**Completable-cost gate** (`max_completable_pair_cost`, default `1.00`). The existing
`max_pair_cost = 0.99` checks a **both-maker** pair: `up_bid + down_bid`. On a binary
market the legs are anti-correlated (`UP + DOWN ≈ 1.00`, measured correlation −0.9989),
so a double-maker fill is rare by construction and one-sided fills require deliberate
lifecycle management. The completable-cost gate refuses a resting bid when
`price + best_ask(hedge) >= max_completable_pair_cost`; it does not itself trigger a taker
completion. It fires only when we hold none of the hedge token (otherwise `max_pair_cost`
governs), has no opinion when the hedge book has no ask (`book_health` already refuses
unreadable books), and does not replace `max_pair_cost` — the two bound different
questions and both must hold. During normal lifecycle management, the opposite maker
waits or escalates; the settlement fallback is the sole automatic taker-completion path.
Switch it off with `enforce_completable_pair_cost=false`; override the cap with env var
`HUNTER_COMPLETABLE_CAP`.

**Re-quote dead band** (`requote_dead_band`, default `0.03`). On that same run all 205
consecutive re-quotes changed price and every one reset queue position to zero (median
order lifetime 11.7 s against median 1058 shares ahead in queue). Measured suppression on
the run's own price series: 1c → 0%, 2c → 36%, **3c → 48%**, 4c → 58%, 5c → 68%. An order
resting within 3c of the desired price is kept, not cancelled and replaced; the band is
symmetric and independent of `price_eps` (sub-tick venue jitter) — the effective keep
tolerance is the larger of the two. Override with env var `HUNTER_REQUOTE_DEAD_BAND`.

A kept order rests at its own price, up to one dead band away from what the gate approved,
so it is **re-gated every cycle**: if its own resting price now fails the completable-cost
check, it is cancelled and this cycle's intent for that token is submitted in its place.
The re-gate is what makes the band safe; without it the band would be a hole punched
through the cap.

Fewer orders posted is the expected result of both rules, not a regression — wide markets
are declined rather than quoted into an empty book.

**Mid-hold band** (`MID_HOLD_BAND`, `0.02`, inclusive). While a token's mid sits
at or below its resting order's price plus the band, the order is held — never
cancelled, never re-quoted, never duplicated. It covers the price-driven
planner routes (`not_quoted` cancels, grace expiry, `lifecycle_replace`);
terminal refusals, hard stop, `lifecycle_cancel`, and explicit cancel sets
still cancel in band, and a missing, one-sided, or crossed book falls back to
today's behavior.
