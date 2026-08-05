"""Risk in dollars, not in shares.

Every per-market limit in this repo was share-denominated, and on a binary
market that is the wrong unit. The downside of one long share is the price
paid for it, so a share cap permits $72 of risk at 0.20 and $293 at 0.8152 --
it is loosest exactly where a wrong resolution costs most. Measured 2026-08-05:
three limits armed, none bound, and 85% of a -$223.32 unhedged float sat in
one market that had stopped at 233 shares against a 360-share cap.

Pure functions over an inventory and two book dictionaries, deliberately
separate from `strategy/quotes.py`. `_decide_quotes_rewards` already carries
six caps inline; adding five more there makes the binding constraint
unreadable. Keeping them here also lets replay call the gates directly without
standing up a quoting cycle.

Nothing in here does I/O, and nothing imports the quoting layer -- `inv` is
duck-typed on `Inventory` so the dependency runs one way only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

OTHER = {"UP": "DOWN", "DOWN": "UP"}


@dataclass
class BookHealth:
    """Why a book is or is not quotable.

    `depth_evaluated` is separate from `ok` on purpose: recorded history
    carries a mid but no depth, and a replay must be able to tell "depth
    passed" apart from "depth was never measured".
    """
    ok: bool
    reason: str = ""
    depth_evaluated: bool = True


def _shares(inv, side: str) -> float:
    return inv.up_shares if side == "UP" else inv.down_shares


def naked_side(inv) -> Optional[str]:
    """The heavier leg, or None when flat or exactly balanced.

    Balanced is not a small imbalance -- it is no imbalance. The hedged part
    of a position pays exactly $1.00 whichever way the market resolves, so
    only the excess is at risk and only the excess has a side.
    """
    up, down = inv.up_shares, inv.down_shares
    if up == down:
        return None
    return "UP" if up > down else "DOWN"


def naked_usd(inv, side: str) -> float:
    """Dollars at risk on this side: excess shares valued at average cost.

    Average cost, not the current mark. This is the amount that goes to zero
    on an adverse resolution, and it must not shrink because the mid already
    moved against us -- that would loosen the cap exactly when the position
    started losing.
    """
    excess = _shares(inv, side) - _shares(inv, OTHER[side])
    if excess <= 0:
        return 0.0
    return excess * inv.avg(side)


def risk_utilization(cfg, inv, side: str) -> float:
    """Naked dollars on this side as a fraction of the budget, clamped 0..1.

    A zero budget means the rule is unset, the same escape hatch every other
    cap in this config has. It must read as 0.0, never as infinite.
    """
    budget = cfg.max_naked_usd
    if budget <= 0:
        return 0.0
    return max(0.0, min(1.0, naked_usd(inv, side) / budget))


def book_health(book: dict, cfg) -> BookHealth:
    """Is this one token's book worth resting a bid into?

    Three rejections, cheapest and most certain first.

      * ONE-SIDED. No mid, nothing to quote against.
      * SETTLED. Either quote sits within `decided_price` of an end. The
        market has already decided: there is no spread left to capture, and
        the naked leg a fill would create is decided against us. Both ends are
        tested against both quotes -- the recorded failure was a 0.999 bid
        against a 0.001 ask, an inverted shape no single arm catches.
      * TOO WIDE / TOO THIN. A spread wider than `max_book_spread` puts the
        whole reward window inside it, so quoting there means being the most
        exposed order in the book. Summed bid depth under `min_book_depth_sh`
        means nothing is there to absorb an exit.
    """
    bb, ba = book.get("best_bid"), book.get("best_ask")
    if bb is None or ba is None:
        return BookHealth(False, "one-sided book", depth_evaluated=False)

    lo, hi = min(bb, ba), max(bb, ba)
    if lo <= cfg.decided_price or hi >= 1.0 - cfg.decided_price:
        return BookHealth(False, f"settled book {bb:.3f}/{ba:.3f}",
                          depth_evaluated=False)

    spread = ba - bb
    if spread > cfg.max_book_spread:
        return BookHealth(False,
                          f"book too wide {100*spread:.1f}c > "
                          f"{100*cfg.max_book_spread:.1f}c",
                          depth_evaluated=False)

    bids = book.get("bids")
    if bids is None:
        # Depth was never recorded. Passing is the honest reading -- refusing
        # would turn a gap in the data into a permanent block -- but the
        # caller is told the arm did not run.
        return BookHealth(True, "", depth_evaluated=False)

    depth = sum(bids.values())
    if depth < cfg.min_book_depth_sh:
        return BookHealth(False,
                          f"book too thin {depth:.0f}sh < "
                          f"{cfg.min_book_depth_sh:.0f}sh")
    return BookHealth(True, "")
