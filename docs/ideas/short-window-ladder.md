# Short-window ladder (D11, issue #49) — design before code

**Date:** 2026-09-13 · **Status:** proposal, no money path touched · **Issue:** #49

## The idea

On the BTC/ETH 5-minute and 15-minute up/down series, rest several bids per
side around mid = 0.50 in the first seconds after a market opens, instead of
one bid per side. Each rung is the same size, every rung shares one
`pair_id`, and any two opposite fills still merge back into $1.00. The edge
hypothesis is that these series open with the widest spreads and the thinnest
books of anything on the venue, so multiple price levels catch fills that a
single price misses — more completed pairs per market, at the same
sub-$1.00 discipline.

This is **not** a directional strategy as first drawn: a completed pair
(any UP fill at any rung + any DOWN fill at any rung, combined cost < $1.00)
still merges to exactly $1.00. The directional risk lives only in the
one-leg residue, which is the risk the repo already carries today via
`single_buy_saver` — just arrived at more often, because more rungs means
more chances of one side filling without the other.

## Why the three open questions have answers on paper first

The owner's comment on #49 is right: this is a new strategy, not a defect,
and the three questions change the design, not just the parameters.

### Q1 — Does it share the bankroll?

**Recommendation: yes, through the existing Dynamic Caps, with the ladder
treated as more orders, not a second budget.**

Reasoning:

- The ladder's rungs are the same instrument, same wallet, same merge
  identity as the current single-pair path. Giving it a separate allocation
  would mean building a second accounting domain (which portfolio value does
  a cap read, which registry rows belong to whom) for no safety gain — the
  caps already bound the wallet, which is the thing that can actually lose
  money.
- Concretely: `order_risk_pct` (25%) applies per rung as it does per order
  today. A 4-rung ladder at equal size means each rung is sized so that the
  *worst case* — both sides fully filled across all rungs at the worst
  prices — still sits inside `bankroll_ceiling_pct` (90%). The per-leg and
  total notional checks in `shadow_exec.py`/`venue.py` already enforce this
  per call; the ladder passes all rungs in one call, so one guard covers the
  whole ladder.
- What does change: the **single-buy rescue** cost. A ladder that fills one
  side across two rungs needs a bigger completion buy than a single leg, so
  `naked_risk_pct` (6%) must be checked against the *blended* unfilled side,
  not per rung. This is an implementation requirement to write into the
  plan, not a new cap.

Rejected alternative — a separate ladder budget: it adds a second source of
truth for account value, splits the KPI picture, and the failure mode it
would protect against (ladder eats the spread hunter's capital) is already
covered by the ceiling. Complexity without a new guarantee.

### Q2 — What is the exit?

**Recommendation: keep the merge identity, and treat the one-leg residue
exactly as the code does today — single-buy exit — with a shorter, explicit
window because the market itself is short.**

- A completed pair exits by merge, as always. No change, no new risk.
- A one-leg residue exits through `single_buy_saver`, which already groups
  all orders on a token into one blended leg — the ladder's multiple rungs
  on one side collapse into that leg for free. The only new knob is
  `ladder_exit_window_sec`: on a 5-minute market the existing exit timer
  (tuned for longer horizons) can eat most of the market's life. Default
  proposal: **45s** on the 5-min series, **120s** on the 15-min series —
  both configurable, both to be revisited with replay-probe data.
- The owner's framing — "exits by being right about direction, or by time" —
  is the honest description of the *residue only*. The design goal is that
  the residue is a small, capped, timer-bounded remainder of a mostly-paired
  book, not a position anyone chose. If the residue ever becomes the
  *expected* outcome (more than, say, half of ladder attempts ending
  one-legged), the strategy is mis-shaped and the ladder offsets should be
  tightened — that ratio is a KPI to watch from day one, not a vibe.

What this design deliberately does **not** do: hold unpaired inventory to
resolution as a directional bet. That path exists in the code only as the
fallback it already is, and #49's plan correctly keeps it there.

### Q3 — What proves it before it trades?

**Recommendation: three gates, in order, each cheap and each refusable.**

1. **Replay probe (paper, offline).** The `scripts/` read-only harness from
   #49's plan: replay recorded tapes across the series window, simulate
   candidate ladder shapes and exit policies, and answer Q1–Q3 of the issue
   (how many rungs, what exit window, what order lifetime) with data. This
   is the `range-legging.md` lesson applied: that idea *looked* good until
   the stranded legs were scored at their real resolution value. The probe
   must score one-leg residues at real resolution outcomes, not at zero,
   or it will flatter the ladder exactly the way the range-legging backtest
   was first flattered.
2. **Shadow rehearsal (live book, no signer).** Full-loop shadow runs on the
   crypto series via the `markets_fn` seam. Shadow numbers are rehearsal —
   fill rates there are model outputs — but they prove the mechanics: rungs
   rest under one `pair_id`, fills attribute oldest-first, one-leg exits
   fire inside the window, nothing double-counts. This is the gate the
   shadow path exists for.
3. **Live probe, venue minimum.** Only after 1 and 2 agree, a bounded live
   session at the smallest sizes the venue allows, inside Dynamic Caps,
   with the undo commands pre-approved as always. Success criterion stated
   in advance: blended pair cost across the session at least 1c below the
   single-pair baseline on the same series, and the one-leg residue rate
   under the mis-shape threshold above.

**The go/no-go number the probe must produce:** expected profit per market =
P(complete pair) × (1.00 − blended pair cost) − P(one leg) × E(residue loss).
If the probe cannot estimate both terms from tape data, the ladder does not
go live — an unmeasurable edge is not an edge.

## What stays untouched

- `fetch_live_market` and the rollover loop — a new discovery function
  returns upcoming series markets; the existing path is byte-identical.
- The screener (`scripts/filter_markets.py`) — the ladder reaches crypto
  series through the shadow `markets_fn` seam only.
- The single-pair quote path — a separate, switch-gated ladder decision
  function; with `ladder_mode` off, quoting is unchanged, provably.
- Dynamic Caps arithmetic — reused, not forked (see Q1).

## Open items for the operator

- Confirm the residue budget framing in Q2 (merge identity preserved,
  residue timer-bounded) is the intended risk posture for this strategy.
- Confirm the live-probe success criterion in Q3, or set a different one.
- The replay probe needs recorded tape data for the BTC/ETH series; decide
  whether to collect a fresh window or reuse what `markout.py` already
  stores.

## Verdict

Worth building **after** the replay probe returns numbers. The mechanics are
low-risk (they reuse the pair/merge/exposure machinery this repo has already
hardened) but the edge is unproven, and the cheapest way to find out it
doesn't exist is offline, on paper, before any code lands on the money path.
