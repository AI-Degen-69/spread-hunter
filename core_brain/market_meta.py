"""One shared market-metadata resolver for KPI and registry state.

Active Markets showed `Uncategorized` for two compounding reasons: KPI matched
a condition id only against the current top-20 `markets.json`, and registry
state resolved titles a second way with no category at all. Both delegate
here now: the current feed first, the scored universe for departed markets,
slug/title keywords last. Category is display-only -- it never feeds
selection, ranking, or risk.
"""
from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Any, Optional

from core_brain.runtime_paths import resolve_runtime_file

REPO_ROOT = Path(__file__).resolve().parent.parent

# Shown when neither the ranker feed nor its fallbacks name a category. A named
# bucket groups honestly; a blank cell reads as missing data.
UNCATEGORIZED = "Uncategorized"

ESPORTS_LABEL = "E-Sports"
POLITICS_LABEL = "Politics"

# Display-only esports vocabulary (#501). "E-Sports" names the genre and
# nothing else -- the same way "Uncategorized" named no category at all. A LoL
# market is a League of Legends market, so the detection token maps to the game
# itself and the genre survives only as a fallback for a game this table cannot
# name. Ordered so the more specific franchise wins: Counter-Strike 2 is checked
# before the generic Counter-Strike, and Overwatch 2 before Overwatch.
# Kept local like the sport rules on purpose: descriptive labels that must never
# influence selection or ranking.
_ESPORTS_GAME_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:lol|league\s*of\s*legends)\b", re.IGNORECASE),
     "League of Legends"),
    (re.compile(r"\b(?:cs2|counter[- ]strike\s*2)\b", re.IGNORECASE),
     "Counter-Strike 2"),
    (re.compile(r"\b(?:counter[- ]strike|csgo|cs:go)\b", re.IGNORECASE),
     "Counter-Strike"),
    (re.compile(r"\bvalorant\b", re.IGNORECASE), "Valorant"),
    (re.compile(r"\bdota\s*2?\b", re.IGNORECASE), "Dota 2"),
    (re.compile(r"\boverwatch\s*2\b", re.IGNORECASE), "Overwatch 2"),
    (re.compile(r"\boverwatch\b", re.IGNORECASE), "Overwatch"),
    (re.compile(r"\brainbow\s*six\b", re.IGNORECASE), "Rainbow Six Siege"),
    (re.compile(r"\b(?:starcraft|sc2)\b", re.IGNORECASE), "StarCraft II"),
    (re.compile(r"\brocket\s*league\b", re.IGNORECASE), "Rocket League"),
    (re.compile(r"\bapex\s*legends\b", re.IGNORECASE), "Apex Legends"),
    # `fifa` alone is deliberately NOT a token: the FIFA World Cup is football,
    # not the video game. Only the explicit EA Sports FC naming counts.
    (re.compile(r"\b(?:ea\s*sports\s*fc|ea\s*fc|fc\s*24|fc\s*25|fc\s*26)\b",
                re.IGNORECASE), "EA Sports FC"),
    (re.compile(r"\b(?:call\s*of\s*duty|cod)\b", re.IGNORECASE), "Call of Duty"),
    (re.compile(r"\bpubg\b", re.IGNORECASE), "PUBG"),
    (re.compile(r"\bfortnite\b", re.IGNORECASE), "Fortnite"),
    (re.compile(r"\bhearthstone\b", re.IGNORECASE), "Hearthstone"),
    (re.compile(r"\b(?:king\s*of\s*glory|honor\s*of\s*kings)\b", re.IGNORECASE),
     "Honor of Kings"),
    (re.compile(r"\b(?:arena\s*of\s*valor|mobile\s*legends)\b", re.IGNORECASE),
     "Mobile Legends"),
    (re.compile(r"\btekken\b", re.IGNORECASE), "Tekken"),
    (re.compile(r"\b(?:street\s*fighter|sf6)\b", re.IGNORECASE), "Street Fighter"),
    (re.compile(r"\bsmash\s*bros\.?\b", re.IGNORECASE), "Super Smash Bros."),
    (re.compile(r"\b(?:chess|magnus)\b", re.IGNORECASE), "Chess"),
    (re.compile(r"\b(?:speedrun|fighting\s*games?|battle\s*royale)\b",
                re.IGNORECASE), ESPORTS_LABEL),
]

# The genre catch-all: a row that is plainly esports but names no game above
# still has to group somewhere honest rather than fall through to a keyword
# sweep that would misfile it. Keeps the detection tokens the venue has always
# written (`lol`, `dota`, `valorant`, ...) so this stays a superset of the old
# `_ESPORTS_RE` rather than a narrower one.
_ESPORTS_GENRE_RE = re.compile(
    r"\b(?:esports?|e-sports|pro\s*gaming|gaming\s*title|"
    r"pro\s*players?|twitch|speedrunning|"
    r"lol|league\s*of\s*legends|dota|counter[- ]strike|cs2|valorant)\b",
    re.IGNORECASE,
)

# Politics vocabulary. Distinct from the sport rules: a title mentioning a
# country or a politician is a political market even when no sport appears.
_POLITICS_RE = re.compile(
    r"\b(?:politics?|political|election|president(?:ial)?|"
    r"senate|congress|governor|prime\s*minister)\b",
    re.IGNORECASE,
)

# Display-only sport labels (#501). A detection token maps to the sport's real
# name, not the league acronym: ATP/WTA are tennis circuits, not their own
# sport. Ordered so the first regex that hits wins -- Muay Thai is checked
# before the generic MMA/Boxing rules, and explicit league codes come before
# bare generic words. Kept local like the regexes above on purpose: these are
# descriptive labels and must never influence selection or ranking.
_SPORT_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:atp|wta|itf|tennis|wimbledon|roland[- ]garros|"
                r"australian open)\b", re.IGNORECASE), "Tennis"),
    (re.compile(r"\b(?:nba|wnba|basketball|euroleague|march madness)\b",
                re.IGNORECASE), "Basketball"),
    # "Football" is the hand-egg (oval) sport; a bare "football" lands here.
    (re.compile(r"\b(?:nfl|super bowl|ncaa football|cfb|american football|"
                r"football)\b", re.IGNORECASE), "Football"),
    (re.compile(r"\b(?:mlb|baseball|world series)\b", re.IGNORECASE), "Baseball"),
    (re.compile(r"\b(?:nhl|hockey|stanley cup)\b", re.IGNORECASE), "Hockey"),
    # "Soccer" is the leg-kicking sport.
    (re.compile(r"\b(?:fifa|premier league|epl|la liga|serie a|bundesliga|"
                r"ligue 1|champions league|mls|soccer|world cup|euros|"
                r"europa league)\b", re.IGNORECASE), "Soccer"),
    (re.compile(r"\b(?:pga|lpga|golf|masters|ryder cup)\b", re.IGNORECASE), "Golf"),
    (re.compile(r"\b(?:f1|formula 1|formula e|nascar|indycar|motogp|le mans|"
                r"rally|racing)\b", re.IGNORECASE), "Racing"),
    (re.compile(r"\b(?:muay thai|thai boxing|thai[- ]box)\b", re.IGNORECASE),
     "Muay Thai"),
    (re.compile(r"\b(?:ufc|mma|bellator|pfl|one championship|pride fc)\b",
                re.IGNORECASE), "MMA"),
    (re.compile(r"\b(?:boxing|wba|wbc|ibf|wbo|title fight|heavyweight)\b",
                re.IGNORECASE), "Boxing"),
    (re.compile(r"\b(?:ipl|cricket|test match|ashes)\b", re.IGNORECASE), "Cricket"),
    (re.compile(r"\b(?:rugby|six nations)\b", re.IGNORECASE), "Rugby"),
]


def _classify_sport(text: str) -> Optional[str]:
    """The sport's real name for a detection token, or None."""
    for pattern, label in _SPORT_RULES:
        if pattern.search(text):
            return label
    return None


def _classify_esports_game(text: str) -> Optional[str]:
    """The game's real name for a detection token, or None.

    Only reached when the text is already known to be esports, so a game the
    table cannot name falls through to the genre label by returning None.
    """
    for pattern, label in _ESPORTS_GAME_RULES:
        if pattern.search(text):
            return label
    return None


def classify_display_category(title: Any, event_title: Any,
                              slug: Any) -> Optional[str]:
    """E-Sports, a sport's real name, Politics, or None.

    Pure word-boundary match, no venue I/O. Display-only.
    """
    text = " ".join((
        str(title or ""),
        str(event_title or ""),
        str(slug or "").replace("-", " ").replace("_", " "),
    )).lower()
    if not text.strip():
        return None
    if _ESPORTS_GENRE_RE.search(text) or _classify_esports_game(text) is not None:
        # The genre keyword proves it is esports; the named game is the label.
        # A game this table cannot name keeps the genre, never a keyword guess.
        return _classify_esports_game(text) or ESPORTS_LABEL
    sport = _classify_sport(text)
    if sport is not None:
        return sport
    if _POLITICS_RE.search(text):
        return POLITICS_LABEL
    return None


# Top-level venue shelves the word rules above do not name (#748). A tag or a
# venue label is a category only when those rules name it or it appears here;
# anything else (`NBA Tip-Off`, `Match Winner`, `Republican Party`) is a series,
# group or outcome name and must never become the label. Whole-label match on
# purpose: `Elections` is Politics, `Republican Party` is not.
_CATEGORY_TAG_ALIASES: dict[str, str] = {
    "sports": "Sports",
    "e-sports": ESPORTS_LABEL,
    "esports": ESPORTS_LABEL,
    "politics": POLITICS_LABEL,
    "elections": POLITICS_LABEL,
    "geopolitics": "Geopolitics",
    "crypto": "Crypto",
    "culture": "Culture",
    "economy": "Economy",
    "tech": "Tech",
    "weather": "Weather",
    "world": "World",
}

# The shelves that contain something more specific. A label here loses to a
# named sport or game, so `Basketball` beats `Sports` whatever tag order says.
_PARENT_CATEGORIES = frozenset({"Sports", ESPORTS_LABEL, POLITICS_LABEL,
                               "Geopolitics"})


def _category_tag_rank(label: str) -> Optional[tuple[int, str]]:
    """`(specificity, canonical label)` for one tag or venue label, or None.

    Rank 3 is a named sport or game the word rules can identify, rank 2 a parent
    shelf (Politics, E-Sports, ...), rank 1 any other known top-level shelf.
    None means the text names no category at all.
    """
    game = _classify_esports_game(label) or _classify_sport(label)
    if game:
        return 3, game
    # Only an exact shelf name counts. The title classifier is deliberately NOT
    # consulted: it reads keywords, so a `Senate` tag would rank as Politics and
    # outrank a real `Crypto` tag on the same market (#748).
    alias = _CATEGORY_TAG_ALIASES.get(label.strip().lower())
    if alias is None:
        return None
    return (2 if alias in _PARENT_CATEGORIES else 1), alias


def category_from_tags(row: dict) -> Optional[str]:
    """The most specific category among a feed row's tags, or None.

    Tags arrive as strings or as `{"label": ...}` objects, and most of a row's
    tags are not categories at all (`Recurring`, `Hide From New`, team names),
    so the winner is the highest-ranked recognised tag, never the first one.
    """
    if not isinstance(row, dict):
        return None
    tags = row.get("tags")
    if not isinstance(tags, list):
        return None
    best: Optional[tuple[int, str]] = None
    for tag in tags:
        if isinstance(tag, dict):
            tag = tag.get("label") or tag.get("slug")
        if not isinstance(tag, str) or not tag.strip():
            continue
        ranked = _category_tag_rank(tag)
        if ranked and (best is None or ranked[0] > best[0]):
            best = ranked
    return best[1] if best else None


def _category_from_feed_label(label: Optional[str]) -> Optional[str]:
    """A venue label as a category, only when the vocabulary actually names it.

    gamma writes a real shelf into `venue_category`/`category` on some rows and
    leaves an unrecognised series or group name there on others; returning None
    for those lets the tags and the keyword fallback run instead of printing
    `Match Winner` under every row (#748).
    """
    if not label or not label.strip():
        return None
    ranked = _category_tag_rank(label)
    return ranked[1] if ranked else None


def _feed_label_category(row: dict) -> Optional[str]:
    """The first venue label on the row the vocabulary recognises, or None.

    Each label is validated on its own, in precedence order: a row can carry
    `venue_category="Match Winner"` next to `category="Tennis"`, and stopping at
    the first non-empty string would throw the real label away (#748).
    """
    for key in ("venue_category", "category"):
        label = _category_from_feed_label(_text_of(row, key))
        if label:
            return label
    return None


class _FeedList(list):
    """List subclass holding an index of rows by cid for O(1) lookup."""
    _cid_map: dict[str, dict]


@functools.lru_cache(maxsize=32)
def _load_feed_cached(path_str: str, mtime_ns: int) -> _FeedList:
    try:
        path = Path(path_str)
        if not path.exists():
            res = _FeedList()
            res._cid_map = {}
            return res
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("rows") or []
        if not isinstance(payload, list):
            res = _FeedList()
            res._cid_map = {}
            return res
        rows = [row for row in payload if isinstance(row, dict)]
        res = _FeedList(rows)
        res._cid_map = {_text_of(r, "cid").lower(): r for r in rows if _text_of(r, "cid")}
        return res
    except Exception:
        res = _FeedList()
        res._cid_map = {}
        return res


def _feed_rows(name: str, root: Path | str) -> list[dict]:
    """Rows of a runtime feed file; unreadable or misshapen reads as empty."""
    try:
        path = resolve_runtime_file(name, root=root)
        if not path.exists():
            return []
        mtime_ns = path.stat().st_mtime_ns
        return _load_feed_cached(str(path.resolve()), mtime_ns)
    except Exception:
        return []


def _text_of(row: dict, key: str) -> str:
    """A feed text field: truthy non-strings are malformed, not labels."""
    value = row.get(key)
    return value.strip() if isinstance(value, str) else ""


def _find_row(rows: list[dict], cid: str) -> Optional[dict]:
    cid_lower = cid.lower()
    cid_map = getattr(rows, "_cid_map", None)
    if cid_map is not None:
        return cid_map.get(cid_lower)
    for row in rows:
        if _text_of(row, "cid").lower() == cid_lower:
            return row
    return None


def _slug_to_title(slug: str) -> str:
    return slug.replace("-", " ").title()


def resolve_market_meta(cid: str, closes: Optional[list[dict]] = None,
                        quotes: Optional[list[dict]] = None, *,
                        root: Optional[Path | str] = None) -> dict[str, Any]:
    """Human-readable title, slug, link, and category for a condition id.

    `root` is the caller's repo root (KPI and registry state each pass their
    own, which is also how tests redirect the feed). Category comes from typed
    candidates in order (#748): a venue label the vocabulary recognises, the
    most specific recognised tag, then the title/slug keyword fallback, then
    `Uncategorized`. A series title, a group name or an outcome name is never a
    candidate -- those are what printed `NBA Tip-Off` and `Republican Party`
    under a market name. `event_title` rides along because the Positions row
    needs a real second line.
    """
    out: dict[str, Any] = {
        "condition_id": cid,
        "title": None,
        "slug": None,
        "url": None,
        "category": None,
        "event_title": "",
        "days_to_resolve": None,
        "min_size": None,
        "volume_24h": None,
        "movement_usd": None,
        "movement_window_sec": None,
        "top3_bid_depth": None,
        "yes_depth_usd": None,
        "no_depth_usd": None,
        "source": None,
    }
    if not cid:
        out["category"] = UNCATEGORIZED
        return out

    base = REPO_ROOT if root is None else root
    row = _find_row(_feed_rows("markets.json", base), cid)
    if row is None:
        row = _find_row(_feed_rows("market_universe.json", base), cid)

    event_title = ""
    if row is not None:
        event_title = _text_of(row, "event_title")
        out["event_title"] = event_title
        yes_d = row.get("yes_depth_usd")
        no_d = row.get("no_depth_usd")
        top3_d = None
        if yes_d is not None and no_d is not None:
            try:
                top3_d = min(float(yes_d), float(no_d))
            except (TypeError, ValueError):
                pass
        elif row.get("top3_bid_depth") is not None:
            top3_d = row.get("top3_bid_depth")
        elif yes_d is not None or no_d is not None:
            top3_d = yes_d if yes_d is not None else no_d

        out.update({
            "title": row.get("title") or event_title or None,
            "slug": row.get("slug"),
            # The live feed ships category="" on most rows. Candidate one is the
            # first venue label the vocabulary recognises; candidate two is the
            # most specific recognised tag. Everything else -- series, group,
            # first tag -- is not a category and is deliberately not consulted
            # (#748).
            "category": _feed_label_category(row) or category_from_tags(row),
            "days_to_resolve": row.get("days_to_resolve"),
            "min_size": row.get("min_size"),
            "volume_24h": row.get("volume_24h"),
            "movement_usd": row.get("movement_usd"),
            "movement_window_sec": row.get("movement_window_sec"),
            "top3_bid_depth": top3_d,
            "yes_depth_usd": yes_d,
            "no_depth_usd": no_d,
            "source": row.get("source"),
        })

    # Fallback to closes or quotes market_slug
    if not out["slug"]:
        for c in closes or []:
            if c.get("condition_id") == cid and c.get("market_slug"):
                out["slug"] = c["market_slug"]
                break
    if not out["slug"]:
        for q in quotes or []:
            if q.get("condition_id") == cid and q.get("market_slug"):
                out["slug"] = q["market_slug"]
                break

    if not out["title"] and out["slug"]:
        out["title"] = _slug_to_title(out["slug"])
    elif not out["title"]:
        out["title"] = f"Market {cid[:10]}...{cid[-6:]}" if len(cid) > 16 else cid

    if out["slug"]:
        out["url"] = f"https://polymarket.com/market/{out['slug']}"
    if not out["category"]:
        out["category"] = (classify_display_category(
            out["title"], event_title, out["slug"]) or UNCATEGORIZED)
    return out
