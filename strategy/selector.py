"""Hard market eligibility rules shared by the ranker and live paper fleet.

The selector is deliberately pure. It does not fetch markets or write state; it
only answers whether metadata and a pair of order books satisfy the strategy's
entry contract. Keeping the rule here prevents the ranker and the fleet from
drifting apart when a stale ``run/markets.json`` is still on disk.
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping


# Normalize separators before matching so both human titles ("Game 1") and
# venue slugs ("game1", "in-play", "map_2") are rejected.
_BLOCKED_RE = re.compile(
    r"(?<![a-z0-9])(?:game|map|set)[\s_-]*[12](?![a-z0-9])|"
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


def _text(*values: object) -> str:
    return " ".join(str(v or "") for v in values).strip()


def identity_allowed(title: object = "", slug: object = "",
                     category: object = "", market_type: object = "",
                     market_group: object = "", series_title: object = "",
                     event_title: object = "") -> tuple[bool, str]:
    """Return whether a market is a permitted primary/main-line instrument.

    Unknown metadata is rejected. Sports/eSports markets must explicitly identify
    themselves as Moneyline/Main Line/Outright; Politics and Macro/Economics are
    allowed by category or market type because their primary market titles do not
    consistently carry the word ``Moneyline``.
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
    # A direct sports matchup with no group label is the venue's common shape
    # for a main line (for example MLB). Submarkets carry a group label or a
    # blocked token and are rejected above.
    if (_SPORTS_SERIES_RE.search(_text(category, market_type, series_title,
                                       event_title, title_slug))
            and re.search(r"\bvs\.?\s", title_slug, re.IGNORECASE)
            and not _text(market_group)):
        return True, ""
    # Unknown category/type is rejected rather than inferred from a generic
    # "A vs B" title. Series, finals, and other submarkets often look exactly
    # like that; admitting them would recreate the failure this selector exists
    # to prevent. The venue must identify the primary instrument explicitly.
    return False, "not a primary Moneyline/Outright or Macro/Politics market"


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
