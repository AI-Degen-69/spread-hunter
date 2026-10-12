# Hard-stop fresh-poll exit — design

Date: 2026-10-12 · Branch: `i487/catalyst-tweaks-tournament-cockpit-and-run`
Status: approved §§1–4 by operator; spec for review before implementation.
Related: [hard-stop-stale-marks explanation](../../explanations/hard-stop-stale-marks.html),
`core_brain/single_buy_saver.py:exit_single_buy`, `core_brain/shadow_exec.py:_sell_into_bids`.

## Problem

Hard-stop exits (`decision.action == "hard_stop"`, `lifecycle_hard_stop`) book
exit prices far above the bid that fired the stop (H1: trigger 0.13 → booked
0.24; H2: trigger 0.15 → booked 0.83). The stop triggers on the fresh poll
book while the exit prices from a ~13-min-stale snapshot (bot's own
`queue_marks` prove both sides). Run impact under audit: reported +7.44
(merges +13.30 / exits −5.86); H1+H2 alone overstate by ~$7.2–7.7.

Live `exit_single_buy` step 3 already re-reads `client.get_order_book(heavy_token)`
inside the exit; shadow `_sell_into_bids` deliberately fills against the
PRIOR rotation's `queue_marks` snapshot (docstring: avoids booking "an exit
price the venue never gave"). The defect is therefore shadow-side pricing
staleness on the stop-loss route — the path that must sell *now* — plus the
close row keeping only the stale price, so no audit can replay the comparison.

## Approaches considered

**A. Re-poll inside the hard-stop exit (recommended, approved).** On
`hard_stop`, the exit path fetches the heavy-token book fresh *inside* the
exit call — after cancelling the opposite resting order, immediately before
the market SELL — and prices off that snapshot. Matches the operator's words
("poll the price to get it fresh, specially for stop-loss"). One code path,
shared live + shadow.

**B. Pass the decision bid through.** Thread the fresh `up_bid`/`down_bid`
the poll loop already computed into `exit_single_buy` and price from it. No
extra poll, but the decision book is itself seconds old by execution time,
and it papers over *why* the exit's own fetch went stale. Rejected.

**C. Staleness refusal.** Refuse when the exit book is older than N sec,
retry next cycle. Rejected — a stop must sell *now*, not next cycle.

## §1 — Trigger sequence (approved)

On `decision.action == "hard_stop"`: (1) cancel the opposite resting order
(already happens in the exit flow — kept, not added), (2) fresh
`get_order_book(heavy_token)` poll, timestamped, (3) market SELL sized by
registry × acceptable depth, (4) log the close. Live keeps its existing
cancel → re-read → sell order; shadow's `_sell_into_bids` gains a
`reason == "hard_stop"` branch that skips the `queue_marks` snapshot and
uses the fresh book — the snapshot path stays for every other route.

## §2 — Pricing (approved)

`min_price = floor(fresh_bid − MAX_SELL_SLIPPAGE)` with the existing
`MAX_SELL_SLIPPAGE = 0.02` (no new constant) and today's `_exit_fill_price`
semantics unchanged (venue-reported fill wins, else the fresh floor). The
close additionally stores the trigger bid and fresh bid so a future audit
can replay this exact comparison. Depth beneath the floor is still not
counted; thin books fill short rather than selling through.

## §3 — Staleness definition (approved)

"Fresh" = polled inside the exit call, not from any cache, recording, or
earlier cycle. If the poll returns no bid, today's
`PairExitRefused("No bid…")` behavior stands (nothing to sell into —
unchanged). No new threshold constant, nothing to tune. Aged-out rescue
path untouched (its marks verified fresh).

## §4 — Testing (approved)

Failing test first from the real exhibits: H1 (bid 0.13 at trigger, assert
exit prices ≈0.13 not 0.24) and H2 (bid 0.15, assert ≈0.15 not 0.83), plus a
no-bid refusal test. Then focused suites: `test_single_buy_saver`,
`test_aged_out_rescue`, `test_shadow_exec`, `test_order_registry`. Full
`python -m pytest -q` stays CI's job (repo rule: no full local repeat).

Scope: shared `exit_single_buy` + shadow SELL branch means live + shadow
fixed together; no historical-DB rewrite included.

## Spec self-review

- Placeholders: none — all constants, files, and test names pinned above.
- Consistency: §1's shadow carve-out matches §3's fresh definition (fresh =
  inside-exit poll; snapshot path explicitly bypassed only for hard_stop).
- Scope: single route (`hard_stop`), single pricing input (exit book
  source), audit columns additive. No unrelated refactor.
- Ambiguity: "shared live + shadow" could read as one function; clarified —
  live change is audit columns (+ guarantee the poll is inside the call),
  shadow change is the snapshot bypass. Same behavior contract, two touch
  points.
