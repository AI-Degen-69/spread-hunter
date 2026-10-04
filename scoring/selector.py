"""Hard market eligibility rules shared by the ranker and live paper fleet.

The selector is deliberately pure. It does not fetch markets or write state; it
only answers whether metadata and a pair of order books satisfy the strategy's
entry contract. Keeping the rule here prevents the ranker and the fleet from
drifting apart when a stale ``runtime/markets.json`` is still on disk.
"""
from __future__ import annotations

import math

import re
from typing import Iterable, Mapping


# Normalize separators before matching so both human titles ("Game 1") and
# venue slugs ("game1", "in-play", "map_2") are rejected.
_BLOCKED_RE = re.compile(
    r"(?<![a-z0-9])(?:game|map|set)[\s_-]*[12](?![a-z0-9])|"
    r"\b(?:game|map|set)\s+winner\b|"
    r"\bround\b|\blive\b|\bin[\s_-]*play\b|\bhandicap\b",
    re.IGNORECASE,
)
_PRIMARY_RE = re.compile(
    r"\b(?:moneyline|main\s*line|outright|match\s*winner)\b",
    re.IGNORECASE,
)
_MACRO_RE = re.compile(
    r"\b(?:politic(?:s|al)?|macro|econom(?:y|ics)|election|government|"
    r"president|senate|congress|governor|inflation|interest\s*rate|"
    r"gdp|federal\s*reserve|fed|tariff|war|prime\s*minister)\b",
    re.IGNORECASE,
)
_SPORTS_SERIES_RE = re.compile(
    r"\b(?:mlb|nfl|nba|nhl|ufc|atp|wta|itf|fifa|premier\s*league|"
    r"esport|esports|dota|league\s*of\s*legends|counter[- ]strike|"
    r"valorant|cs2|soccer|tennis|baseball|basketball|football|hockey)\b",
    re.IGNORECASE,
)

# A group label refuses only when it names the fragment: a spread/handicap
# line, a game/map/round/set number, an over/under or totals number, or a
# bare numeric price band. Bare country, candidate, party, team, and date
# labels ("Brazil", "Donald Trump", "December 31, 2027") are main-line
# event names the venue groups under -- refusing them emptied the eligible
# universe in Oct 2026 (95 label refusals on one rank) while the fragments
# they were meant to block stayed refused by _BLOCKED_RE anyway.
_FRAGMENT_LABEL_RE = re.compile(
    r"spread|handicap|\bO/U\b|\bover/under\b|"
    r"\b(?:over|under)\s+\d+(?:\.\d+)?\b|"
    r"\b(?:game|map|round|set|quarter|period|hole|inning)\b[\s_-]*\d|"
    r"\btotal\b.*\d|"
    r"^[↑↓<>]?\s*<?[\d,]+(?:\.\d+)?(?:\s*-\s*[\d,]+(?:\.\d+)?)?\)?$|"
    r"(?<!\d)[-+]\d[\d,]*(?:\.\d+)?\s*\)?\s*$",
    re.IGNORECASE,
)


def _is_fragment_label(label: object) -> bool:
    """Whether a venue group label names a submarket fragment."""
    text = str(label or "").strip()
    return bool(text) and bool(_FRAGMENT_LABEL_RE.search(text))


def _text(*values: object) -> str:
    return " ".join(str(v or "") for v in values).strip()


def identity_allowed(title: object = "", slug: object = "",
                     category: object = "", market_type: object = "",
                     market_group: object = "", series_title: object = "",
                     event_title: object = "",
                     require_primary: bool = True) -> tuple[bool, str]:
    """Return whether a market is a permitted primary/main-line instrument.

    A head-to-head "A vs B" title is the shape that hides Game/Map/Round
    submarkets (a BO5 has a Game 1, Game 2, ... alongside its match winner), so
    that shape must confirm itself against a known league/series keyword before
    it is admitted. A standalone event question ("Strait of Hormuz traffic
    returns to normal by August 31?") has no head-to-head to fragment into a
    submarket -- there is no "Game 1" version of it -- so it only needs to clear
    the blocked-keyword and no-group-label checks. Requiring a topic keyword
    for that shape too was rejecting real, liquid, non-sports questions (audited
    2026-08-06: a $600K/24h geopolitical market) for lacking a hardcoded macro
    word, while contributing almost nothing against the reward-market universe
    it was meant to protect (audited same day: of 131 identity-only rejections,
    1 cleared the volume gate and 0 cleared volume+horizon). The liquidity,
    depth, and spread gates downstream are the real risk control for this
    shape, not a keyword whitelist.

    `require_primary=False` keeps every REJECTION arm and drops only the
    positive confirmation the matchup shape has to earn. It exists for one
    caller: a `runtime/markets.json` written before this module did, whose entries
    carry a title and a slug and none of the five metadata fields the sports
    and macro keywords are read from. Judged normally, every such entry fails
    -- "Yankees vs Red Sox" has no league word in its own title -- so the live
    fleet would cancel its quotes on the entire universe until the next rank
    rewrote the file. That is a gap in the DATA, not evidence about the market,
    and answering it with a block inverts what the flag is for.

    What still runs on that path is the part that needs no metadata: the
    blocked-keyword arm reads title and slug, so Game 1, Map 2, Round, live,
    in-play and handicap are refused exactly as before. Only the "confirm this
    matchup against a known league" step is skipped, and the market still
    answers to the volume, depth, and spread gates downstream, which do not
    depend on the ranker's vocabulary. The window is bounded by the re-rank
    interval (600s), after which the file carries the fields and the full rule
    applies again.
    """
    title_slug = _text(title, slug)
    all_text = _text(title, slug, category, market_type, market_group,
                     series_title, event_title)
    if _BLOCKED_RE.search(all_text):
        return False, "blocked dynamic/submarket keyword"
    if _PRIMARY_RE.search(_text(market_type, market_group)):
        return True, ""
    if _MACRO_RE.search(_text(category, market_type, market_group,
                              series_title, event_title, title, slug)):
        return True, ""
    is_matchup = bool(re.search(r"\bvs\.?\s", title_slug, re.IGNORECASE))
    if is_matchup:
        # A direct sports matchup with no fragment group label or fragment title
        # is the venue's common shape for a main line (for example MLB, NFL). Submarkets
        # carry a fragment group label (Spread -3.5, O/U 46.5), a fragment title token,
        # or a blocked token and are rejected above or here. Unknown category/type is
        # rejected rather than inferred from a generic "A vs B" title -- series, finals,
        # and other submarkets often look exactly like that, and this shape is where a
        # keyword miss would readmit exactly what the selector exists to block.
        # (Audited 2026-10-04, Issue #355: docs/issues/355-matchup-refusal-audit.md).
        if (_SPORTS_SERIES_RE.search(_text(category, series_title, title_slug))
                and not _is_fragment_label(market_group)
                and not _FRAGMENT_LABEL_RE.search(title_slug)):
            return True, ""
        if not require_primary:
            # The keyword could not be found because the fields it is read
            # from are absent, not because the market failed the test. A
            # fragment-shaped group label still refuses -- that field being
            # present and line-shaped IS evidence, and it is the one submarket
            # signal that survives on a metadata-less spec. A bare country,
            # name, or date label is not a fragment and answers downstream.
            if _is_fragment_label(market_group):
                return False, "carries a submarket group label"
            return True, ""
        return False, "not a primary Moneyline/Outright or Macro/Politics market"
    # No head-to-head shape to fragment. A blocked token already returned
    # above; a fragment-shaped group label means this is still someone's
    # submarket even without one, so it is refused. A bare country, name, or
    # date label is a main-line event name, so it is admitted and answers for
    # itself at the liquidity/depth/spread gates.
    if _is_fragment_label(market_group):
        return False, "carries a submarket group label"
    return True, ""


def top_depth_usd(levels: Mapping[float, float] | Iterable[tuple[float, float]],
                  count: int = 3) -> float:
    """Notional depth in the best ``count`` bid levels.

    The selector intentionally measures bids, not asks: bids are the immediate
    exit liquidity that prevents a filled naked leg from becoming unmarkable.
    """
    if count <= 0:
        return 0.0
    items = levels.items() if isinstance(levels, Mapping) else levels
    cleaned = []
    for price, size in items:
        try:
            p, s = float(price), float(size)
        except (TypeError, ValueError):
            continue
        if p > 0 and s > 0:
            cleaned.append((p, s))
    return sum(price * size for price, size in
               sorted(cleaned, key=lambda item: item[0], reverse=True)[:count])


def book_allowed(bids: Mapping[float, float] | Iterable[tuple[float, float]],
                 asks: Mapping[float, float] | Iterable[tuple[float, float]],
                 min_depth_usd: float = 5000.0,
                 max_spread: float = 0.04) -> tuple[bool, str, float, float]:
    """Check one token's two-sided book and return depth/spread diagnostics."""
    bid_items = list(bids.items()) if isinstance(bids, Mapping) else list(bids)
    ask_items = list(asks.items()) if isinstance(asks, Mapping) else list(asks)
    valid_bids = []
    for p, s in bid_items:
        try:
            p, s = float(p), float(s)
        except (TypeError, ValueError):
            continue
        if p > 0 and s > 0:
            valid_bids.append((p, s))
    valid_asks = []
    for p, s in ask_items:
        try:
            p, s = float(p), float(s)
        except (TypeError, ValueError):
            continue
        if p > 0 and s > 0:
            valid_asks.append((p, s))
    if not valid_bids or not valid_asks:
        return False, "one-sided or empty book", top_depth_usd(valid_bids), 0.0
    best_bid = max(p for p, _ in valid_bids)
    best_ask = min(p for p, _ in valid_asks)
    spread = best_ask - best_bid
    depth = top_depth_usd(valid_bids)
    if spread < 0 or spread > max_spread:
        return False, f"spread {spread:.4f} > {max_spread:.4f}", depth, spread
    if depth <= min_depth_usd:
        return False, f"top-3 bid depth ${depth:,.2f} <= ${min_depth_usd:,.2f}", depth, spread
    return True, "", depth, spread


def pair_books_allowed(books: Iterable[tuple[str, Mapping[float, float],
                                             Mapping[float, float]]],
                       min_depth_usd: float = 5000.0,
                       max_spread: float = 0.04) -> tuple[bool, str]:
    """Require the same book contract independently on YES and NO."""
    for label, bids, asks in books:
        ok, reason, _, _ = book_allowed(bids, asks, min_depth_usd, max_spread)
        if not ok:
            return False, f"{label}: {reason}"
    return True, ""


def queue_minutes_at(level_size: float, traded_at_level: float,
                     window_minutes: float) -> float:
    """Minutes of observed volume needed to clear the resting size at OUR price.

    Both terms are measured at the level we would actually quote at --
    `mid - reward_offset` on each token -- not market-wide and not near mid.
    That is the whole point: this is the one bar where our own price is the
    thing being measured.

    Measured on session B of shadow run `run-2809a7161de1`: 344 orders, 10
    markets, 30 minutes, zero fills, and not one order's queue drained. The
    best market in that universe (atp-bonzi-halys) had 22,081 shares resting
    against 966 shares of tape at our levels -- 686 minutes, 11.4 hours, to
    clear the queue in front of ONE order. `pairs_exit_window_sec` already
    calls a 15-minute-old naked leg stale. The mismatch is three orders of
    magnitude.

    No observed volume returns infinity, not zero. Four of those ten markets
    were in that state, and "we never saw a trade at our price" is the
    strongest form of the finding this exists to catch -- reading it as
    missing data would invert the rule exactly where it matters most.

    `window_minutes` MUST BE A RECENT, BOUNDED WINDOW -- comparable to how long
    an order actually rests, not however much history a caller happens to hold.
    Nothing here can check that, and the failure is silent: `family_probe`
    passed the span of the venue's entire returned tape, up to 46,000 minutes
    on live markets, and this function dutifully divided a month of volume by a
    month of minutes. The resulting rate admitted 173 moments as clearing
    inside 10 minutes, of which 97% of legs then drained under 1% of what they
    needed in 15. The arithmetic was right and the answer was fiction.

    UPPER BOUND, NOT AN ESTIMATE. The shadow model drains queue only on
    trades; a real book also advances an order when those ahead of it cancel,
    and at these depths that is likely the dominant mechanism. The true wait is
    lower, possibly by a lot. The RANKING between markets is more trustworthy
    than the absolute number, because every market is measured the same wrong
    way. See `cancel_decay`.
    """
    if window_minutes <= 0:
        return math.inf
    rate = float(traded_at_level) / float(window_minutes)
    if rate <= 0:
        return math.inf
    return float(level_size) / rate


def maker_queue_allowed(queue_minutes: float, max_queue_minutes: float,
                        enforce: bool = True) -> tuple[bool, str]:
    """Whether a market's queue at our level clears fast enough to join.

    `>=` not `>`, matching `select_min_top3_depth_usd` and every other cap
    here: a ceiling reached, not approached.

    `max_queue_minutes <= 0` disables the rule, the escape hatch every other
    limit in this repo has.

    `enforce=False` is RECORD-ONLY and is how this ships first. At the proposed
    15-minute bar the measured universe scores 686 minutes at best, so
    enforcing on day one refuses every market and takes the bot silent. That is
    the right answer for the run we measured -- 344 orders and no fills is
    worse than quoting nothing -- but shipping it enforcing and then hunting
    for why the dashboard is empty would be a self-inflicted incident. Record
    first, and let a day of real numbers say whether anything ever clears.
    """
    if max_queue_minutes <= 0:
        return True, ""
    if queue_minutes == math.inf:
        refusal = "maker queue: no trade observed at our price -- queue never clears"
    elif queue_minutes >= max_queue_minutes:
        refusal = (f"maker queue: {queue_minutes:.0f} min to clear at our price "
                   f">= {max_queue_minutes:.0f} min bar")
    else:
        return True, ""
    if not enforce:
        return True, f"record-only, would refuse -- {refusal}"
    return False, refusal


def cancel_decay(size_before: float, size_after: float,
                 traded: float) -> float:
    """Shares that left our level WITHOUT trading, net of shares that joined.

    The shadow fill model credits queue progress only from the tape, so it
    cannot see the mechanism that most likely moves a maker up a 13,000-share
    queue: the orders ahead of it being cancelled. Three zero-fill rehearsals
    have therefore produced no information about queue behaviour at all.

    This is the cheap way to find out without spending money. The level shrank
    by `size_before - size_after`; the tape explains `traded` of it; the
    residual is cancels. Both inputs are already fetched every cycle -- the
    book for queue position, the tape for fills -- so observing this costs one
    subtraction.

    NOT clamped at zero. A negative result means more size joined the level
    than left it, which is real information about a level filling in behind us;
    clamping would make a daily total read as pure decay and flatter the model
    exactly where it is already too generous.
    """
    return (float(size_before) - float(size_after)) - float(traded)
