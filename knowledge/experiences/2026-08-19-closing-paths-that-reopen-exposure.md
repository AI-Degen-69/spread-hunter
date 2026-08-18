# 2026-08-19 — A closing path that re-opens the exposure it just closed

## What happened

While auditing the Stage 4 live-execution code before any real order was rested, a review
pass on `complete_pair` found that the function cancelled only the light leg of a pair
before crossing to complete the second side. The heavy leg's working orders were left
untouched. That means a heavy order can keep filling during and after the cross, so the
path whose entire purpose is to close naked exposure re-opens it immediately on exit.

The comparison that exposed it: `exit_naked_leg`, which handles the sibling case, quiets
*both* legs before acting. Two functions in the same module solving adjacent problems used
different safety preludes, and only one of them was right.

The same pass turned up two more defects in the same area. `load_pair` ranks legs by
matched size, so once the light leg fills past the heavy leg, the two silently swap roles
and downstream code reasons about the wrong side. And `light` is taken as `legs[1]` with
everything past index 1 discarded, so a `pair_id` carrying three or more token ids is
reduced to its two largest legs with no warning at all.

A related timing hole: `pair_cost` is computed from the pre-cancel `fill_cost` and the cap
is enforced there, but the BUY is sent after the cancel and after the venue is re-read.
Since the heavy leg is never cancelled, its average cost can move between the check and the
send, and the cross proceeds on a stale figure.

## Key learning

- When two functions in the same module handle adjacent cases, diff their preludes. The
  safe one documents what the unsafe one is missing. Asymmetry between siblings is a
  stronger bug signal than either function read on its own.
- A path named for *closing* something is not automatically a path that leaves you closed.
  Verify the post-state, not the intent encoded in the name.
- Any check-then-act sequence against a venue needs the state frozen between the check and
  the act. If a cancel is the thing that freezes it, a missing cancel silently invalidates
  every figure computed before it.

## Why it matters

These were caught in review, before a single live order rested. Each one costs real money
at up to 100% loss on the affected leg if it reaches production — an unhedged leg rides to
resolution. Review of the closing paths was worth more than review of the opening ones,
which is the opposite of where attention naturally goes.

## Tags

live-execution, code-review, race-conditions, polymarket, risk, spread-hunter
