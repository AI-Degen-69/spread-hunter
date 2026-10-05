"""Rank live venue markets by what a merged UP+DOWN pair actually earns.

The strategy buys one UP share and one DOWN share of the same binary market and
merges the pair back into $1.00 of USDC. Profit is therefore

    edge_per_pair = 1.00 - (up_price + down_price)

and the only question a scanner can answer is WHERE that number is positive and
reachable. Two measurements decide it, and they are not the same thing:

* `bid_pair` -- the two best BIDS summed. This is the price the strategy pays,
  because both legs are quoted passively. A live scan on 2026-09-06 found 50 of
  70 markets with `bid_pair` under $1.00.
* `ask_pair` -- the two best ASKS summed. This is what CROSSING would cost. The
  same scan found 0 of 25 sampled markets under $1.00 there, median $1.002.
  Crossing also makes us the taker on both legs, so the venue's
  `fee_rate * p * (1 - p)` is charged twice -- 3.5c a pair mid-book, wider than
  most of the spreads ranked here. `taker_pair_is_profitable` charges it.
  Taking a leg is a booked loss on every market that has ever been measured
  here, which is why that property exists: to keep the fact in the output
  rather than in a comment.

A live measurement on 2026-09-06 settled what `edge_per_pair` actually is. The
two books of a binary market mirror each other -- `ask_UP == 1 - bid_DOWN` --
so the arithmetic collapses:

    bid_pair = bid_UP + bid_DOWN = bid_UP + (1 - ask_UP) = 1 - spread_UP
    edge_per_pair = 1 - bid_pair = spread_UP

The pair "edge" IS the bid-ask spread, renamed. Across 32 live markets the
difference between `edge_per_pair` and the tighter leg's own spread was
0.000c in 32 of 32. A 13c pair edge is a 13c spread: a market nobody is
quoting, not money nobody has noticed.

That identity is not a coincidence, and the reason closes the question. A
binary market has ONE order book, served under two token ids. Checked
level-for-level on 12 live markets -- including books 148 levels deep -- every
UP bid (p, s) appeared as a DOWN ask (1-p, s), same price, same size, 12 of 12.
Selling UP and buying DOWN are the same order. So `dislocation` on a binary
market is not rare, it is IMPOSSIBLE: the two sides cannot come apart because
there are not two sides. It stays in the output as an integrity check -- a
non-zero reading means the venue's own book invariant broke -- not as an
opportunity finder.

Cross-market arbitrage on multi-outcome events is where separate books do
exist. Measured the same day on 8 complete events over $20k of daily volume:
sum-of-asks under $1.00 in 0 of 8, median $1.0215. A further 14 events could
not be priced at all because some outcome had no ask resting. That surface was
checked and is not open either.

What survives every measurement is this: the strategy is two-sided market
making. Profit is the spread, and it is earned only when BOTH legs fill.

Everything else in this module ranks MARKET-MAKING value, not arbitrage:
capturing `edge_per_pair` requires BOTH legs to fill at the bid, which is the
same trade as quoting two-sided and being hit on both. `queue_ahead_usd` is
therefore a COST -- money already resting at the touch that must be consumed
before our own order trades -- and a pair needs both legs, so the constraint is
the leg with MORE queue in front of it. Ranking on `edge * depth` inverts this,
rewarding exactly the markets that are hardest to get filled in.
`spread_capture_score` divides instead:

    spread_capture_score = edge_per_pair * (volume_24h / max(queue_ahead_usd, 1))

read as "cents per pair, times how many times a day the queue in front of us
clears". `volume_24h` is a coarse, backward-looking proxy for flow reaching the
touch -- the best per-market figure the public API exposes -- and it is why the
score ranks rather than predicts. It says nothing about whether both legs fill
in the same moment, which is the strategy's actual binding risk.

Read-only. This module places no orders and holds no signer; running it cannot
reach the venue's trading surface.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

import requests

from core_brain.config import load as load_cfg

log = logging.getLogger("pair_scanner")

GAMMA_HOST = os.environ.get("GAMMA_HOST", "https://gamma-api.polymarket.com")
CLOB_HOST = os.environ.get("CLOB_HOST", "https://clob.polymarket.com")

# (connect, read). A scan walks up to `limit` markets and issues two book calls
# per market, so a single hung host must not hold the whole sweep.
SCAN_TIMEOUT = (3.05, 5.0)

# Half the venue's smallest tick. Float subtraction of two book prices leaves
# residue at 1e-17, and without a floor that residue reads as a dislocation and
# shuffles the ranking. Anything under half a tick is not a price difference.
DISLOCATION_EPS = 0.005

# The venue charges the taker `fee_rate * p * (1 - p)` per share and the
# maker nothing (`crypto_fees_v2: takerOnly=true`). Both numbers are already
# measured and recorded in `MakerConfig`, so the scanner reads them from
# there rather than asking the venue for metadata it already has.
#
# Read LAZILY and with `for_display=True`. Both matter:
#
# * At module scope, `load()` runs on import, so `import core_brain.pair_scanner`
#   inherits every refusal on the money path. Nothing else in `core_brain/`
#   calls `load()` at import time.
# * Without `for_display=True`, `resolve_wide_book_trial` REFUSES whenever
#   `HUNTER_WIDE_BOOK_TRIAL` is exported. That knob is handed to a rehearsal,
#   `Start-Process` copies the operator's environment into every child, and
#   the refusal then aborts the import of a module that holds no signer and
#   places no order. `config.load` documents this exact escape hatch for a
#   read-only consumer.
_TAKER_FEE_RATE: Optional[float] = None


def taker_fee_rate() -> float:
    """The venue's taker fee rate, read once, never at import."""
    global _TAKER_FEE_RATE
    if _TAKER_FEE_RATE is None:
        _TAKER_FEE_RATE = float(load_cfg(for_display=True).fee_rate)
    return _TAKER_FEE_RATE

# Slugs and questions come from the venue and are later printed to a terminal
# and embedded in reports. Restrict them at the boundary so a hostile value
# never reaches either place.
_SAFE_SLUG_RE = re.compile(r"[^A-Za-z0-9._~-]")
_SAFE_TEXT_RE = re.compile(r"[^A-Za-z0-9 ._~:/()'+,%$#&?!-]")

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Mozilla/5.0"})
for _scheme in ("https://", "http://"):
    _SESSION.mount(_scheme, requests.adapters.HTTPAdapter(
        pool_connections=8, pool_maxsize=8, max_retries=0))


@dataclass(frozen=True)
class Candidate:
    """A venue market row that survived parsing, before its book is read."""

    condition_id: str
    slug: str
    question: str
    up_token: str
    down_token: str
    volume_24h: float


@dataclass(frozen=True)
class PairQuote:
    """One market's pair economics at the current touch."""

    condition_id: str
    slug: str
    question: str
    up_token: str
    down_token: str
    volume_24h: float
    bid_pair: float
    ask_up: float
    ask_down: float
    queue_ahead_usd: float
    leg_spread_up: float = 0.0
    leg_spread_down: float = 0.0
    # True only when both legs came back in one `POST /books` carrying the same
    # venue `timestamp`. Two serial reads can straddle a market move no matter
    # how often they are repeated, so they can never support a dislocation.
    one_snapshot: bool = False

    @property
    def edge_per_pair(self) -> float:
        """Dollars earned per merged pair when both legs fill at the touch."""
        return 1.0 - self.bid_pair

    @property
    def ask_pair(self) -> float:
        """What crossing both legs would cost in shares, before fees."""
        return self.ask_up + self.ask_down

    @property
    def taker_fee_per_pair(self) -> float:
        """Match-time taker fee on both legs of a crossed pair.

        Crossing makes us the taker on BOTH legs, and the venue charges each
        one `fee_rate * p * (1 - p)`. Near the middle of the book that is 3.5c
        a pair -- larger than most of the spreads this scanner ranks -- so a
        takeability check that ignores it reports pairs as free money that are
        not. Merging is gasless, so the fee is the whole cost.
        """
        return taker_fee_rate() * sum(
            price * (1.0 - price) for price in (self.ask_up, self.ask_down)
        )

    @property
    def taker_pair_is_profitable(self) -> bool:
        """True only if CROSSING both legs, fees included, lands under $1.00."""
        return self.ask_pair + self.taker_fee_per_pair < 1.0

    @property
    def queue_turns_per_day(self) -> float:
        """How many times a day the money in front of us is consumed."""
        return self.volume_24h / max(self.queue_ahead_usd, 1.0)

    @property
    def dislocation(self) -> float:
        """How far the pair edge exceeds either leg's own spread.

        An integrity check, not an opportunity finder. A binary market serves
        ONE book under two token ids, so this reads zero by construction: a
        pair can only be as cheap as the book is wide. A non-zero reading means
        the venue's own mirror invariant broke, and the number is worth acting
        on for that reason rather than as a trade.

        Reads zero unless both legs came from ONE venue snapshot. Fetched
        serially, the UP response and the DOWN response are two different
        moments, and a market that moves between them yields a pair that
        existed at no single instant -- which is staleness wearing the costume
        of a mispricing. Repeating the pair of reads does not fix it: the
        second pair can straddle a move exactly like the first. Only the
        venue's own batch read, where both books carry the same `timestamp`,
        can support the claim.
        """
        if not self.one_snapshot:
            return 0.0
        raw = self.edge_per_pair - min(self.leg_spread_up, self.leg_spread_down)
        return raw if raw >= DISLOCATION_EPS else 0.0

    @property
    def spread_capture_score(self) -> float:
        """Market-making value: spread weighted by how reachable it is.

        Ranking only. Assumes nothing about both legs filling in one moment,
        which is the strategy's real binding risk.
        """
        return self.edge_per_pair * self.queue_turns_per_day


def _sanitize_slug(slug: str) -> str:
    return _SAFE_SLUG_RE.sub("", slug or "")


def _sanitize_text(text: str) -> str:
    return _SAFE_TEXT_RE.sub("", text or "")


def _as_float(value: Any) -> Optional[float]:
    """A venue number -> float, or None when the venue sent something else.

    NaN and Infinity are "something else". `float("NaN")` parses happily, and
    every comparison against the result is False -- so a NaN volume walks
    straight past `volume_24h < min_volume_24h`, and every score derived from
    it is NaN and sorts above every real market. Reject at the boundary, once,
    rather than guarding each comparison downstream.
    """
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_volume(raw: Any) -> Optional[float]:
    """24h volume -> float, or None when the row must be rejected outright.

    Three cases, and collapsing any two of them is the bug this exists to
    avoid:

    * ABSENT or blank -> 0.0. A market with no measured flow, which the volume
      bar then filters on its merits.
    * NOT A NUMBER (a mangled string) -> 0.0. Same treatment: unusable reading,
      no claim of flow.
    * NaN or INFINITY -> None, reject the row. These PARSE, so `or 0.0` cannot
      tell them from the first two, and every comparison against NaN is False
      -- the row would clear the volume bar and then sort above every real
      market on a NaN score.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return 0.0
    parsed = _as_float(raw)
    if parsed is not None:
        return parsed
    try:
        float(raw)
    except (TypeError, ValueError):
        return 0.0      # never a number; no flow claimed
    return None         # parsed, so it was NaN or Infinity: a venue error


def parse_candidate(row: Any) -> Optional[Candidate]:
    """One gamma market row -> Candidate, or None when the row is unusable.

    Returns None rather than raising: a row the venue mangled is a market to
    skip, not a reason to abort the caller's whole scan.
    """
    if not isinstance(row, dict):
        return None

    condition_id = str(row.get("conditionId") or "")
    if not condition_id:
        return None

    uma_status = str(row.get("umaResolutionStatus") or row.get("uma_resolution_status") or "").strip().lower()
    if uma_status in ("proposed", "disputed", "resolved"):
        return None
    uma_statuses = row.get("umaResolutionStatuses") or row.get("uma_resolution_statuses")
    if uma_statuses:
        raw_list = []
        if isinstance(uma_statuses, (list, tuple)):
            raw_list = uma_statuses
        elif isinstance(uma_statuses, str) and uma_statuses.strip():
            try:
                parsed = json.loads(uma_statuses)
                raw_list = parsed if isinstance(parsed, (list, tuple)) else [parsed]
            except Exception:
                raw_list = [uma_statuses]
        for item in raw_list:
            if str(item or "").strip().lower() in ("proposed", "disputed", "resolved"):
                return None

    raw_tokens = row.get("clobTokenIds")
    tokens: Sequence[Any]
    if isinstance(raw_tokens, str):
        try:
            tokens = json.loads(raw_tokens)
        except (ValueError, TypeError):
            log.debug("unparseable clobTokenIds on %s", condition_id)
            return None
    elif isinstance(raw_tokens, list):
        tokens = raw_tokens
    else:
        return None

    if not isinstance(tokens, list) or len(tokens) != 2:
        return None

    volume = _parse_volume(row.get("volume24hr"))
    if volume is None:
        return None

    return Candidate(
        condition_id=condition_id,
        slug=_sanitize_slug(str(row.get("slug") or "")),
        question=_sanitize_text(str(row.get("question") or "")),
        up_token=str(tokens[0]),
        down_token=str(tokens[1]),
        volume_24h=volume,
    )


def _touch(book: Any, side: str) -> Optional[tuple[float, float]]:
    """Best (price, size) on one side of a book, or None when it is unusable."""
    if not isinstance(book, dict):
        return None
    levels = book.get(side)
    if not isinstance(levels, list) or not levels:
        return None

    parsed: list[tuple[float, float]] = []
    for level in levels:
        if not isinstance(level, dict):
            return None
        price = _as_float(level.get("price"))
        size = _as_float(level.get("size"))
        if price is None or size is None:
            return None
        # A binary share is worth between $0.00 and $1.00, and a level with no
        # size is not a level. NaN is the dangerous one: every comparison
        # against it is False, so an unfiltered NaN sails through the
        # `bid_pair >= 1.0` guard below and ranks a market that has no price.
        if not (math.isfinite(price) and math.isfinite(size)):
            return None
        if not 0.0 <= price <= 1.0 or size <= 0.0:
            return None
        parsed.append((price, size))

    # Gamma returns bids ascending and asks descending in places; sort rather
    # than trust the order, so "best" means best on both sides.
    return max(parsed) if side == "bids" else min(parsed)


def _is_one_snapshot(up_book: Any, down_book: Any) -> bool:
    """True when both books carry the same non-empty venue `timestamp`."""
    if not (isinstance(up_book, dict) and isinstance(down_book, dict)):
        return False
    stamp = up_book.get("timestamp")
    return bool(stamp) and stamp == down_book.get("timestamp")


def build_quote(
    candidate: Candidate,
    up_book: Any,
    down_book: Any,
) -> Optional[PairQuote]:
    """Both books -> PairQuote, or None when no profitable pair exists there."""
    up_bid = _touch(up_book, "bids")
    down_bid = _touch(down_book, "bids")
    up_ask = _touch(up_book, "asks")
    down_ask = _touch(down_book, "asks")
    if not (up_bid and down_bid and up_ask and down_ask):
        return None

    # A book whose ask sits at or below its bid is crossed or locked, which no
    # live CLOB serves -- it is a half-stale `/book` response (fresh asks,
    # stale bids). Left through, the negative leg spread makes `dislocation`
    # positive, and because `rank` orders on `dislocation` first, a transport
    # artifact lands at the top of the table under a line claiming the venue
    # broke its own mirror invariant.
    if up_ask[0] <= up_bid[0] or down_ask[0] <= down_bid[0]:
        log.debug("crossed or locked book on %s, skipping", candidate.condition_id)
        return None

    bid_pair = up_bid[0] + down_bid[0]
    if bid_pair >= 1.0:
        return None

    # A pair needs BOTH legs, so the binding constraint is the leg with MORE
    # money resting in front of us, not less.
    queue_ahead = max(up_bid[0] * up_bid[1], down_bid[0] * down_bid[1])

    return PairQuote(
        condition_id=candidate.condition_id,
        slug=candidate.slug,
        question=candidate.question,
        up_token=candidate.up_token,
        down_token=candidate.down_token,
        volume_24h=candidate.volume_24h,
        bid_pair=bid_pair,
        ask_up=up_ask[0],
        ask_down=down_ask[0],
        queue_ahead_usd=queue_ahead,
        leg_spread_up=up_ask[0] - up_bid[0],
        leg_spread_down=down_ask[0] - down_bid[0],
        one_snapshot=_is_one_snapshot(up_book, down_book),
    )


def rank(quotes: Iterable[PairQuote]) -> list[PairQuote]:
    """Genuine dislocations first, then market-making value.

    A dislocated pair is cheap for a reason other than a wide book, so it
    outranks any amount of spread-capture value. Below that, ranking falls back
    to `spread_capture_score`.
    """
    return sorted(
        quotes,
        key=lambda q: (q.dislocation, q.spread_capture_score),
        reverse=True,
    )


def _fetch_book(token_id: str, clob_host: str, session: Any) -> Any:
    try:
        r = session.get(f"{clob_host}/book", params={"token_id": token_id},
                        timeout=SCAN_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as exc:
        log.debug("book fetch failed for %s: %s", token_id[:12], exc)
        return None


def _fetch_pair_books(
    candidate: Candidate,
    clob_host: str,
    session: Any,
) -> tuple[Any, Any]:
    """Both legs, preferring the venue's atomic batch read.

    `POST /books` returns every requested book from one server moment: both
    entries carry the same `timestamp`, which is the only evidence available
    that the two legs existed together. Falling back to two serial `GET /book`
    calls still ranks the market for spread capture; it just cannot support a
    dislocation, and `_is_one_snapshot` sees no matching timestamps and says so.
    """
    tokens = (candidate.up_token, candidate.down_token)
    batch = _fetch_books(tokens, clob_host, session)
    if batch is not None:
        return batch[tokens[0]], batch[tokens[1]]
    return (_fetch_book(tokens[0], clob_host, session),
            _fetch_book(tokens[1], clob_host, session))


def _fetch_books(
    tokens: Sequence[str],
    clob_host: str,
    session: Any,
) -> Optional[dict[str, Any]]:
    """Every requested book in one call, keyed by token id, or None."""
    try:
        r = session.post(
            f"{clob_host}/books",
            json=[{"token_id": t} for t in tokens],
            timeout=SCAN_TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json()
    except (requests.RequestException, ValueError, AttributeError, TypeError) as exc:
        log.debug("batch book read unavailable: %s", exc)
        return None

    if not isinstance(payload, list):
        return None
    books: dict[str, Any] = {}
    for book in payload:
        if isinstance(book, dict) and book.get("asset_id"):
            books[str(book["asset_id"])] = book
    # A partial batch is not a batch: the missing leg would have to be fetched
    # separately, and the pair would no longer share a moment.
    return books if all(t in books for t in tokens) else None


def scan(
    min_volume_24h: float = 50_000.0,
    limit: int = 70,
    gamma_host: str = GAMMA_HOST,
    clob_host: str = CLOB_HOST,
    session: Any = None,
) -> list[PairQuote]:
    """Live markets ranked by pair economics. Read-only; places no orders."""
    session = session or _SESSION

    # Guarded the same way `_fetch_book` is. A 502 HTML error page or a
    # truncated body during a venue outage otherwise leaves `scan` through
    # `HTTPError` or `ValueError`, and `_main` installs no handler -- the
    # operator gets a traceback where every other mangled venue response is
    # a skipped row.
    try:
        r = session.get(
            f"{gamma_host}/markets",
            params={"closed": "false", "active": "true", "limit": 500,
                    "order": "volume24hr", "ascending": "false"},
            timeout=SCAN_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("market list unavailable from %s: %s", gamma_host, exc)
        return []
    if not isinstance(rows, list):
        return []

    quotes: list[PairQuote] = []
    for row in rows:
        if len(quotes) >= limit:
            break
        candidate = parse_candidate(row)
        if candidate is None or candidate.volume_24h < min_volume_24h:
            continue
        up_book, down_book = _fetch_pair_books(candidate, clob_host, session)
        quote = build_quote(candidate, up_book, down_book)
        if quote is not None:
            quotes.append(quote)

    return rank(quotes)


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--min-volume", type=float, default=50_000.0,
                    help="24h volume bar a market must clear to be booked")
    ap.add_argument("--limit", type=int, default=70,
                    help="stop after this many priced markets")
    ap.add_argument("--top", type=int, default=20, help="rows to print")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    quotes = scan(min_volume_24h=args.min_volume, limit=args.limit)

    print(f"{'spread':>8}{'disloc':>8}{'queue$':>11}{'turns/day':>11}"
          f"{'score':>9}{'pair':>8}{'vol24h':>13}  market")
    for q in quotes[:args.top]:
        print(f"{q.edge_per_pair * 100:>7.2f}c{q.dislocation * 100:>7.2f}c"
              f"{q.queue_ahead_usd:>11,.0f}{q.queue_turns_per_day:>11,.1f}"
              f"{q.spread_capture_score:>9.2f}{q.bid_pair:>8.3f}"
              f"{q.volume_24h:>13,.0f}  {q.question[:42]}")

    real = [q for q in quotes if q.dislocation > 0]
    takeable = [q for q in quotes if q.taker_pair_is_profitable]
    print(f"\n{len(quotes)} markets with a pair under $1.00 at the bid; "
          f"{len(takeable)} of them are still under $1.00 at the ask once "
          f"both taker fees are charged.")
    print(f"{len(real)} dislocated. A binary market has one book served under "
          f"two token ids, so this reads 0 unless the venue's book invariant "
          f"broke.")
    print("The 'spread' column is the bid-ask spread by identity, not free "
          "money: capturing it needs BOTH legs filled at the bid.")


if __name__ == "__main__":
    _main()
