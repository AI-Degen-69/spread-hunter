"""Discover the currently-live BTC 5-min market via gamma-api events endpoint."""
from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Any, NamedTuple, Optional

import requests

from core_brain.market_resolution import extract_uma_resolution_status

log = logging.getLogger("markets")

# (connect, read). `fetch_pinned_market` is called from inside the fleet's
# trading loop for any market not yet loaded, and its old scalar 15s applied to
# the connect phase as well -- one unreachable market could hold the whole
# rotation for half a minute and push the sweep past the dashboard's 120s
# staleness threshold.
MARKET_TIMEOUT = (3.05, 5.0)
EVENTS_TIMEOUT = (3.05, 5.0)

# Pooled keep-alive instead of a fresh TLS handshake per call. No retries -- a
# failed load is handled by the caller (the market is skipped for this visit)
# and retrying here would spend the loop's time budget silently.
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Mozilla/5.0"})
for _scheme in ("https://", "http://"):
    _SESSION.mount(_scheme, requests.adapters.HTTPAdapter(
        pool_connections=8, pool_maxsize=8, max_retries=0))


# How fresh series evidence must be for the live-series exemption (#402). The
# feed stamps each live row when it is built; older than this, the score may
# describe a finished game and the band applies as usual (fail closed).
SERIES_EVIDENCE_MAX_AGE_SEC = 300.0


@dataclass(frozen=True)
class SeriesState:
    """What the venue says about a series market's progress.

    ``scope`` is "series" (a best-of match whose moneyline is the match
    winner), "single" (a per-game line), or "unknown" (anything the parser
    cannot prove). Only "series" can ever pass the live-series predicate;
    unknown fails closed. ``games_remaining`` is None when the score gives no
    series count to work from.
    """
    scope: str
    best_of: Optional[int] = None
    wins_a: Optional[int] = None
    wins_b: Optional[int] = None
    games_remaining: Optional[int] = None
    venue_live: bool = False
    venue_ended: bool = False
    evidence_ts: Optional[float] = None


_BO_SUFFIX_RE = re.compile(r"\b[Bb][Oo]\s?(\d{1,2})\b")
_BEST_OF_TEXT_RE = re.compile(r"best\s+of\s+(\d{1,2})", re.IGNORECASE)
_PERIOD_GAMES_RE = re.compile(r"^\s*(\d{1,2})\s*/\s*(\d{1,2})\s*$")
_GAMES_SCORE_RE = re.compile(r"^\s*(\d{1,3})\s*-\s*(\d{1,3})\s*$")


def _parse_games_score(text: str) -> Optional[tuple[int, int]]:
    m = _GAMES_SCORE_RE.match(text or "")
    if not m:
        return None
    try:
        return int(m.group(1)), int(m.group(2))
    except ValueError:
        return None


def parse_series_state(
    *,
    sports_market_type: Optional[str] = None,
    score: Optional[str] = None,
    period: Optional[str] = None,
    question: Optional[str] = None,
    live: bool = False,
    ended: bool = False,
    evidence_ts: Optional[float] = None,
) -> SeriesState:
    """Pure: venue series evidence -> SeriesState, never raises.

    Score shape (docs/polymarket-live-events-endpoint.md): the running game
    score, the series games, and the best-of suffix, e.g. "8-1|2-0|Bo5".
    `moneyline` + best-of > 1 is a series; `child_moneyline` is a single
    game; anything else is unknown. Best-of falls back to the "k/N" period
    form ("3/5" -> best of 5) and then to "(BO3)" / "best of 3" in the
    question. A lone "A-B" score counts as the series games only when a
    best-of is known from elsewhere -- without that context it is as likely
    a game score (NHL "2-0") and stays unknown.
    """
    mtype = str(sports_market_type or "").strip().lower()
    best_of: Optional[int] = None
    wins: Optional[tuple[int, int]] = None

    parts = [p.strip() for p in str(score or "").split("|")]
    bo_part = next((p for p in reversed(parts)
                    if _BO_SUFFIX_RE.search(p)), None)
    if bo_part:
        try:
            best_of = int(_BO_SUFFIX_RE.search(bo_part).group(1))  # type: ignore[union-attr]
        except (ValueError, AttributeError):
            best_of = None
        if len(parts) >= 3:
            wins = _parse_games_score(parts[-2])
    if best_of is None:
        pm = _PERIOD_GAMES_RE.match(str(period or ""))
        if pm:
            try:
                best_of = int(pm.group(2))
            except ValueError:
                best_of = None
    if best_of is None:
        q = str(question or "")
        qm = _BO_SUFFIX_RE.search(q) or _BEST_OF_TEXT_RE.search(q)
        if qm:
            try:
                best_of = int(qm.group(1))
            except ValueError:
                best_of = None
    if wins is None and best_of is not None and len(parts) == 1:
        wins = _parse_games_score(parts[0])

    if mtype == "child_moneyline":
        scope = "single"
    elif mtype == "moneyline" and best_of is not None and best_of > 1:
        scope = "series"
    else:
        scope = "unknown"

    games_remaining: Optional[int] = None
    if scope == "series" and wins is not None and best_of:
        games_remaining = max(0, best_of - (wins[0] + wins[1]))

    return SeriesState(
        scope=scope,
        best_of=best_of,
        wins_a=wins[0] if wins else None,
        wins_b=wins[1] if wins else None,
        games_remaining=games_remaining,
        venue_live=bool(live),
        venue_ended=bool(ended),
        evidence_ts=evidence_ts,
    )


def live_series_with_games_remaining(
    state: Optional[SeriesState],
    now_ts: Optional[float] = None,
    max_age_sec: float = SERIES_EVIDENCE_MAX_AGE_SEC,
) -> bool:
    """True only when venue evidence proves a live series has games left.

    Fails closed: unknown scope, missing or clinched score, an ended venue
    flag, or stale/absent evidence all read False and the band applies as
    it does today.
    """
    if state is None or state.scope != "series":
        return False
    if not state.venue_live or state.venue_ended:
        return False
    if state.games_remaining is None or state.games_remaining <= 0:
        return False
    if state.best_of and state.wins_a is not None and state.wins_b is not None:
        needed = state.best_of // 2 + 1
        if max(state.wins_a, state.wins_b) >= needed:
            return False
    if state.evidence_ts is None:
        return False
    at = now_ts if now_ts is not None else time.time()
    age = at - float(state.evidence_ts)
    return 0 <= age <= max_age_sec


@dataclass(frozen=True)
class LiveMarket:
    condition_id: str
    market_slug: str
    up_token: str
    down_token: str
    start_ts: float  # unix seconds, market opens
    end_ts: float    # unix seconds, market closes
    tick_size: float
    neg_risk: bool
    game_start_ts: Optional[float] = None  # venue kickoff, None when unstated
    # Feed-row series evidence, attached by the Trader after `fetch_market`
    # (#402). None on every other path: the band applies as before.
    series_state: Optional[SeriesState] = None

    def t_remaining(self, now: Optional[float] = None) -> float:
        return self.end_ts - (now if now is not None else time.time())


# How long after kickoff an in-play market stays quotable. Sports/esports
# `endDate` is kickoff, not the final whistle (see #312, #386), so the plain
# countdown goes negative the moment the match starts. Six hours covers a BO5
# or a football match with extra time; the operator can retune it.
IN_PLAY_WINDOW_SEC = 6 * 3600.0


def quote_t_remaining(market: Any, now: Optional[float] = None) -> float:
    """Seconds left to quote this market, kickoff-aware.

    A market whose kickoff has passed counts down to kickoff plus
    `IN_PLAY_WINDOW_SEC` instead of the kickoff-valued `end_ts`. A real later
    end date always wins, and anything without a past kickoff keeps the plain
    countdown -- pre-start and genuinely expired markets stay refused.
    `market` needs only a `t_remaining()` method; the kickoff field is read
    defensively so older stand-ins keep working.
    """
    at = now if now is not None else time.time()
    try:
        real = market.t_remaining() if now is None else market.t_remaining(now)
    except TypeError:
        real = market.t_remaining()
    try:
        kickoff = float(getattr(market, "game_start_ts", None))
    except (TypeError, ValueError):
        return real
    if kickoff > at:
        return real
    return max(real, kickoff + IN_PLAY_WINDOW_SEC - at)


# Slugs come from the venue API and are later embedded in dashboard HTML
# attributes/links and persisted to the fleet DB. Restrict them to the
# unreserved URL character set at the venue-data boundary so a hostile value
# never reaches either place.
_SAFE_SLUG_RE = re.compile(r"[^A-Za-z0-9._~-]")


def _sanitize_slug(slug: str) -> str:
    return _SAFE_SLUG_RE.sub("", slug or "")


def _parse_market(market: dict) -> Optional[LiveMarket]:
    """One venue market row -> LiveMarket, or None when the row is unusable.

    Row garbage is skipped, never raised. `fetch_live_market` walks every
    market of every open event, so one malformed row used to abort the whole
    universe scan and leave the screener with no market list for the rotation
    -- the same failure mode `parse_book` was hardened against.
    """
    if not isinstance(market, dict):
        return None
    try:
        return _parse_market_row(market)
    except (TypeError, ValueError, KeyError, AttributeError, json.JSONDecodeError):
        log.debug("skipping malformed market row: %r", market.get("conditionId")
                  if isinstance(market, dict) else market)
        return None


def _parse_market_row(market: dict) -> Optional[LiveMarket]:
    token_ids_raw = market.get("clobTokenIds")
    if not token_ids_raw:
        return None
    token_ids = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw
    if not isinstance(token_ids, (list, tuple)) or len(token_ids) != 2:
        return None
    # Element-level check too: `str(None)` is the string "None", which would
    # travel on as a token id and fail far from here.
    # `bool` is an `int` subclass: True would otherwise pass as the token id
    # "True". Strings and real integers only.
    if not all(isinstance(t, (str, int)) and not isinstance(t, bool)
               and str(t).strip() for t in token_ids):
        return None

    condition_id = market.get("conditionId")
    if not condition_id:
        return None

    if extract_uma_resolution_status(market):
        return None

    # eventStartTime is the actual trading-window open (UTC :00/:05/:10 boundary).
    # startDate is when the market was *listed*, often hours earlier.
    start_iso = market.get("eventStartTime")
    end_iso = market.get("endDate") or market.get("endDateIso")
    if not start_iso or not end_iso:
        return None

    start_ts = _iso_to_unix(start_iso)
    end_ts = _iso_to_unix(end_iso)
    if start_ts is None or end_ts is None:
        return None

    return LiveMarket(
        condition_id=str(condition_id),
        market_slug=_sanitize_slug(market.get("slug", "")),
        up_token=str(token_ids[0]),
        down_token=str(token_ids[1]),
        start_ts=start_ts,
        end_ts=end_ts,
        tick_size=float(market.get("orderPriceMinTickSize") or 0.01),
        neg_risk=bool(market.get("negRisk", False)),
    )


def _iso_to_unix(s: str) -> Optional[float]:
    """ISO-8601 -> unix seconds, or None when the string will not parse.

    Returns None rather than raising: a timestamp the venue mangled is a row
    to skip, not a reason to abort the caller's whole scan.
    """
    # tolerate "Z" suffix
    from datetime import datetime
    if not isinstance(s, str) or not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        log.debug("unparseable market timestamp: %r", s)
        return None


def fetch_live_market(gamma_host: str, series_slug: str) -> Optional[LiveMarket]:
    """Return the single 5-min BTC market that's currently live, or None."""
    url = f"{gamma_host}/events"
    params = {"series_slug": series_slug, "closed": "false", "limit": 500}
    r = _SESSION.get(url, params=params, timeout=EVENTS_TIMEOUT)
    r.raise_for_status()
    events = r.json()

    now = time.time()
    candidates: list[LiveMarket] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        markets = ev.get("markets") or []
        if not isinstance(markets, list):
            continue
        for m in markets:
            lm = _parse_market(m)
            if lm and lm.start_ts <= now < lm.end_ts:
                candidates.append(lm)
    if not candidates:
        return None
    candidates.sort(key=lambda m: m.start_ts, reverse=True)
    return candidates[0]


def discover_ladder_series(gamma_host: str, series_slugs: list[str], *,
                           open_window_sec: float = 30.0,
                           now: Optional[float] = None) -> list[LiveMarket]:
    """Upcoming ladder series markets inside their OPEN window, oldest first.

    The ladder quotes a series at OPEN (first `open_window_sec` seconds of
    `start_ts`). One `/events` call per slug, same row parsing as
    `fetch_live_market` (malformed rows skipped, never raised). Non-series
    rows cannot appear -- the venue filters by `series_slug` -- but anything
    outside the window is dropped here all the same.
    """
    at = now if now is not None else time.time()
    found: list[LiveMarket] = []
    for slug in series_slugs:
        r = _SESSION.get(f"{gamma_host}/events",
                         params={"series_slug": slug, "closed": "false",
                                 "limit": 500},
                         timeout=EVENTS_TIMEOUT)
        r.raise_for_status()
        events = r.json()
        if not isinstance(events, list):
            continue
        for ev in events:
            if not isinstance(ev, dict):
                continue
            inner = ev.get("markets") or []
            if not isinstance(inner, list):
                continue
            for m in inner:
                lm = _parse_market(m)
                if lm and lm.start_ts <= at < lm.start_ts + open_window_sec:
                    found.append(lm)
    found.sort(key=lambda m: m.start_ts)
    return found


def fetch_pinned_market(condition_id: str) -> Optional[LiveMarket]:
    """One specific long-dated market, pinned by condition_id.

    The 5-min BTC series pays nothing for resting (rewards.rates = null); these
    markets do. They also do not roll every five minutes, so there is no window
    to discover -- we quote the same book all day. `end_ts` is the real
    resolution date (months out), which makes t_remaining effectively infinite
    and disables every 5-min-specific timing rule by construction.

    Whether a market is worth funding is the allocator's decision and it is made
    from `runtime/markets.json`; this function's job is only to say whether the
    market can be quoted at all.
    """
    r = _SESSION.get(f"https://clob.polymarket.com/markets/{condition_id}",
                     timeout=MARKET_TIMEOUT)
    r.raise_for_status()
    m = r.json()

    rewards = m.get("rewards") or {}
    rates = rewards.get("rates") or []
    daily = sum(x.get("rewards_daily_rate", 0) or 0 for x in rates)
    if m.get("closed") or not m.get("accepting_orders"):
        return None

    toks = [t.get("token_id") for t in (m.get("tokens") or [])]
    if len(toks) != 2:
        return None

    end_iso = m.get("end_date_iso")
    end_ts = _iso_to_unix(end_iso) if end_iso else None
    if end_ts is None:
        # No parseable resolution date: treat the market as long-dated rather
        # than refusing to load it. Every 5-min timing rule is disabled by an
        # effectively-infinite t_remaining anyway.
        end_ts = time.time() + 365 * 86400
    # Kickoff when the venue states one (sports/esports `game_start_time`).
    # Missing or mangled reads as None: the quote clock falls back to the
    # plain countdown exactly as before.
    game_start_ts = _iso_to_unix(m.get("game_start_time") or "")
    return LiveMarket(
        condition_id=condition_id,
        market_slug=_sanitize_slug(m.get("market_slug") or condition_id[:10]),
        up_token=str(toks[0]),
        down_token=str(toks[1]),
        start_ts=time.time() - 1.0,
        end_ts=end_ts,
        tick_size=float(m.get("minimum_tick_size") or 0.01),
        neg_risk=bool(m.get("neg_risk", False)),
        game_start_ts=game_start_ts,
    )


def market_meta(condition_id: str) -> dict:
    """Question text, link and funded daily rate, for the dashboard header."""
    try:
        m = _SESSION.get(f"https://clob.polymarket.com/markets/{condition_id}",
                         timeout=MARKET_TIMEOUT).json()
    except Exception:
        return {}
    rw = m.get("rewards") or {}
    slug = _sanitize_slug(m.get("market_slug") or "")
    return {
        "question": m.get("question") or condition_id[:12],
        "slug": slug,
        "url": f"https://polymarket.com/market/{slug}" if slug else "",
        "daily_rate": sum(x.get("rewards_daily_rate", 0) or 0
                          for x in (rw.get("rates") or [])),
        "max_spread": rw.get("max_spread"),
        "min_size": rw.get("min_size"),
        "tick": m.get("minimum_tick_size"),
    }


# --- book / tape fetchers (moved here from the deleted strategy/main.py, #14) --

TRADES_API = "https://data-api.polymarket.com/trades"

# (connect, read) rather than one scalar. Split deliberately: a host that is not
# answering its SYN at all is abandoned in ~3s, while a host that did answer
# gets 5s to finish the body. The old scalar 10s applied to BOTH phases, so a
# single unreachable endpoint could add 20s to one market visit -- three such
# markets in a sweep is the difference between a 60s cycle and the >120s the
# dashboard calls dead.
BOOK_TIMEOUT = (3.05, 5.0)
TAPE_TIMEOUT = (3.05, 5.0)


def parse_book(raw: dict, token_id: str) -> dict:
    """Venue /book payload -> the canonical book dict, skipping bad levels.

    The parse half of the fetch seam. The contract distinguishes ROW garbage
    from a STRUCTURAL failure:

      * a row whose price or size will not parse is skipped and counted in
        `malformed` -- the same tolerance `selector.top_depth_usd` applies
        to gate inputs. One bad level must not take down a caller: it used
        to crash the ranker's whole run and get the fleet to cancel quotes
        on a healthy venue.
      * a payload that is not a dict, or a side that is not a list, raises
        ValueError -- that is a fetch-shaped failure, and callers already
        treat fetch failures (retry, hold, fail closed).

    `malformed` lets a caller fail closed when a skipped level would overstate
    its own reading (the ranker drops the market: an under-counted competitor
    inflates projected income) or ignore the count when the gate judges what
    is readable (the fleet).
    """
    if not isinstance(raw, dict):
        raise ValueError("book payload is not a dict")
    bids: dict[float, float] = {}
    asks: dict[float, float] = {}
    malformed = 0
    for side, target in (("bids", bids), ("asks", asks)):
        rows = raw.get(side) or []
        if not isinstance(rows, list):
            raise ValueError(f"book {side} is not a list")
        for x in rows:
            if not isinstance(x, dict):
                malformed += 1
                continue
            try:
                price = round(float(x["price"]), 4)
                size = float(x["size"])
            except (TypeError, ValueError, KeyError):
                malformed += 1
                continue
            target[price] = size
    return {
        "token_id": token_id,
        "bids": bids,
        "asks": asks,
        "best_bid": max(bids) if bids else None,
        "best_ask": min(asks) if asks else None,
        "malformed": malformed,
    }


def full_book(clob_host: str, token_id: str) -> dict:
    """Full depth, not just top-of-book -- queue position needs the level sizes.

    Row-level garbage is skipped by `parse_book`, never raised: a malformed
    level must not look like a network failure to the sweep's book gate, or
    a healthy venue gets its quotes cancelled. Structural failures still
    raise and ride the fetch-failure path.
    """
    r = _SESSION.get(f"{clob_host}/book", params={"token_id": token_id},
                     timeout=BOOK_TIMEOUT)
    r.raise_for_status()
    return parse_book(r.json(), token_id)


def recent_trades(condition_id: str, seen: set, limit: int = 500,
                  taker_side: str | None = "SELL") -> dict:
    """Volume by (token_id, price) that could fill a RESTING BID since we looked.

    The fill model needs this to tell a level that was TRADED from one that was
    CANCELLED -- from the book they are identical, and guessing costs an order
    of magnitude: on recorded books the book-only model reported a 50% fill
    rate where the tape-confirmed rate was 3%, because every fill it produced
    came from the "level emptied, credit the whole remainder" branch.

    De-duplicated by trade identity rather than by timestamp window: the API
    stamps trades to the second while we poll faster than that, so a time-based
    cursor would double-count or skip. The identity key carries the normalized
    side, so a BUY-labelled row (mint leg) can never suppress the matching
    SELL row -- rows the filter rejects still take their own keys, which is
    what lets the page walk below recognise a fully-read page.

    Two walks per call. Walk 1 pages the taker view (`limit` rows, `offset`
    pages, at most TRADE_MAX_PAGES) and counts `taker_side` rows exactly as
    before. Walk 2 pages the full view (`takerOnly=False`) and counts only
    BUY rows whose trade never appeared in walk 1: those are resting bids the
    tape filled from the maker side, e.g. the DOWN leg of a mint whose taker
    row prints UP. Maker SELL rows stay uncounted -- a lifted ask is not queue
    drain, and under-counting is the conservative direction for a fill model.
    Rows carry no role field, so walk 2 identifies maker rows by subtracting
    walk 1's identities. Either walk stops early on a short page, a page with
    nothing new, or the page bound. Each walk commits its volume and identities
    only when it finishes: a failed walk keeps nothing of its own pages, so a
    later poll re-reads them instead of skipping silently -- while walk 1's
    committed result always survives a walk 2 failure.

    Row-level garbage is skipped, never raised: the parse sits OUTSIDE the
    fetch try, and before this a single unparseable price crashed out of the
    loop -- which the sweep's "exceptions propagate" contract turned into a
    market that silently vanished from every sweep with no status, no err and
    no event. A skipped trade only under-counts volume at a level, which is
    the conservative direction for a fill model.

    `taker_side` is the side the AGGRESSOR took, and it defaults to the only
    side that can reach a resting bid. A resting BUY at $0.42 fills when
    somebody SELLS at $0.42; a taker who BUYS at $0.42 lifts an ask or mints
    against the complement leg and leaves our bid where it was. Returning both
    directions credited the fill model volume it could never touch -- on a live
    500-trade sample the endpoint returned 84% BUY, so the shadow queue drained
    roughly six times too fast and every rehearsal fill rate built on it read
    fast. A row whose side is missing or unreadable is skipped rather than
    counted: if the endpoint ever drops the field the rehearsal must report no
    fills instead of inventing them. Pass `taker_side=None` for the whole tape,
    which is what a recorder measuring market activity wants.
    """
    out: dict[str, dict[float, float]] = {}
    wanted = None if taker_side is None else str(taker_side).strip().upper()
    taker_ids: set = set()  # side-blind identities from walk 1, this call
    try:
        staged, walk_out = _walk_tape(condition_id, seen, taker_ids,
                                      wanted, limit, taker_only=None)
    except Exception as e:
        log.debug("tape fetch failed: %s", e)
        return {}                       # no tape -> caller falls back to books
    seen.update(staged)
    _merge_tape_volume(out, walk_out)
    if wanted in (None, "SELL"):
        # Resting-bid settlement (and the whole-tape recorder) only: a
        # taker-BUY request must read taker BUYs, never maker fills.
        try:
            staged, walk_out = _walk_tape(condition_id, seen, taker_ids,
                                              wanted, limit, taker_only=False)
        except (requests.RequestException, ValueError) as e:
            log.debug("maker tape fetch failed: %s", e)
        else:
            seen.update(staged)
            _merge_tape_volume(out, walk_out)
    return out


# Pages per settlement read. Walk 1 preserves the old single-page shape on a
# quiet market (short first page stops immediately); the bound only matters on
# a busy tape, where one page used to go blind past 500 rows. Offsets stay
# inside the endpoint's documented 10,000 cap.
TRADE_MAX_PAGES = 4
TRADE_MAX_OFFSET = 10000


def _row_side(t: dict):
    side = t.get("side")
    return None if side is None else str(side).strip().upper()


def _merge_tape_volume(out: dict, walk_out: dict) -> None:
    for tok, by_price in walk_out.items():
        slot = out.setdefault(tok, {})
        for p, size in by_price.items():
            slot[p] = slot.get(p, 0.0) + size


def _walk_tape(condition_id: str, seen: set, taker_ids: set,
               wanted, limit: int, taker_only):
    """One paginated pass over the tape.

    Returns this walk's `(staged_identities, volume)`; the caller commits
    both into `seen`/`out` only when the walk finishes. A fetch failure
    raises, discarding the walk's pages so a later poll re-reads them.

    `taker_only=None` is the taker-view baseline walk: every row takes a
    side-aware staged key, its side-blind identity joins `taker_ids`, and
    only `wanted`-side rows count. `taker_only=False` is the maker walk: only
    BUY rows (or every row when `wanted` is None) whose identity walk 1 never
    carried count -- resting-bid fills the baseline view cannot see.
    """
    staged: set = set()
    walk_out: dict = {}
    maker_walk = taker_only is not None
    for page in range(TRADE_MAX_PAGES):
        offset = page * int(limit)
        if offset >= TRADE_MAX_OFFSET:
            break
        params = {"market": condition_id, "limit": limit, "offset": offset}
        if maker_walk:
            params["takerOnly"] = False
        r = _SESSION.get(TRADES_API, params=params, timeout=TAPE_TIMEOUT)
        r.raise_for_status()
        rows = r.json() or []
        if not isinstance(rows, list):
            log.debug("tape response is not a list (got %s)",
                      type(rows).__name__)
            return staged, walk_out
        fresh = False
        for t in rows:
            if not isinstance(t, dict):
                continue
            side = _row_side(t)
            blind = (str(t.get("transactionHash") or ""), str(t.get("asset")),
                     t.get("timestamp"), t.get("price"), t.get("size"))
            key = blind + (side,)
            if key in seen or key in staged:
                continue
            staged.add(key)
            fresh = True
            if not maker_walk:
                taker_ids.add(blind)
                if wanted is not None and side != wanted:
                    continue
            else:
                if blind in taker_ids:
                    continue
                if wanted is not None and side != "BUY":
                    continue
            tok = str(t.get("asset"))
            try:
                p = round(float(t.get("price") or 0), 4)
                size = float(t.get("size") or 0)
            except (TypeError, ValueError):
                continue
            slot = walk_out.setdefault(tok, {})
            slot[p] = slot.get(p, 0.0) + size
        if len(rows) < int(limit) or not fresh:
            break
    return staged, walk_out


class SellFlow(NamedTuple):
    """Taker SELL shares that reached a price, with an honest reading status.

    `status` is the point of the type. `complete` means the walk reached the
    edge of the window, so the numbers are the whole window; `truncated` means
    it ran out of pages first, so they are a FLOOR; `unavailable` means the tape
    could not be read at all. A gate that treated a floor as a measurement would
    refuse placements on evidence it never gathered.
    """
    status: str                       # "complete" | "truncated" | "unavailable"
    window_sec: float
    by_token: dict[str, dict[float, float]]   # token -> {price(4dp): SELL shares}


# Pages per flow read. One page of the tape covers minutes on a busy token and
# this reader only ever needs the last `window_sec`, so a short walk is enough --
# bounded, because this runs inside the fleet's rotation for every gated market.
FLOW_PAGE_SIZE = 500
FLOW_MAX_PAGES = 3


def recent_sell_flow(condition_id: str, window_sec: float, *,
                     now: Optional[float] = None, session=None,
                     page_size: int = FLOW_PAGE_SIZE,
                     max_pages: int = FLOW_MAX_PAGES) -> SellFlow:
    """Taker SELL shares per token and price inside a bounded window of the tape.

    The question this answers is narrow on purpose: could a seller have REACHED a
    resting bid at this price? A SELL print at or below our price proves somebody
    sold through our level; a BUY print lifts an ask and leaves our bid where it
    was (`recent_trades` makes the same distinction for the same reason).

    Rows are dropped rather than repaired when they are unreadable -- a missing
    side, a non-finite or non-positive size, a stamp outside the window. A stamp
    later than `now + 60` is dropped, never converted: a millisecond epoch lands
    a million years in the future, and reading one as seconds would turn an
    unreadable clock into a wall of fabricated flow.

    Never raises outward: a request failure or a payload that is not a list
    returns `unavailable` with an empty map, and the caller fails OPEN on it.
    """
    sess = _SESSION if session is None else session
    window = float(window_sec)
    ts_now = time.time() if now is None else float(now)
    cutoff = ts_now - window
    newest = ts_now + 60.0
    by_token: dict[str, dict[float, float]] = {}
    seen: set = set()
    pages = max(1, int(max_pages))

    for page_index in range(pages):
        try:
            r = sess.get(TRADES_API,
                         params={"market": condition_id, "limit": page_size,
                                 "offset": page_index * page_size},
                         timeout=TAPE_TIMEOUT)
            rows = r.json()
        except Exception as e:
            log.debug("flow tape fetch failed: %s", e)
            return SellFlow("unavailable", window, {})
        if not isinstance(rows, list):
            # NOT an empty window. A falsy non-list payload (`null`, `{}`) read as
            # `[]` would claim a COMPLETE window with no sells at any price -- the
            # one reading that refuses a placement -- on a response that carried
            # no measurement at all. The ranker's tape reader refuses the same
            # shapes; so does this one.
            log.debug("flow tape response is not a list (got %s)",
                      type(rows).__name__)
            return SellFlow("unavailable", window, {})

        oldest: Optional[float] = None
        for t in rows:
            if not isinstance(t, dict):
                continue
            try:
                raw_ts = t.get("timestamp")
                if raw_ts is None:
                    continue
                ts = float(raw_ts)
            except (TypeError, ValueError):
                continue
            # A stamp that is missing, non-finite or non-positive is garbage, and
            # it must not define the window edge: `or 0.0` turned such a row into
            # epoch 0, which then became `oldest`, so the completeness check read
            # "oldest <= window start" and called a page-bounded count a whole
            # window. A floor read as a measurement is what refuses placements
            # once the gate is enforced.
            if not math.isfinite(ts) or ts <= 0:
                continue
            if oldest is None or ts < oldest:
                oldest = ts
            if ts <= cutoff or ts > newest:
                continue
            side = t.get("side")
            if side is None or str(side).strip().upper() != "SELL":
                continue
            key = (str(t.get("transactionHash") or ""), str(t.get("asset")),
                   t.get("timestamp"), t.get("price"), t.get("size"))
            if key in seen:
                continue
            seen.add(key)
            try:
                p = round(float(t.get("price") or 0), 4)
                size = float(t.get("size") or 0)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(p) or not math.isfinite(size) or size <= 0:
                continue
            level = by_token.setdefault(str(t.get("asset")), {})
            level[p] = level.get(p, 0.0) + size

        # A short page is the end of the tape, and a page whose oldest row is
        # already at or behind the window start has covered the whole window.
        # Either way the reading is complete; anything else means the page limit
        # bounded it and the numbers are a floor.
        if len(rows) < page_size or (oldest is not None and oldest <= cutoff):
            return SellFlow("complete", window, by_token)

    return SellFlow("truncated", window, by_token)


if __name__ == "__main__":
    from core_brain.config import load

    cfg = load()
    m = fetch_live_market(cfg.gamma_host, cfg.series_slug)
    if not m:
        print("no live market right now")
    else:
        rem = m.t_remaining()
        print(f"live: {m.market_slug}  t_remaining={rem:.1f}s")
        print(f"  cond={m.condition_id}")
        print(f"  up_token={m.up_token[:18]}...")
        print(f"  down_token={m.down_token[:18]}...")
        print(f"  tick={m.tick_size}  neg_risk={m.neg_risk}")
