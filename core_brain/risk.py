"""Risk in dollars, not in shares.

Every per-market limit in this repo was share-denominated, and on a binary
market that is the wrong unit. The downside of one long share is the price
paid for it, so a share cap permits $72 of risk at 0.20 and $293 at 0.8152 --
it is loosest exactly where a wrong resolution costs most. Measured 2026-08-05:
three limits armed, none bound, and 85% of a -$223.32 unhedged float sat in
one market that had stopped at 233 shares against a 360-share cap.

Pure functions over an inventory and two book dictionaries, deliberately
separate from `strategy/quotes.py`. `_decide_quotes_from_mid` already carries
six caps inline; adding five more there makes the binding constraint
unreadable. Keeping them here also lets replay call the gates directly without
standing up a quoting cycle.

Nothing in here does I/O, and nothing imports the quoting layer -- `inv` is
duck-typed on `Inventory` so the dependency runs one way only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core_brain import config

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


def size_for(cfg, inv, side: str, price: float, pair_price: float | None = None) -> int:
    """How many shares to rest on `side`, given what is already at risk.

    R5. `hard_block` is a step, and a step is the wrong shape for a limit: at
    $119.99 of a $120 budget it permits the FULL 120 shares, and at $120.00 it
    permits none. The largest order of a market's life therefore arrives with
    one cent of headroom left -- and on lol-maz-mg1, where 233 shares arrived
    in two fills, that is not a hypothetical shape. This walks the size down
    instead, reaching zero AT the budget rather than one order past it.

    Three terms, applied in order:

      * THE TAPER, base * (1 - u)^2 with u = `risk_utilization`. Quadratic and
        not linear, because linear is still too generous near the top: halfway
        to the budget a linear rule rests 60 of a 120-share base against the
        $60 that remains, so one order could consume most of what is left.
        Quadratic rests 30.
      * THE REMAINDER CAP, (budget - naked) / price. The taper bounds the SIZE
        and not the dollars the size costs, and those differ by a factor of the
        price -- the same confusion of units that made the old share cap $72 of
        risk at 0.20 and $293 at 0.8152. With it, one order can never buy more
        exposure than the budget it is walking toward.
      * THE FLOOR at `min_quote_shares`. rewardsMinSize is 50: below it an
        order earns no score at all while still buying inventory, so the honest
        answer is 0 -- post nothing -- rather than a token order. This is what
        makes the tail of the ladder terminate instead of trickling.

    The light side is never tapered (R4). It is the only resting order that
    FLATTENS the position, and slowing it as exposure peaks is precisely the
    failure the share cap produced: it stopped the heavy side and then had no
    authority left, freezing the market at maximum exposure.
    """
    # `quote_shares == 0` is the allocator defunding this market, in EVERY
    # sizing mode. It is not a sizing instruction and must never be read as
    # one: reading 0 as "size from dollars" is how 17 defunded markets kept
    # posting 50-share orders. Defunded means silent.
    if cfg.quote_shares <= 0:
        return 0

    if getattr(cfg, "size_mode", "shares") == "dollars":
        # Sized in couple shares from the Owner's allocation rule:
        # N = floor(couple_allocation / (up_price + down_price))
        # N = max(N, venue_min_size)
        if price <= 0:
            return 0
        opp_avg = inv.avg("DOWN" if side == "UP" else "UP") if inv is not None else 0.0
        if pair_price is not None and pair_price > 0:
            eff_pair = pair_price
        elif opp_avg > 0:
            eff_pair = price + opp_avg
        else:
            eff_pair = min(1.0, 2.0 * price if price <= 0.5 else 1.0)
        if eff_pair <= 0:
            return 0
        couple_alloc = config.couple_allocation_usd(cfg)
        raw_n = int(couple_alloc / eff_pair)
        if raw_n < cfg.min_quote_shares:
            return 0
        base = max(raw_n, cfg.min_quote_shares)
    else:
        base = max(cfg.quote_shares, cfg.min_quote_shares)

    max_shares = getattr(cfg, "max_quote_shares", None)
    if max_shares is not None and max_shares > 0:
        base = min(base, int(max_shares))

    # The venue floor applies to the derived size exactly as it applies to the
    # fixed one: an order below it cannot be posted, so the honest answer is
    # no order rather than a token one.
    if base < cfg.min_quote_shares:
        return 0

    if naked_side(inv) != side:
        if getattr(cfg, "strict_paired_inventory", True) and naked_side(inv) is not None:
            excess = abs(inv.up_shares - inv.down_shares)
            if excess > 0:
                deficit_sz = int(excess)
                if deficit_sz < cfg.min_quote_shares:
                    return 0
                return min(base, deficit_sz)
        return base

    if getattr(cfg, "strict_paired_inventory", True):
        return 0

    size = base * (1.0 - risk_utilization(cfg, inv, side)) ** 2

    if cfg.max_naked_usd > 0:
        if price <= 0:
            # The remaining budget buys an unbounded number of shares at a
            # price of zero. That is the one reading that must never reach the
            # venue, so a degenerate book quotes nothing.
            return 0
        remaining = max(0.0, cfg.max_naked_usd - naked_usd(inv, side))
        size = min(size, remaining / price)

    if size < cfg.min_quote_shares:
        return 0
    return int(size)


def skew_offset(cfg, inv, side: str) -> float:
    """How far to move this side's quote, in price units. Positive = away.

    The spring that used to be wound by imbalance against a fixed share ramp
    and is now wound by dollars. 100 naked shares is $85 of downside at 0.85 and $15
    at 0.15; the share-denominated spring answered both with the same push,
    which is why it was still ramping on lol-maz-mg1 -- 233 shares against a
    240-share ramp -- when the position was already fully built and $190 was at
    stake.

    Heavy side out, light side in, by the same magnitude: pushing the heavy
    side away without pulling the light side toward mid would just make us
    quote worse, rather than making the FLATTENING order the one that fills.
    Zero for a flat or balanced book -- the hedged part pays exactly $1.00
    whichever way the market resolves, so there is nothing to lean against.
    """
    naked = naked_side(inv)
    if naked is None:
        return 0.0
    push = cfg.max_skew * risk_utilization(cfg, inv, naked)
    return push if side == naked else -push


@dataclass
class BandRisk:
    """Two answers to two different price risks, from one weight.

    Kept as a pair rather than folded into one number because they act on
    different levers -- variance is answered with size, magnitude with offset --
    and a single blended factor could not express "quote smaller AND further"
    without one of the two silently cancelling the other.
    """
    size_mult: float
    extra_offset: float


def band_risk_factor(cfg, price: float) -> BandRisk:
    """How much to shrink and how much further out to sit, at this price.

    R6. Every other rule in this module prices a position in DOLLARS and none
    of them notices where in the 0..1 range the fill lands. Two risks live
    there and they point in opposite directions:

      * VARIANCE, answered with SIZE. The payout is Bernoulli, so per-share
        variance is p(1-p) -- 0.2500 at 0.50 against 0.2100 at 0.30/0.70 and
        zero at the ends. The coin flip is where a fill says least about the
        outcome. The weight `w` below is a linear taper of that hump, reaching
        zero at `coinflip_halfwidth` (0.20 = half the price band, so the
        treatment stops exactly where the band stops permitting quotes).
      * MAGNITUDE, answered with OFFSET. The downside of one long share IS the
        price paid for it, so 0.70 buys more than twice the risk of 0.30 for
        the same share. `price_risk_widen * price` demands a better price where
        a share costs more; it is monotonic in price and carries no hump,
        which is what lets the two terms be told apart at 0.68 versus 0.32.

    The coin-flip term appears in BOTH outputs on purpose. Size alone would
    leave us resting at the same price in the least readable market on the
    board; offset alone would keep full size against the widest variance. The
    offset terms share one scale (`price_risk_widen`) because `w` and `price`
    are both dimensionless 0..1 weights -- a second magnitude knob would be a
    number with no independent evidence behind it.

    Zero `coinflip_halfwidth` unsets the variance term, the same escape hatch
    every other tunable here has, and is also the one value that would divide
    by zero. The multiplier floors at 0: a negative multiplier would flip the
    sign of a size, and "quote nothing" is the correct limit of "quote less".
    """
    hw = cfg.coinflip_halfwidth
    w = 0.0 if hw <= 0 else max(0.0, 1.0 - abs(price - 0.50) / hw)
    return BandRisk(
        size_mult=max(0.0, 1.0 - cfg.coinflip_size_cut * w),
        extra_offset=cfg.price_risk_widen * (w + price),
    )


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

    # CROSSED. A bid at or above the ask is not a wide book, it is an
    # impossible one -- stale data, or a venue mid-update. Refused on its own
    # arm rather than left to the width test below, because width cannot catch
    # it either way round: the raw `ba - bb` goes NEGATIVE and a negative
    # number is never greater than the cap, while the absolute gap of a
    # crossed 0.60/0.55 is 5c, comfortably inside the 6c bar. The recorded
    # 0.999/0.001 case was only ever stopped because the settled arm fires
    # first; a crossed book in the middle of the range had nothing to stop it.
    if bb >= ba:
        return BookHealth(False, f"crossed book {bb:.3f}/{ba:.3f}",
                          depth_evaluated=False)

    # `hi - lo` rather than `ba - bb`, so this arm reads the same normalised
    # pair the settled arm above does. With the crossed case already refused
    # the two are equal; keeping the normalised form means a future reordering
    # cannot silently reintroduce the negative-width hole.
    spread = hi - lo
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


def completable_pair_block(cfg, price: float,
                           hedge_ask: Optional[float]) -> Optional[str]:
    """Why a bid at `price` must not rest given what the hedge leg costs to TAKE.

    `max_pair_cost` asks what a pair costs if both legs fill as resting bids.
    This asks the question the tape actually answers: one leg fills as maker,
    and the other has to be bought at its ask. On a binary market the two are
    not close. UP + DOWN = 1.00 is near a mechanical identity, so the legs are
    anti-correlated -- -0.9989 across 98 mid steps on shadow run
    run-2809a7161de1, 97 of them opposite sign -- and a double-maker fill is
    rare by construction rather than by bad luck.

    The cost of not asking it is on the record. Every one of that run's 209
    orders carried `max_pair_cost_at_post = 0.995`, one distinct value, from a
    both-maker check that never tested `price + hedge ask`. The bot was resting
    pairs that were profitable only under the rarest outcome available to it.

    `hedge_ask` of None or 0 is NO OPINION, not a refusal. `book_health` already
    rejects a book it cannot read, in its own words; a second refusal for the
    same condition would leave the operator two different reasons for one fact.

    `>=` not `>`: the pair pays exactly $1.00, so a completable cost AT the cap
    is a booked loss or a fill slot worth nothing. A ceiling reached, not
    approached -- the same reading every other cap in this module takes.
    """
    if not getattr(cfg, "enforce_completable_pair_cost", True):
        return None
    cap = float(getattr(cfg, "max_completable_pair_cost", 0.0))
    if cap <= 0:
        return None
    if hedge_ask is None or float(hedge_ask) <= 0:
        return None
    cost = round(float(price) + float(hedge_ask), 4)
    if cost >= cap:
        return (f"completable pair {price:.3f}+{float(hedge_ask):.3f}="
                f"${cost:.4f} >= ${cap:.3f} cap -- the second leg cannot be "
                f"bought at a profit")
    return None


def queue_clear_block(cfg, side: str, price: float, queue_shares: float,
                      reachable_shares: float,
                      window_min: float) -> tuple[bool, str]:
    """Why a new bid at `price` should not rest, from the queue ahead of it.

    The question is "can this ever fill", not "what is the fill probability":
    `queue_shares` is the book depth resting AT our price right now, and
    `reachable_shares` is taker SELL volume on that token at or below our price
    inside `window_min`. Minutes to clear = ahead / (reachable / minutes) -- the
    wait before a seller could even reach us. Cancels ahead of us are not
    modelled, so it is an UPPER BOUND on the wait, which is the honest direction
    for a gate whose answer is "do not bother".

    This is deliberately NOT a second copy of the rule. The ranker's maker-queue
    bar has measured exactly this since 2026-08-25
    (`scoring.selector.queue_minutes_at` + `maker_queue_allowed`), including the
    two cases that are easy to get wrong: no trade at our price is `inf` (never
    clears) rather than missing data, and `enforce=False` is record-only. Two
    copies of one rule drift, so this adapts that pair, imported inside the call
    so the module import graph is exactly what it was before this gate existed.

    Returns `(allowed, why)`. `why` is EMPTY when the queue clears and otherwise
    carries the shared `maker queue: ...` reason with the leg appended, so the
    string stands on its own in a log line or a cycle event.
    `max_queue_clear_minutes` of 0 disables the rule, and a zero queue is allowed
    before any division -- an empty level needs no time to clear, whatever the
    tape says.
    """
    from scoring.selector import maker_queue_allowed, queue_minutes_at

    bar = float(getattr(cfg, "max_queue_clear_minutes", 0.0) or 0.0)
    if bar <= 0:
        return True, ""
    ahead = float(queue_shares)
    if ahead <= 0:
        return True, ""
    minutes = queue_minutes_at(
        ahead, float(reachable_shares), float(window_min))
    allowed, why = maker_queue_allowed(
        minutes, bar,
        enforce=bool(getattr(cfg, "enforce_queue_clear_gate", False)),
    )
    if not why:
        return True, ""
    return allowed, f"{why} ({side} @ {float(price):.4f})"


def hard_block(cfg, inv, side: str, price: float,
               own_book: dict, hedge_book: dict) -> Optional[str]:
    """Why a NEW bid on `side` must not rest, or None if it may.

    One function rather than five inline branches, because the caller has to
    report a single reason and the operator reading it has to be able to tell
    which limit bound. `price` is the provisional resting price, read by the
    last two arms.

    Five arms, cheapest and most certain first, so the reason names the
    rejection that is hardest to argue with:

      * HEDGE SIDE (KTD2). A bid is safe only if the position it might create
        can be closed, and on a binary market it is closed by buying the OTHER
        token. A pristine own book says nothing about that: wta-kalinsk-kessler
        finished quoting 0.999 bid against a 0.001 ask, and a fill on the
        healthy leg would have left a position no price could unwind.
      * OWN SIDE. The book we would rest into, on the same three arms.
      * DOLLAR CAP. Naked cost on this side at or past `max_naked_usd`.
      * PRICE BAND (R7). Outside `price_band_low..price_band_high` the spread
        has collapsed toward one tick on an outcome the market already
        considers settled, so there is nothing to capture -- while a wrong
        resolution still costs the full $1.00. Reported AFTER the dollar cap
        on purpose: when both bind, what is already at stake is the more
        useful reading than what a new fill would be worth.
      * PAIR COST (R7). `price + inv.avg(other) >= max_pair_cost`. The pair
        pays exactly $1.00, so a pair assembled above that is a booked loss,
        not a risk.

    The last two are not new rules. Both have existed in `strategy/quotes.py`
    since the beginning and neither has ever executed: they sit in the legacy
    branch of `decide_quotes`, below the line where `_decide_quotes_from_mid`
    returns, so on the path the fleet actually runs they were unreachable.
    The telemetry reads exactly as absent rules would -- fills averaged 0.8152
    against a nominal 0.30-0.70 band, and wta-kalinsk-kessler bought 14 pairs
    at $1.0200 each against a $0.995 cap.

    R4 rides above four of the five: an order that REDUCES exposure is never
    blocked. The light side is the only resting order that flattens a
    position, so refusing it would freeze the market at maximum exposure with
    no route back down -- the failure mode the share cap actually produced,
    where it stopped the heavy side and then had no further authority. The
    emergency stop-loss and the merge path reduce exposure too; neither
    reaches this function.

    The PAIR-COST arm is the exception, and deliberately: it is not an
    exposure bound. A light-side fill that assembles the pair at/over
    `max_pair_cost` is a booked loss on an instrument that pays exactly
    $1.00, and "reduces exposure" does not make a guaranteed loss
    acceptable. It is checked before R4 returns, so the light side is exempt
    from the exposure arms only -- never from the cap that exists to stop a
    pair that cannot be profitable.
    """
    other = OTHER[side]

    # THE PAIR-COST CAP is evaluated BEFORE enable_hard_blocks so it always
    # rejects pairs at or above max_pair_cost, regardless of the flag.
    # R4 exempts the light side from the exposure arms because buying the
    # light side REDUCES exposure -- but the pair-cost arm is not an exposure
    # bound. A light-side fill that assembles the pair at/over `max_pair_cost`
    # is a booked loss on an instrument that pays exactly $1.00; "reduces
    # exposure" does not make a guaranteed loss acceptable. The old order
    # skipped this arm entirely for the light side, which is how a light-side
    # quote chased to $0.92 against a $0.20 held leg produced the $1.12 pair
    # seen in production (and how the paper run's own docstring records buying 14
    # pairs at $1.0200 against a $0.995 cap). For the heavy side the arm still
    # reports LAST, preserving the documented "most useful reason first" order.
    other_avg = inv.avg(other)
    pair_cost_block = None
    if other_avg > 0 and (price + other_avg) >= cfg.max_pair_cost:
        pair_cost_block = (
            f"pair {price:.3f}+{other_avg:.3f}=${price + other_avg:.4f} "
            f">= ${cfg.max_pair_cost:.3f} cap -- pays exactly $1.00")

    if _shares(inv, side) < _shares(inv, other):
        return pair_cost_block

    # enable_hard_blocks gates only the exposure and price-band arms below
    if not getattr(cfg, "enable_hard_blocks", True):
        return None

    hedge = book_health(hedge_book, cfg)
    if not hedge.ok:
        return (f"hedge token {other} not tradeable ({hedge.reason}) -- "
                f"a fill here could not be closed")

    own = book_health(own_book, cfg)
    if not own.ok:
        return f"own book not quotable ({own.reason})"

    # 0 disables the rule, the same escape hatch every other cap in this config
    # has. `>=` not `>`: the budget is a ceiling reached, not approached.
    if cfg.max_naked_usd > 0:
        naked = naked_usd(inv, side)
        if naked >= cfg.max_naked_usd:
            return (f"${naked:.2f} naked >= ${cfg.max_naked_usd:.0f} "
                    f"budget -- not adding")

    # THE PRICE BAND. Inclusive at both ends, mirroring `_in_band` -- a rule
    # that refused its own stated endpoints would quietly be narrower than the
    # config says. Switchable for the same reason it always was: a gate that
    # cannot be turned off cannot be attributed a result.
    if cfg.enforce_price_band and not (
            cfg.price_band_low <= price <= cfg.price_band_high):
        return (f"{price:.3f} outside band {cfg.price_band_low:.2f}-"
                f"{cfg.price_band_high:.2f}")

    # PAIR COST, for the heavy side. `other_avg > 0` guards it, exactly as
    # the legacy branch does: a zero average means we hold none of that token,
    # not that the hedge is free, and without the guard every opening bid
    # would read as a sub-$1.00 pair. `>=` not `>`, same as every other cap
    # here.
    return pair_cost_block
