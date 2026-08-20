# ADR-0001: The live fleet intentionally skips a depth-in-USD gate

**Status:** Accepted (2026-08-21, issue #62)

## Context

The ranker (shared script, `scripts/rank_markets.py`) admits markets against a
depth-in-USD bar: permanent `select_min_top3_depth_usd = 1_000.0`, or a trial
bar from `HUNTER_DEPTH_TRIAL_USD` (currently $500). Trial-adopted markets are
tagged `trial_depth_usd` in `run/markets.json` and their markouts are watched
before the change is made permanent.

The simulation fleet (`strategy/fleet.py`) re-checks the depth gate itself via
`pair_books_allowed` (`strategy/selector.py`) and honors `trial_depth_usd` from
the spec — the U36f fix (2026-08-11). That fix mattered because the sim had an
**internal inconsistency**: its own book gate re-blocked markets the ranker had
just admitted at the trial bar.

The live fleet (`live/engine/live_fleet.py`) has no depth-in-USD gate. Its
continuous protection is `risk.book_health` (`live/engine/risk.py`), run on
**both legs** from the hard block on every `decide_quotes` call:

- `min_book_depth_sh = 200.0` shares of summed bid depth per token,
- `max_book_spread = 0.06` per token,
- one-sided / settled / crossed book arms.

`select_min_top3_depth_usd` and `select_min_top3_depth_usd_trial` exist in the
live config fork (`live/engine/config.py`) but are **never read by live engine
code** — they are ranker knobs mirrored by the fork.

## Decision

**The live fleet deliberately skips a depth-in-USD gate.** The division of
labour is:

- **Entry filter (USD depth, volume):** the ranker, re-read fresh every cycle
  from `run/markets.json` via `load_graduated_markets`.
- **Continuous protection:** `book_health`, both legs, every decision — share
  depth, spread, one-sided/settled/crossed arms.
- **Exposure bound:** the per-order caps (`MAX_ORDER_USD = 25`, `MAX_TOTAL_USD
  = 100`) keep any single quote small relative to even a thin book.

The U36f conflict cannot occur in the live fleet because there is exactly one
admission path: the ranker's row, then `book_health`. There is no second
USD-depth gate to disagree with the first.

`trial_depth_usd` therefore stays a **ranker-only** field. It is deliberately
not threaded through `market_feed.GraduatedMarket` / `live_fleet._market_specs`
— today it is dropped at the feed boundary, and that is the point.

## Consequences

- `select_min_top3_depth_usd` / `_trial` in `live/engine/config.py` remain,
  annotated ranker-only. They are fork mirrors of the root config the ranker
  reads; deleting them would be re-introduced on the next `refork.py`.
- No USD arm will be added to `book_health` without new evidence.
- Future architecture reviews and issue triage should not re-suggest porting
  `pair_books_allowed` into the live tree.

## Re-evaluation trigger (measured, not speculative)

Reopen this ADR if a live fill ever lands on a book whose **total USD depth
(bids × price) was below 3× the filled order's notional** at decision time.
That is the failure this gate would have caught — a fill the share-depth arm
approved but a USD arm would have refused. The markout sampler's fills ledger
is the evidence source. Until that measurement exists, the gate stays off.
