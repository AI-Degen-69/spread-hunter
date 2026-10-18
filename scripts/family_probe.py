"""Measure the market FAMILIES the funnel audit left open, over a day.

The audit that produced this script scanned the global taker tape once: 725
markets traded in ten minutes, 60 of them were on the list the bot reads, and
inside the families it could not see -- Dota 2 "Game 2 Winner", CS "Map 2",
weather, five-minute crypto -- queues cleared in one to one and a half minutes.
Three of those markets cleared both the spread bar and the queue bar. Every one
of them is refused today, by the blocked-keyword arm of `identity_allowed` or
by the $125K volume gate.

That is a SNAPSHOT, and a snapshot cannot decide this. The families in question
are made of markets that open and close inside the same day: a "Game 2 Winner"
exists for the length of one game, a five-minute crypto market for five
minutes. Ten minutes of tape samples whichever handful happened to be open, at
whatever hour the audit ran, and reports the queue of that handful. The
question is whether the FAMILY is reliably quotable -- across the hours the
venue is busy and the hours it is not -- and the only instrument that answers
it is a run that samples the same families repeatedly for a day.

So this records per family, not per market. Individual markets are the sample;
the family is the unit of the finding.

READ-ONLY, AND OUTSIDE THE MONEY PATH. It opens no client that can sign, makes
only HTTP GETs against the public venue APIs, writes its own store, and refuses
`data/orders.db` by name. It cannot place, cancel, or price an order.

    python scripts/family_probe.py --hours 24            # the real run
    python scripts/family_probe.py --hours 0.05          # smoke, one cycle
    python scripts/family_probe_report.py                # read what it found

The store is append-only and every cycle is independent, so an interrupted run
resumes by being started again against the same `--db`.
"""
from __future__ import annotations

import argparse
import calendar
import json
import logging
import math
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import concurrent.futures as cf

# Running `python scripts/family_probe.py` puts `scripts/` on the path, not the
# repo root, so `core_brain` and `scoring` are invisible without this.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger("family_probe")

REFUSED_STORES = ("orders.db",)

# Half a nanocent: enough to absorb `1.0 - price` binary error, far below any
# tick the venue quotes on.
_PRICE_EPS = 1e-9

# How far back the tape is read when measuring the drain rate at our level.
# Long enough that a market trading a few times an hour still registers, short
# enough that the rate describes now rather than the market's whole life. An
# order this project would actually rest is judged over 15 minutes
# (`pairs_exit_window_sec`), so an hour is four of those -- the shortest window
# that does not turn one quiet stretch into "nothing ever trades here".
DEFAULT_TAPE_WINDOW_MIN = 60.0

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# Three sorts, unioned, because one of them cannot see the population under
# test. Ranked by 24-hour volume, a five-minute crypto market is invisible: it
# has existed for five minutes and its volume is a rounding error next to an
# election market. Soonest-to-resolve is where short-lived markets live, and
# most-recently-opened is where the next batch of them appears -- measured
# 2026-09-03, one page of `endDate` ascending held 55 five-minute crypto
# markets and one page of `startDate` descending held 10 CS map submarkets,
# against zero of either in a page ranked by volume.
# `end_date_min` on the soonest-to-resolve sort is not optional. Without it
# that sort returns markets whose end date is in the PAST and which the venue
# has simply not closed: measured 2026-09-03, the first page held five-minute
# crypto markets stamped December 2025, every one with an empty book and no
# tape. They look exactly like the population under test and contain none of
# it.
OPEN_SORTS = (("volume24hr", "false", False), ("endDate", "true", True),
              ("startDate", "false", False))

SCHEMA = """
CREATE TABLE IF NOT EXISTS probe_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    run_id TEXT NOT NULL,
    cycle INTEGER NOT NULL,
    condition_id TEXT NOT NULL,
    family TEXT NOT NULL,
    slug TEXT, question TEXT,
    series_title TEXT, event_title TEXT, market_group TEXT, category TEXT,
    tick REAL, volume_24h REAL, days_to_resolve REAL,
    gate_pass INTEGER NOT NULL, gate_reason TEXT,
    best_bid_up REAL, best_ask_up REAL,
    best_bid_down REAL, best_ask_down REAL,
    spread_up REAL, spread_down REAL,
    touch_pair_cost REAL,
    q_bid_up REAL, q_ask_up REAL, q_bid_down REAL, q_ask_down REAL,
    tape_span_min REAL, tape_window_min REAL,
    tape_prints INTEGER, tape_prints_new INTEGER,
    vol_at_bid_up REAL, vol_at_ask_up REAL,
    qmin_bid_up REAL, qmin_ask_up REAL, qmin_worst REAL,
    book_ok INTEGER NOT NULL DEFAULT 0,
    is_bootstrap INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS probe_cycles (
    cycle INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    ts REAL NOT NULL,
    tape_prints INTEGER, markets_traded INTEGER,
    candidates INTEGER, sampled INTEGER, seconds REAL
);
CREATE INDEX IF NOT EXISTS ix_probe_family ON probe_samples(family);
CREATE INDEX IF NOT EXISTS ix_probe_cid ON probe_samples(condition_id);
CREATE INDEX IF NOT EXISTS ix_probe_ts ON probe_samples(ts);
"""


# --- family classification ----------------------------------------------------

_ESPORT_RE = re.compile(
    r"\b(dota\s*2?|cs\s*2|csgo|counter[\s-]*strike|league\s*of\s*legends|lol|"
    r"valorant|rocket\s*league|overwatch|starcraft)\b", re.IGNORECASE)
_UNIT_RE = re.compile(r"\b(game|map|set|round)\s*(\d+)\b", re.IGNORECASE)
_CRYPTO_RE = re.compile(
    r"\b(bitcoin|btc|ethereum|eth|solana|sol|xrp|ripple|dogecoin|doge)\b",
    re.IGNORECASE)
# `5m` and `1h` are how the venue writes these in a slug (`btc-updown-5m-...`),
# and the long forms are how it writes them in a question. Both are the same
# family and matching only one of them splits it in half.
_WINDOW_RE = re.compile(
    r"\b(\d+)\s*(?:-|\s)?\s*(minutes?|mins?|m|hours?|hourly|h)\b",
    re.IGNORECASE)
_WINDOW_UNIT = {"m": "min", "min": "min", "mins": "min", "minute": "min",
                "minutes": "min", "h": "hour", "hour": "hour",
                "hours": "hour", "hourly": "hour"}
_WEATHER_RE = re.compile(
    r"\b(temperature|highest\s*temp|rain|rainfall|snow|snowfall|hurricane|"
    r"weather|degrees|celsius|fahrenheit)\b", re.IGNORECASE)
_SPORT_RE = re.compile(
    r"\b(mlb|nfl|nba|nhl|ufc|atp|wta|itf|fifa|premier\s*league|soccer|tennis|"
    r"baseball|basketball|football|hockey|boxing)\b", re.IGNORECASE)
_MACRO_RE = re.compile(
    r"\b(fed|inflation|cpi|gdp|election|president|senate|congress|tariff|"
    r"interest\s*rate|recession)\b", re.IGNORECASE)
_DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}(?:am|pm)|january|february|march|april|may|"
    r"june|july|august|september|october|november|december)\b", re.IGNORECASE)


def _norm(value: object) -> str:
    return re.sub(r"[\s_-]+", " ", str(value or "")).strip().lower()


def slug_skeleton(slug: object) -> str:
    """The slug with every instance-specific token replaced.

    Dates and digit runs are what make two members of the same family look
    like two families, so both are collapsed. Team and ticker names survive,
    which is why this is the FALLBACK and not the rule: it over-splits, and
    over-splitting is visible in the report as a family of one.
    """
    # Dates are stripped from the RAW slug: `_norm` turns "2026-09-03" into
    # "2026 09 03", which the ISO arm can no longer see.
    text = _norm(_DATE_RE.sub(" ", str(slug or "")))
    text = re.sub(r"\d+", "#", text)
    return re.sub(r"\s+", "-", text.strip()).strip("-") or "unknown"


def family_key(title: object = "", slug: object = "",
               series_title: object = "", event_title: object = "") -> str:
    """Name the recurring template this market is one instance of.

    Ordered, and the order is the point: a Dota "Game 2 Winner" also matches
    the esports keyword and the sports keyword, and the finding is about the
    submarket template, not about Dota.
    """
    text = " ".join(_norm(v) for v in (title, slug, series_title, event_title))
    unit = _UNIT_RE.search(text)
    esport = _ESPORT_RE.search(text)
    if unit and esport:
        game = re.sub(r"\s+", "", esport.group(1))
        return f"esports-{game}-{unit.group(1).lower()}-winner"
    if unit:
        return f"submarket-{unit.group(1).lower()}-winner"
    if esport:
        # The match itself, not one of its games. A different instrument from
        # the submarket above and gated differently, so a different family.
        return "esports-" + re.sub(r"\s+", "", esport.group(1)) + "-match"
    crypto = _CRYPTO_RE.search(text)
    window = _WINDOW_RE.search(text)
    if crypto and window:
        unit_name = _WINDOW_UNIT.get(_norm(window.group(2)), "win")
        return f"crypto-{window.group(1)}{unit_name}"
    if _WEATHER_RE.search(text):
        return "weather"
    if crypto:
        return "crypto-other"
    if _MACRO_RE.search(text):
        return "macro-politics"
    sport = _SPORT_RE.search(text)
    if sport:
        return "sports-" + re.sub(r"\s+", "", _norm(sport.group(1)))
    return "other:" + slug_skeleton(slug)


# --- gates --------------------------------------------------------------------


def gate_verdict(meta: dict, min_volume_usd: float,
                 max_days: float) -> tuple[bool, str]:
    """Would the bot's own selector admit this market, and if not, which gate?

    The reason string is the deliverable. "Blocked keyword" and "under the
    volume bar" are different findings with different fixes, and a run that
    recorded only a boolean would have to be run again to tell them apart.
    """
    from scoring.selector import identity_allowed

    ok, reason = identity_allowed(
        meta.get("question"), meta.get("slug"), meta.get("category"),
        meta.get("market_type"), meta.get("market_group"),
        meta.get("series_title"), meta.get("event_title"))
    if not ok:
        return False, reason
    vol = meta.get("volume_24h")
    if vol is None:
        return False, "volume unknown"
    if vol < min_volume_usd:
        return False, f"24h volume {vol:,.0f} under bar {min_volume_usd:,.0f}"
    days = meta.get("days_to_resolve")
    if days is None:
        return False, "horizon unknown"
    if days < 0:
        return False, "horizon passed"
    if days > max_days:
        return False, f"horizon {days:.1f}d over {max_days:.0f}d"
    return True, ""


# --- pure measurement ---------------------------------------------------------


def _levels(side: object) -> dict[float, float]:
    """Price to size for one side, skipping levels the venue sent malformed.

    A level with no `size`, or a `price`/`size` that is not a finite number
    above zero, is dropped rather than raised. The alternative loses the whole
    cycle: the error would travel up through `measure` and `build_rows` into
    `run_cycle`, which logs "cycle failed" and discards every other market
    measured in it.

    `float()` accepts `"nan"` and `"inf"`, and a NaN price silently poisons
    every comparison downstream: `max(bids)` can return NaN, the crossed-book
    guard in `measure` compares false against it, and the sample is stored as
    if the book were sound. Non-finite values are refused here, at the edge.
    """
    out: dict[float, float] = {}
    for level in (side or []):
        if not isinstance(level, dict) or not level.get("price"):
            continue
        try:
            price = float(level["price"])
            size = float(level["size"])
        except (TypeError, ValueError, KeyError):
            continue
        if (not (math.isfinite(price) and math.isfinite(size))
                or price <= 0.0 or size <= 0.0):
            continue
        out[price] = size
    return out


def touch(book: object) -> tuple[Optional[float], Optional[float],
                                 float, float]:
    """Best bid, best ask, and the size resting at each."""
    if not isinstance(book, dict):
        return None, None, 0.0, 0.0
    bids = _levels(book.get("bids"))
    asks = _levels(book.get("asks"))
    if not bids or not asks:
        return None, None, 0.0, 0.0
    bb, ba = max(bids), min(asks)
    return bb, ba, bids[bb], asks[ba]


def new_trades(rows: Iterable[dict], since_ts: float) -> list[dict]:
    """Only the prints this cycle has not already counted.

    This is the LIVENESS signal, not the queue measurement. The venue returns
    a rolling recent window, so consecutive cycles overlap; the count of prints
    that are genuinely new since the last cycle is what says whether a family
    has members trading right now, which is the question a snapshot could not
    answer. The queue itself is measured on the full window below, because a
    five-minute slice of a thin market holds nought or one print and every
    queue estimate built on it is infinite.
    """
    out = []
    for row in rows or []:
        try:
            ts = float(row["timestamp"])
        except (TypeError, ValueError, KeyError):
            continue
        if ts > since_ts:
            out.append(row)
    return out


def _finite_window(window_min: float) -> float:
    """The tape window as a positive, finite float, or a refusal.

    `float()` happily accepts zero, a negative, `nan` and `inf`.
    `queue_minutes_at` turns a zero or negative window into `math.inf`, and a
    `nan` slips past every comparison it makes and comes out the other side as
    a `nan` queue estimate that `_finite` does not catch. A bad window is
    therefore not a bad number downstream -- it is a measurement that looks
    valid and is not, so it is refused here.
    """
    try:
        window = float(window_min)
    except (TypeError, ValueError):
        raise ValueError(
            f"tape window must be a number, got {window_min!r}") from None
    if not math.isfinite(window) or window <= 0.0:
        raise ValueError(
            f"tape window must be finite and greater than zero, "
            f"got {window_min!r}")
    return window


def tape_at_touch(rows: list[dict], best_bid: float, best_ask: float,
                  window_min: float = DEFAULT_TAPE_WINDOW_MIN,
                  now_ts: Optional[float] = None
                  ) -> tuple[float, float, float, int]:
    """Volume that traded AT our two levels in the LAST `window_min` minutes.

    A trade is attributed to the bid side when it hits down into our bid and to
    the ask side when it lifts up through our ask; a maker resting there is the
    counterparty in exactly those two cases and in no others. Prices are
    normalised to the UP token, so a NO print at 0.40 is read as 0.60.

    BOUNDED, AND THIS IS THE POINT. The venue returns whatever tape history it
    holds -- spans of 46,000 minutes, a month, were recorded on live markets --
    and dividing a month of volume by a month of minutes produces an average
    rate no 15-minute order will ever see. Measured that way the probe admitted
    173 moments whose queues it said would clear inside 10 minutes; 97% of
    those legs then drained less than 1% of what they needed. The rate has to
    be measured over a window comparable to how long an order actually rests.

    The denominator is the WINDOW, not the spread of the prints inside it. Two
    prints three minutes apart inside an hour is 3 minutes of tape only if you
    are willing to claim the other 57 minutes did not happen; they did, and
    nothing traded in them.
    """
    window = _finite_window(window_min)
    horizon = window * 60.0
    now = time.time() if now_ts is None else float(now_ts)
    floor = now - horizon
    recent = []
    for row in rows:
        # `is not None`, not truthiness: a timestamp of 0 is a real reading and
        # dropping it silently collapses the span to zero, which reads
        # downstream as "no queue data" on a market that had plenty.
        try:
            stamp = float(row["timestamp"])
        except (TypeError, ValueError, KeyError):
            continue
        if stamp >= floor:
            recent.append(row)
    if not recent:
        return 0.0, 0.0, window, 0
    span_min = window
    vol_bid = vol_ask = 0.0
    for row in recent:
        try:
            price, size = float(row["price"]), float(row["size"])
        except (TypeError, ValueError, KeyError):
            continue
        is_no = str(row.get("outcome") or "").lower().startswith("n")
        up_price = (1.0 - price) if is_no else price
        lifts = (str(row.get("side") or "").upper() == "BUY") != is_no
        # Compared with a tolerance because `1.0 - 0.58` is 0.42000000000000004
        # in binary floating point. Without it, every NO print whose mirror
        # lands exactly on our level is discarded -- and a level the tape hits
        # exactly is the one case this function exists to count.
        if lifts and up_price >= best_ask - _PRICE_EPS:
            vol_ask += size
        elif (not lifts) and up_price <= best_bid + _PRICE_EPS:
            vol_bid += size
    return vol_bid, vol_ask, span_min, len(recent)


# Every measurement key, so a row without a book has the same shape as a row
# with one. Callers then read one dict, not two.
_BLANK_MEASURE = {
    "best_bid_up": None, "best_ask_up": None, "best_bid_down": None,
    "best_ask_down": None, "spread_up": None, "spread_down": None,
    "touch_pair_cost": None, "q_bid_up": None, "q_ask_up": None,
    "q_bid_down": None, "q_ask_down": None, "tape_span_min": None,
    "tape_window_min": None,
    "vol_at_bid_up": None, "vol_at_ask_up": None, "qmin_bid_up": None,
    "qmin_ask_up": None, "qmin_worst": None,
}


def _finite(value: float) -> Optional[float]:
    return None if value == float("inf") else round(value, 2)


def measure(up_book: object, down_book: object, rows: list[dict],
            prints_new: int = 0,
            window_min: float = DEFAULT_TAPE_WINDOW_MIN,
            now_ts: Optional[float] = None) -> Optional[dict]:
    """Everything one market contributes to one cycle, or None if unreadable.

    `rows` is whatever tape history the venue returned; only the last
    `window_min` minutes of it are counted, and that window is the denominator
    of the queue rate. Consecutive cycles are therefore overlapping readings of
    a recent rate rather than of the market's lifetime average. `prints_new` is
    the count of those prints the previous cycle had not already seen.
    """
    from scoring.selector import queue_minutes_at

    bb_up, ba_up, q_bid_up, q_ask_up = touch(up_book)
    if bb_up is None or ba_up is None or ba_up <= bb_up:
        return None
    bb_dn, ba_dn, q_bid_dn, q_ask_dn = touch(down_book)
    vol_bid, vol_ask, span_min, prints = tape_at_touch(
        rows, bb_up, ba_up, window_min=window_min, now_ts=now_ts)
    qmin_bid = queue_minutes_at(q_bid_up, vol_bid, span_min)
    qmin_ask = queue_minutes_at(q_ask_up, vol_ask, span_min)
    return dict(
        best_bid_up=bb_up, best_ask_up=ba_up,
        best_bid_down=bb_dn, best_ask_down=ba_dn,
        spread_up=round(ba_up - bb_up, 4),
        spread_down=(None if bb_dn is None or ba_dn is None
                     else round(ba_dn - bb_dn, 4)),
        touch_pair_cost=(None if bb_dn is None else round(bb_up + bb_dn, 4)),
        q_bid_up=q_bid_up, q_ask_up=q_ask_up,
        q_bid_down=q_bid_dn, q_ask_down=q_ask_dn,
        tape_span_min=round(span_min, 2), tape_window_min=float(window_min),
        tape_prints=prints,
        tape_prints_new=prints_new,
        vol_at_bid_up=round(vol_bid, 1), vol_at_ask_up=round(vol_ask, 1),
        qmin_bid_up=_finite(qmin_bid), qmin_ask_up=_finite(qmin_ask),
        qmin_worst=_finite(max(qmin_bid, qmin_ask)))


# --- store --------------------------------------------------------------------


def refuse_production_store(db_path: Path) -> None:
    """Refuse the production registry by name, before connecting."""
    if db_path.name in REFUSED_STORES:
        raise SystemExit(
            f"refusing to write {db_path}: that is the production registry")


def open_store(db_path: Path) -> sqlite3.Connection:
    refuse_production_store(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    _add_missing_columns(conn)
    conn.commit()
    return conn


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Bring a store written by an older probe up to the current schema.

    The store is resumable by design, so a run started against last week's
    file must not fail on a column that did not exist then. The added column
    stays NULL on those rows, which is the honest reading: their rate was
    measured over the venue's whole tape, not over a bounded window, and a
    report that mixes the two is comparing different units.
    """
    have = {row[1] for row in conn.execute("PRAGMA table_info(probe_samples)")}
    if "tape_window_min" not in have:
        conn.execute("ALTER TABLE probe_samples ADD COLUMN tape_window_min REAL")


def last_seen(conn: sqlite3.Connection) -> dict[str, float]:
    """Newest sample time per market, so a resumed run does not double-count."""
    rows = conn.execute(
        "SELECT condition_id, MAX(ts) FROM probe_samples GROUP BY condition_id"
    ).fetchall()
    return {str(cid): float(ts) for cid, ts in rows if ts is not None}


def next_cycle(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(cycle) FROM probe_cycles").fetchone()
    return int(row[0] or 0) + 1


_COLUMNS = (
    "ts", "run_id", "cycle", "condition_id", "family", "slug", "question",
    "series_title", "event_title", "market_group", "category", "tick",
    "volume_24h", "days_to_resolve", "gate_pass", "gate_reason",
    "best_bid_up", "best_ask_up", "best_bid_down", "best_ask_down",
    "spread_up", "spread_down", "touch_pair_cost", "q_bid_up", "q_ask_up",
    "q_bid_down", "q_ask_down", "tape_span_min", "tape_window_min",
    "tape_prints",
    "tape_prints_new",
    "vol_at_bid_up", "vol_at_ask_up", "qmin_bid_up", "qmin_ask_up",
    "qmin_worst", "book_ok", "is_bootstrap")


def write_samples(conn: sqlite3.Connection, rows: list[dict]) -> None:
    if not rows:
        return
    placeholders = ",".join("?" for _ in _COLUMNS)
    conn.executemany(
        "INSERT INTO probe_samples (" + ",".join(_COLUMNS) + ") "
        "VALUES (" + placeholders + ")",
        [tuple(row.get(column) for column in _COLUMNS) for row in rows])
    conn.commit()


def write_cycle(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO probe_cycles "
        "(cycle, run_id, ts, tape_prints, markets_traded, candidates, "
        " sampled, seconds) VALUES (?,?,?,?,?,?,?,?)",
        (row["cycle"], row["run_id"], row["ts"], row["tape_prints"],
         row["markets_traded"], row["candidates"], row["sampled"],
         row["seconds"]))
    conn.commit()


# --- venue reads --------------------------------------------------------------


class Throttle:
    """A shared ceiling on request rate, so a day-long run stays polite."""

    def __init__(self, per_second: float) -> None:
        self._gap = 0.0 if per_second <= 0 else 1.0 / per_second
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        if self._gap <= 0:
            return
        with self._lock:
            now = time.monotonic()
            due = max(now, self._next)
            self._next = due + self._gap
        delay = due - time.monotonic()
        if delay > 0:
            time.sleep(delay)


def make_session(per_second: float) -> Callable[[str, dict], Any]:
    """A throttled GET that returns parsed JSON, or None on any failure."""
    import requests

    session = requests.Session()
    session.headers["User-Agent"] = "spread-hunter-family-probe/1.0"
    # Match the widest worker pool below, or urllib3 discards live connections
    # and reopens them, which is slower and noisier than the run needs to be.
    adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=32)
    session.mount("https://", adapter)
    throttle = Throttle(per_second)

    def get(url: str, params: dict) -> Any:
        for attempt in range(3):
            throttle.wait()
            try:
                response = session.get(url, params=params, timeout=30)
                if response.status_code == 429:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                return response.json()
            except Exception as exc:                        # noqa: BLE001
                log.debug("GET %s failed: %s", url, exc)
                time.sleep(0.5 * (attempt + 1))
        return None

    return get


def sweep_tape(get: Callable, pages: int) -> list[dict]:
    """Every taker print the venue will show, deduplicated."""
    def page(offset: int) -> list[dict]:
        rows = get(DATA_API + "/trades", {"limit": 500, "offset": offset})
        return rows if isinstance(rows, list) else []

    seen, tape = set(), []
    with cf.ThreadPoolExecutor(8) as pool:
        for rows in pool.map(page, range(0, pages * 500, 500)):
            for row in rows:
                key = (row.get("transactionHash"), row.get("asset"),
                       row.get("timestamp"), row.get("size"))
                if key not in seen:
                    seen.add(key)
                    tape.append(row)
    return tape


def _one_meta(market: dict) -> Optional[dict]:
    cid = market.get("conditionId")
    if not cid:
        return None
    try:
        tokens = json.loads(market.get("clobTokenIds") or "[]")
    except (TypeError, ValueError):
        tokens = []
    end = market.get("endDate")
    days = None
    if end:
        try:
            stamp = calendar.timegm(
                time.strptime(str(end)[:19], "%Y-%m-%dT%H:%M:%S"))
            days = (stamp - time.time()) / 86400.0
        except (TypeError, ValueError):
            days = None
    return dict(
        condition_id=str(cid), tokens=tokens,
        question=market.get("question") or "", slug=market.get("slug") or "",
        category=market.get("category") or "",
        market_type=market.get("marketType") or "",
        market_group=market.get("groupItemTitle") or "",
        series_title=(market.get("series") or [{}])[0].get("title", "")
        if isinstance(market.get("series"), list) else "",
        event_title=(market.get("events") or [{}])[0].get("title", "")
        if isinstance(market.get("events"), list) else "",
        tick=float(market.get("orderPriceMinTickSize") or 0.01),
        volume_24h=float(market.get("volume24hr") or 0.0),
        days_to_resolve=days)


def fetch_open_markets(get: Callable, pages: int) -> dict[str, dict]:
    """Every market that is OPEN right now, busiest first.

    The candidate set is drawn from here rather than from the tape, and the
    reason is the whole design of this run. The busiest markets on the global
    tape are five-minute crypto markets that have ALREADY EXPIRED by the time
    the sweep sees their prints -- Gamma will not return them without
    `closed=true`, and a closed market has no book to quote into. Ranking
    candidates by tape prints therefore spends the cycle on markets that
    cannot be traded, and reports nothing about the family's live members.

    The tape still decides the ORDER: it is the only evidence of which
    families are actually moving. It just no longer decides membership.
    """
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def page(job: tuple[str, str, bool, int]) -> list[dict]:
        order, ascending, future_only, offset = job
        params = {"closed": "false", "active": "true", "order": order,
                  "ascending": ascending, "limit": 100, "offset": offset}
        if future_only:
            params["end_date_min"] = now_iso
        rows = get(GAMMA_API + "/markets", params)
        if isinstance(rows, dict):
            rows = rows.get("data") or []
        return rows if isinstance(rows, list) else []

    jobs = [(order, ascending, future_only, offset)
            for order, ascending, future_only in OPEN_SORTS
            for offset in range(0, pages * 100, 100)]
    out: dict[str, dict] = {}
    with cf.ThreadPoolExecutor(8) as pool:
        for rows in pool.map(page, jobs):
            for market in rows:
                meta = _one_meta(market)
                # An end date in the past on an "open" market is the venue
                # having not closed it yet, not a market anyone can quote.
                if meta and (meta["days_to_resolve"] or 0.0) >= 0:
                    out[meta["condition_id"]] = meta
    return out


def _stratum(family: str) -> str:
    return "other" if family.startswith("other:") else family


def rank_candidates(open_markets: dict[str, dict], prints: dict[str, int],
                    limit: int) -> list[str]:
    """Open markets, one family at a time, best member of each first.

    Straight ranking by tape prints and volume cannot see the families this run
    was built for. A five-minute crypto market that is OPEN has by definition
    existed for under five minutes: its 24-hour volume is negligible and its
    print count is near zero, because the prints on the tape belong to the
    expired market it replaced. Ranked against an election market it never
    appears, and the family reads as absent when it is merely small.

    So the budget is spent round-robin across families instead of top-down
    across markets. Every open family gets its best member before any family
    gets its second, which is the sampling a family-level finding requires.
    """
    grouped: dict[str, list[str]] = {}
    for cid, meta in open_markets.items():
        family = family_key(meta.get("question"), meta.get("slug"),
                            meta.get("series_title"), meta.get("event_title"))
        # Every unnamed market is ONE stratum, not one each. The `other:`
        # fallback keys on the slug skeleton, so left alone it produces a
        # hundred families of one member, and round-robin then spends the whole
        # budget giving each of them a turn before any named family gets a
        # second. The row still records the fine-grained key, so the report can
        # regroup without another day of measurement.
        grouped.setdefault(_stratum(family), []).append(cid)
    for members in grouped.values():
        members.sort(key=lambda cid: (
            -prints.get(cid, 0),
            -float(open_markets[cid].get("volume_24h") or 0.0)))
    families = sorted(grouped, key=lambda f: (
        -max(prints.get(cid, 0) for cid in grouped[f]),
        -max(float(open_markets[cid].get("volume_24h") or 0.0)
             for cid in grouped[f])))
    out: list[str] = []
    depth = 0
    while len(out) < limit and any(len(grouped[f]) > depth for f in families):
        for family in families:
            if len(out) >= limit:
                break
            if len(grouped[family]) > depth:
                out.append(grouped[family][depth])
        depth += 1
    return out


def fetch_books(get: Callable, token_ids: list[str]) -> dict[str, Any]:
    def one(token_id: str) -> tuple[str, Any]:
        return token_id, get(CLOB_API + "/book", {"token_id": token_id})

    out: dict[str, Any] = {}
    with cf.ThreadPoolExecutor(16) as pool:
        for token_id, book in pool.map(one, token_ids):
            out[token_id] = book
    return out


def fetch_tapes(get: Callable, cids: list[str]) -> dict[str, list[dict]]:
    def one(cid: str) -> tuple[str, list[dict]]:
        rows = get(DATA_API + "/trades", {"market": cid, "limit": 500})
        return cid, (rows if isinstance(rows, list) else [])

    out: dict[str, list[dict]] = {}
    with cf.ThreadPoolExecutor(16) as pool:
        for cid, rows in pool.map(one, cids):
            out[cid] = rows
    return out


# --- one cycle ----------------------------------------------------------------


def build_rows(metas: dict[str, dict], books: dict[str, Any],
               tapes: dict[str, list[dict]], seen: dict[str, float],
               now: float, run_id: str, cycle: int,
               min_volume_usd: float, max_days: float,
               tape_window_min: float = DEFAULT_TAPE_WINDOW_MIN) -> list[dict]:
    """Turn one cycle's raw reads into the rows the store keeps.

    Pure, so the cycle's judgement can be tested without the venue.
    """
    rows = []
    for cid, meta in metas.items():
        tokens = meta.get("tokens") or []
        if not tokens:
            continue
        up_book = books.get(tokens[0])
        down_book = books.get(tokens[1]) if len(tokens) > 1 else None
        first_seen = cid not in seen
        rows_all = tapes.get(cid) or []
        prints_new = len(new_trades(rows_all, seen.get(cid, 0.0)))
        # A market with no two-sided book still gets a row. "The family was
        # open and had nothing to quote against" is a finding, and dropping the
        # row reports it as the family being absent instead -- measured
        # 2026-09-03, the venue lists tomorrow's five-minute crypto markets
        # ~20 hours early with a completely empty book, and a run that skipped
        # them would have concluded the family does not exist.
        measured = measure(up_book, down_book, rows_all, prints_new,
                           window_min=tape_window_min, now_ts=now)
        passed, reason = gate_verdict(meta, min_volume_usd, max_days)
        row = dict(
            ts=now, run_id=run_id, cycle=cycle, condition_id=cid,
            family=family_key(meta["question"], meta["slug"],
                              meta["series_title"], meta["event_title"]),
            slug=meta["slug"], question=meta["question"],
            series_title=meta["series_title"], event_title=meta["event_title"],
            market_group=meta["market_group"], category=meta["category"],
            tick=meta["tick"], volume_24h=meta["volume_24h"],
            days_to_resolve=meta["days_to_resolve"],
            gate_pass=int(passed), gate_reason=reason,
            tape_prints=len(rows_all), tape_prints_new=prints_new,
            book_ok=int(measured is not None),
            is_bootstrap=int(first_seen))
        row.update(measured or dict(_BLANK_MEASURE))
        rows.append(row)
    return rows


def run_cycle(get: Callable, conn: sqlite3.Connection, run_id: str,
              cycle: int, seen: dict[str, float], candidates: int,
              pages: int, open_pages: int, min_volume_usd: float,
              max_days: float,
              now: Callable[[], float] = time.time,
              tape_window_min: float = DEFAULT_TAPE_WINDOW_MIN) -> int:
    """Sweep the tape, read the open books, write what one pass measured."""
    started = time.monotonic()
    tape = sweep_tape(get, pages)
    counts: dict[str, int] = {}
    for print_row in tape:
        cid = print_row.get("conditionId")
        if cid:
            counts[cid] = counts.get(cid, 0) + 1
    open_markets = fetch_open_markets(get, open_pages)
    ranked = rank_candidates(open_markets, counts, candidates)
    metas = {cid: open_markets[cid] for cid in ranked}
    tokens = [t for m in metas.values() for t in (m.get("tokens") or [])[:2]]
    books = fetch_books(get, tokens)
    tapes = fetch_tapes(get, list(metas))
    stamp = now()
    rows = build_rows(metas, books, tapes, seen, stamp, run_id, cycle,
                      min_volume_usd, max_days, tape_window_min)
    write_samples(conn, rows)
    for row in rows:
        seen[row["condition_id"]] = stamp
    write_cycle(conn, dict(
        cycle=cycle, run_id=run_id, ts=stamp, tape_prints=len(tape),
        markets_traded=len(counts), candidates=len(metas), sampled=len(rows),
        seconds=round(time.monotonic() - started, 1)))
    log.info("cycle %d: %d prints, %d markets traded, %d sampled, %.0fs",
             cycle, len(tape), len(counts), len(rows),
             time.monotonic() - started)
    return len(rows)


def run(hours: float, interval_min: float, db_path: Path, run_id: str,
        candidates: int, pages: int, open_pages: int, per_second: float,
        min_volume_usd: float, max_days: float,
        tape_window_min: float = DEFAULT_TAPE_WINDOW_MIN,
        get: Optional[Callable] = None,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep) -> int:
    """Sample every `interval_min` until the time box expires.

    A failed cycle is logged and skipped, never raised. The expensive thing
    here is the day, and a run that dies on one unreachable hour has spent it.
    """
    conn = open_store(db_path)
    fetch = get or make_session(per_second)
    seen = last_seen(conn)
    cycle = next_cycle(conn)
    deadline = now() + hours * 3600.0
    sampled = 0
    try:
        while True:
            try:
                sampled += run_cycle(fetch, conn, run_id, cycle, seen,
                                     candidates, pages, open_pages,
                                     min_volume_usd, max_days, now,
                                     tape_window_min)
            except Exception as exc:                        # noqa: BLE001
                log.warning("cycle %d failed: %s", cycle, exc)
            cycle += 1
            if now() >= deadline:
                break
            sleep(interval_min * 60.0)
            if now() >= deadline:
                break
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        conn.close()
    log.info("done: %d cycles, %d samples -> %s", cycle - 1, sampled, db_path)
    return 0


def _parse_args(argv: Optional[list[str]] = None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--interval-min", type=float, default=5.0,
                        help="a five-minute crypto market is gone in five "
                             "minutes; a slower cadence cannot see one alive")
    parser.add_argument("--db", default="runtime/family_probe.db")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--tape-window-min", type=float,
                        default=DEFAULT_TAPE_WINDOW_MIN,
                        help="minutes of tape the drain rate is measured over "
                             "(the venue's whole history was the old basis, "
                             "and it predicted drains that never happened)")
    parser.add_argument("--candidates", type=int, default=150,
                        help="open markets per cycle, tape-active first")
    parser.add_argument("--pages", type=int, default=20,
                        help="500-print pages of global tape per cycle")
    parser.add_argument("--open-pages", type=int, default=5,
                        help="100-market pages of the open universe per cycle")
    parser.add_argument("--per-second", type=float, default=8.0,
                        help="request ceiling shared by every worker")
    parser.add_argument("--min-volume-usd", type=float, default=None,
                        help="volume gate to score against (default: config)")
    parser.add_argument("--max-days", type=float, default=None,
                        help="horizon gate to score against (default: config)")
    args = parser.parse_args(argv)
    try:
        _finite_window(args.tape_window_min)
    except ValueError as exc:
        parser.error(f"--tape-window-min: {exc}")
    return args


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    from core_brain import config as config_module

    config = config_module.load()
    min_volume = (args.min_volume_usd if args.min_volume_usd is not None
                  else float(config.filter_std_vol_min_24h_usd))
    max_days = (args.max_days if args.max_days is not None
                else float(config.filter_std_time_max_days_to_resolve))
    run_id = args.run_id or time.strftime("probe-%d-%m_%H-%M")
    log.info("run %s: %.1fh every %.0fmin, %d candidates/cycle, "
             "gates volume>=%.0f horizon<=%.0fd -> %s",
             run_id, args.hours, args.interval_min, args.candidates,
             min_volume, max_days, args.db)
    return run(args.hours, args.interval_min, Path(args.db), run_id,
               args.candidates, args.pages, args.open_pages, args.per_second,
               min_volume, max_days, args.tape_window_min)


if __name__ == "__main__":
    raise SystemExit(main())
