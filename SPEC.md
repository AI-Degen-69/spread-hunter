# SPEC — #401: Shadow tape recorded zero sellers at a level that filled live

Scope note: this file covers issue #401 only
(branch `i401/fix-shadow-tape-recorded-zero-sellers-at-a-level`).
It is deleted or superseded when the next Standard/Large issue writes its own.

## Problem (operator words)

A live DOWN buy filled at 0.26 on lol-fly-sr-2026-10-07 (10 shares on-chain),
while the shadow rehearsal watching the same market recorded `traded=0.0` at
that level on every 10s check and paper-filled nothing. The tape reader missed
real seller flow.

Condition: `0x46e98430142d1aabb6806a196aac8e412f7225acf68aafed7f3d742662b9800c`.
Paper order DOWN 9 @ 0.26 (`pair-45eed98903c9`, run `shadow-06-prudent`) sat
behind a 744 queue, then was pulled as not_quoted when the market decided.

## Goals

1. Name the reader that dropped the prints and the exact filter that dropped them.
2. Fix that reader so prints at a resting level drain the queue / paper-fill.
3. Regression test replays prints-at-level and proves queue drain or paper fill,
   with no double-count on repeat polls.

## Acceptance criteria (from the issue)

- [ ] The miss is explained: which reader dropped the prints and why.
- [ ] A regression test replays prints-at-level and asserts the paper fill
      (or the queue drain) registers them.
- [ ] `python -m pytest -q tests/test_shadow_run.py` passes.

## Established facts (verified from code, not assumed)

- Settlement (fills + `queue_marks.traded`) comes from
  `core_brain/markets.py:recent_trades` via `shadow_run._default_traded_fn`,
  through `shadow_exec.settle_market` → `shadow_fills.credit_fills`.
- `recent_sell_flow` serves the placement admission gate only; not involved.
- Two proven defects in `recent_trades`, whatever caused this incident:
  (a) single 500-row page, no `offset` pagination
  (`core_brain/markets.py:439-441`);
  (b) dedup key `(transactionHash, asset, timestamp, price, size)` omits side
  and is added to `seen` before the side check, so a BUY/missing-side row can
  suppress the matching SELL (`core_brain/markets.py:453-461`).
- The incident store `data/06_shadow_prudent_07-10_13-20.db` IS in this
  checkout (the CodeRabbit plan assumed it was absent).

## Edge cases

- Sweep: one taker SELL crosses several bid levels; the public row may carry a
  single price, so no volume lands on exactly 0.26.
- Complementary mint: taker UP BUY ~0.74 matched our DOWN bid; the tape row is
  UP/BUY and the reader rejects it on side and token.
- Busy market: >500 rows between 10s polls overflow the single page.
- Missing/unreadable side field: currently skipped (conservative — keep).
- Maker-row semantics of `takerOnly=false`: UNCONFIRMED — attribution change
  ships only if Phase-1 evidence confirms it.

## Out of scope

- Live quoting, sizing, registry divergence (operator reconciles via poll).
- `data/orders.db` writes; live order placement.
- Known follow-ups, recorded not built: `seen` persistence across restarts,
  posting-time cutoff, failure-vs-empty-tape distinction.
- `recent_sell_flow`, `enforce_queue_clear_gate`, clear-time bar, pairing and
  merge behaviour, `queue_why` strings.
