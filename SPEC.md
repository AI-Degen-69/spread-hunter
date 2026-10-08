# SPEC — #419: Hold a resting order when the mid drops onto it

Scope note: this file covers issue #419 only
(branch `i419/trader-loop-hold-a-resting-order-and-never-requote`).
It supersedes the #408 spec (done work). Deleted or superseded when the
next Standard/Large issue writes its own.

## Problem (operator words)

The bot cancels or re-quotes a resting order when the market moves down
toward the bid. Every cancel + re-submit sends the order to the back of the
queue at a new level — on shadow run run-2809a7161de1 that happened 205/205
re-quotes. When the mid drops toward our bid, the order should sit at its
own price and wait for the fill.

## Goals

1. A resting order is never cancelled or requoted while
   `current_mid <= order.price + 0.02` — it holds and waits for the fill.
2. Safety cancels keep working: terminal refusals, hard stop,
   `lifecycle_cancel`, and the explicit cancel set still cancel in band.
3. Missing or crossed books change nothing (today's behavior).

## Acceptance criteria (from the issue)

- [ ] A resting order is never cancelled or requoted when
      `current_mid <= order.price + 0.02`
- [ ] Existing hold-and-wait (#384/#387) and no-intent-cancel behaviour
      unchanged — verified by the untouched existing tests plus the moved
      grace fixture below
- [ ] `python -m pytest -q tests/test_trader_loop.py
      tests/test_plan_orders_asymmetric_hold.py` passes (plus the new
      focused file for the changed module)

## Edge cases

- Equality (`mid == price + 0.02`) holds — float sums like `0.48 + 0.02`
  are not exact, so the compare carries `price_eps`.
- Token with no book (rotated away) still cancels — nothing to hold for.
- Crossed book (bid >= ask) or one-sided book: guard stands down.
- Order in both the replace and cancel sets: cancel wins.
- A held token gets no duplicate submit — neither ordinary nor
  lifecycle-pair intents.

## Explicit out of scope

- Fill crediting (`shadow_fills.py` — that was #417).
- Pricing mode (`objective="spread_capture"` stays pinned).
- `config.py`, `quotes.py`, `live_fill_engine.py`, `markets.py`.
- Retuning the 0.02 band or the retired 0.03 dead band.
