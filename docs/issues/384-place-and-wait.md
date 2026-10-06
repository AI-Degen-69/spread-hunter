# Place-and-wait (Issue #384) — decision record

Operator decision 2026-10-06. No config value changed.

## Why chase-mid was wrong

Live trades showed both legs fill independently at their resting prices, with a
time gap between the two fills. UP + DOWN sums to about 1.00 no matter where mid
sits, so a resting profitable pair does not go bad when mid drifts. Chasing mid
with cancel + re-quote only threw away queue position — and the re-quoted order
landed at the back of a new level, where the same thing happened again.

## The rule

Every resting pair passed the pair-cost gate before placement, so every resting
order is profitable by definition — no re-check needed. A resting order whose
token still has an intent this cycle is KEPT at its own price. Point. No
tolerance check, no dead band, no drift comparison. The intent is suppressed
via `held_tokens`, so no duplicate is posted beside the kept order.

## What still cancels (exactly as before)

- No intent for the token this cycle (`not_quoted`) — nothing to hold for.
- The pair-cost re-gate fails (`regate_pair_cost`) — holding would carry a
  completable pair over `max_pair_cost`, and queue position is not worth a
  booked loss.

The dead band, queue hold, and direction hold stop firing. Their code and config
values (`dead_band`, `hold_queue_shares`, `hold_below_target`) are left in place
untouched for the follow-up issue to disposition.

## Limits

- A kept price can go stale against the book; the fill may come later or not at
  all. That is the trade accepted here: time in queue beats a fresh price.
- Nothing here adds a new cancel path. If a stale hold needs a timeout or a
  drift cap, that belongs in the follow-up.

## Follow-up

Separate issue for cancel conditions and risk on resting holds (timeout, max
drift, queue-abandon rules). This change only builds place-and-wait.
