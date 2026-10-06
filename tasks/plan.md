# Plan — Issue #384: place-and-wait (hold a profitable resting pair)

Branch: i384/resting-order-cancelled-requoted-market-approaches | Issue: #384
Size: Standard — 1 hold rule in plan_orders + tests + record.
## Why (operator decision 2026-10-06)

Live trades prove both legs fill independently at resting prices with a time
gap. UP+DOWN sums to ~1.00, so a resting profitable pair does not go bad when
mid drifts. The chase-mid logic was wrong from the start. Build place-and-wait
first. Cancel conditions and risk go in a SEPARATE issue — no new cancel path
here.

## Seams (verified live tree)

- trader_loop.py:179 tolerance = max(price_eps, dead_band), the chase trigger.
- trader_loop.py:223-273 per-order loop (not_quoted, regate, out-of-band, keep).
- trader_loop.py:301-309 submit loop (skips held_tokens).
- quotes.py:225-232 from-mid placement, NOT changed.
- config.py:852,889 dead_band 0.03, queue hold OFF 0.0, values NOT changed.
- tests: test_trader_loop.py helpers _intent/_open; asymmetric_hold stays green.
## Rule (operator decision 2026-10-06, simplified)

Every resting pair passed the pair-cost gate before placement, so every
resting order is profitable by definition — no re-check needed. The rule:
**a resting order whose token still has an intent this cycle is KEPT at its
own price. Point.** No tolerance check, no dead band, no drift comparison.
The intent is suppressed via held_tokens (no duplicate posted). Only two
things still cancel, exactly as today: no intent for the token (not_quoted),
or the in-band re-gate failing pair cost (regate_pair_cost). The dead band,
queue hold, and direction hold stop firing — left in code untouched for the
follow-up cancel-conditions issue to disposition.

## Tasks

### [x] T1 — RED tests: resting pair held through mid drift
Target: tests/test_trader_loop.py. Rest UP 0.70 + rest DOWN 0.27 (pair 0.97,
profitable by placement); intents drift to 0.66/0.25 (far out of band).
Expect: no cancel, no submit on either token. Second case: single resting
leg 0.70, intent drifted to 0.60 → still held, no cancel, no submit. Verify
FAIL first (today both cancel with price_moved).

### [ ] T2 — GREEN: hold-when-wanted in plan_orders
Target: trader_loop.py per-order loop. After the not_quoted check: if the
token has targets this cycle → keep at own price + held_tokens, skip submit.
The tolerance/out-of-band branch and the hold predicates stop firing for
wanted tokens (code left in place, bypassed). In-band re-gate still cancels
on pair-cost failure. not_quoted precedence untouched. Docstring lines only.

### [ ] T3 — Decision record
Target: docs/issues/384-place-and-wait.md. Why chase-mid was wrong (live
fills, UP+DOWN≈1.00), the rule, what still cancels, limits, follow-up issue
for cancel conditions. No config value change.

## Guardrails

- Suites test_trader_loop + asymmetric_hold green. No skipped assertions.
- No new deps, no config values, no quotes/cancel_report/orders.db/live.
- Checkpoints: T1 RED, T2 GREEN, T3 record + clean diff.


