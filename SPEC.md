# SPEC — #402: Full lifecycle quoting: read market state right, quote-fill-merge loop until resolved or discarded

Scope note: this file covers issue #402 only
(branch `i402/improve-full-lifecycle-quoting-read-market-state`).
It supersedes the #401 spec (merged work). Deleted or superseded when the
next Standard/Large issue writes its own.

## Problem (operator words)

A live BO3 series gets treated as decided, dead books get polled forever, and
a paper tape misses fills that really happen. (Tape miss itself is #401,
separate issue.)

Motivating episode, 2026-10-07, lol-fly-sr-2026-10-07 (BO3, FlyQuest vs
Shopify Rebellion): the rehearsal pulled the pair as not_quoted once DOWN mid
left the [0.20, 0.80] band, calling the market decided — while the series
still had two games to play. Poll spams 404 (no orderbook) every ~2.6s, most
likely against a resolved tennis market still tracked as partial.

## Goals

1. A BO3 series with games remaining and a live book is quoted, even when one
   side's mid leaves the [0.20, 0.80] band.
2. A resolved market's books are never requested again — by the Trader, the
   poll loop, or any secondary reader.
3. The loop ends ONLY on truly resolved or a named discard reason from one
   enumerated list; every stop names its reason in the log and the store.
4. A rehearsal exercises the full cycle on one eligible market: quote, paper
   fill, merge, re-quote.

## Acceptance criteria (from the issue)

- [ ] A BO3 series with games remaining and a live book is quoted; a resolved
      market's books are never requested (no 404 spam in a full rehearsal log).
- [ ] A rehearsal exercises the full cycle on an eligible market: quote, paper
      fill, merge, re-quote.
- [ ] The loop stops only on resolved or a discard reason from the single
      enumerated list; every stop names its reason in the log and the store.
- [ ] Targeted suites pass: tests/test_trader_loop.py tests/test_shadow_run.py.

## Established facts (verified from code, not assumed)

- Band refusal: `core_brain/quotes.py:328-329` refuses mid outside
  [0.20, 0.80] as "decided market" with no series awareness.
- Refusal classification: `TERMINAL_REFUSAL_MARKERS` + `_classify_refusal` at
  `core_brain/trader_loop.py:102-117`; hold grace `REFUSED_HOLD_GRACE_CYCLES = 3`
  (`:96`); `MarketEventRecord` already carries `reason_code`
  (`order_registry.py:706-716`) via `log_market_event` (`:1328`).
- Resolution reading: `parse_end_state` (`market_resolution.py:191`),
  `fetch_market_end_state` (`:305`), `sweep_market_resolutions` (`:687`,
  candidate set = markets that left the universe only; never resolves on a
  failed read, `:719-721`). UMA parsing exists (`:133-134`).
- No `core_brain/market_lifecycle.py` exists yet — the shared module is new.
- All 11 regression homes exist under `tests/` (loop, shadow, resolution,
  aged-out rescue, markout maturity, live-event discovery, in-play gate,
  live e2e lifecycle, dynamic risk caps, completable pair gate, uma gate).
- Follow-up linkage: #408 (mid-run UMA flip) explicitly joins this issue's
  discard list — the enum must be extensible, #408 is NOT built here.

## Edge cases

- Elapsed `endDate` on a row explicitly reporting `closed is False` +
  `acceptingOrders is True` stays unresolved (live series whose end date is
  kickoff); elapsed date with unknown `closed` still resolves.
- HTTP 404 on a book read is an availability failure, never a resolution —
  back off the token, confirm via the market-state endpoint, record only on
  confirmed end state.
- Unknown/unparseable/stale series evidence fails closed: the band applies as
  today.
- Late authenticated fills still record (`live_fill_engine.py` untouched).

## Out of scope

- Retuning 20-80 band values, settled-book 0.02/0.98 guard, execution band
  0.10-0.90, countdown, pair-cost, hard_block, completable-pair gate,
  dynamic caps, enforce flags (#377 gate untouched).
- #401 tape-miss fix (separate issue), `data/orders.db` surgery, any live
  position, automatic live merge (shadow rehearsal proves the cycle).
- #408 mid-run UMA re-validation (follow-up; consumes this issue's enum).
