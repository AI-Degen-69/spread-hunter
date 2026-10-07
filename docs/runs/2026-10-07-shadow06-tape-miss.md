# Shadow-06 tape miss: why 0.26 DOWN printed 6,884 shares and credited 0 — 2026-10-07

[Issue #401](https://github.com/AI-Degen-69/spread-hunter/issues/401) asked
which reader dropped the seller prints at 0.26 DOWN on lol-fly-sr-2026-10-07
and why. This note answers from the incident store plus a read-only replay of
the public tape. No store was written; no rehearsal or trading command ran.

**Result in one line: the settlement reader `recent_trades` credited nothing
because the taker-view tape at 0.26 DOWN is 100% BUY-labelled mint flow —
6,884.43 shares in-window, 0.0 of them SELL — so the SELL-only filter rejects
every print by construction; the fills that did reach resting bids are visible
only in the full (`takerOnly=false`) read, which the reader never opens.**

## Setup

| | |
| --- | --- |
| Store | `data/06_shadow_prudent_07-10_13-20.db` (run id `shadow-06-prudent`, opened read-only via stdlib sqlite3 `mode=ro`) |
| Pair | `pair-45eed98903c9`: DOWN BUY 9 @ 0.26 (token `…23538129`) + UP BUY 9 @ 0.69, posted 10:20:32 UTC, cancelled `not_quoted` 10:37:11 UTC |
| Clock | store `ts` is UTC epoch seconds; filename `13-20` is local (UTC+3) = 10:20 UTC — consistent |
| Tape | `GET https://data-api.polymarket.com/trades?market=0x46e9…9800c`, both `takerOnly` modes, paginated; replayed ~75 min after the run, endpoint history assumed complete |
| Reader path | `shadow_run._default_traded_fn` → `markets.recent_trades` → `shadow_exec.settle_market` → `shadow_fills.credit_fills`; `recent_sell_flow` feeds the admission gate only and is **not involved** |

## The numbers

```
queue_marks DOWN @0.26: 102 rows, 10:20:42–10:37:10 UTC, traded=0.0 on every row
taker-view DOWN @0.26 in-window volume by side: BUY 6884.43, SELL 0.0
full-read-only DOWN @0.26 in-window volume by side: BUY 384.93, SELL 55.0
taker rows in-window: 150 over ~19 min (~100 polls) — no page overflow occurred
```

The smoking minute: 10:22:08→10:22:19 the 0.26 level drained 216.86 → 0.0
while `traded` stayed 0.0 and best slid 0.26 → 0.25.

## Cause

Primary (confirmed): this market's 0.26 DOWN flow arrives taker-labelled BUY.
Same-transaction rows show the shape — e.g. tx `0xa6090964` at 10:26:00 pairs
UP BUY 0.74 ×1085.78 with DOWN BUY 0.26 ×1140.78: the taker takes the UP leg
and the DOWN leg prints as a DOWN BUY. A SELL-only reader therefore sees a
busy level and credits zero, every poll, for the whole run.

Secondary (proven in code, fixed unconditionally): the dedup key
`(transactionHash, asset, timestamp, price, size)` omits side and is stored
before the side check (`markets.py:453-461`), so a same-identity BUY row can
suppress a SELL row; and the reader takes one 500-row page with no `offset`.
Overflow did not fire here (150 rows), but the single page is one busy market
away from blindness.

Maker-row semantics (confirmed enough to build on): rows carry **no role
field** — maker fills are identifiable only by identity-subtraction (present
in the full read, absent from the taker read). Maker-view-only DOWN @0.26
(BUY 384.93 + SELL 55.0) is resting-bid flow the fixed reader must count at
exact price. No complement-price mapping (`1 - price`): it would credit UP
ask-lifts to DOWN bids.

## What the fix does (built in T3, proved in T4)

1. Normalized `side` joins the identity key; rejected rows keep their own keys.
2. Bounded `offset` pagination after the `recent_sell_flow` pattern.
3. The full read minus taker-view identities contributes maker BUY rows at the
   resting token and exact price. Nothing else changes: contract, exact-price
   matching, and `recent_sell_flow` stay frozen.

## Follow-ups recorded (not built)

1. `seen` persistence across restarts, posting-time cutoff, failure-vs-empty-tape distinction.
2. The operator's live-fill timestamp would pin the exact minute our 10 shares
   matched; the store + tape already prove the mechanism without it.
