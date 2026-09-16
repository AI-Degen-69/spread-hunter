"""Record a forward price tape, because the venue stops serving one backwards.

`/prices-history` returns a dense tape for a market that is still live -- 1441
points at 1-minute fidelity over a day -- and stops returning one once the
market resolves. Measured 2026-09-07: of 925 resolved markets over $15k of
volume, only 92 still served more than 30 points. That 90% loss is why the
question this module exists to answer cannot be settled from history at any
effort. The sample is capped by the venue, not by patience.

The question is whether a price move continues or comes back. Scored on real
resolutions the sample is one observation per market, which reached n=51 and
t=1.07 -- noise. The same test needs a few hundred resolved markets whose tape
survives, and the only way to have those is to record the tape while the
markets are open and stamp the resolution on afterwards.

Nothing here signs, quotes, or spends. It reads two public endpoints and
appends to its own SQLite file, which is never `data/orders.db`.

Recovery, not polling, is the design. Each pass backfills the last few hours at
1-minute fidelity and the store dedups on `(token_id, ts)`, so a pass that runs
late, runs twice, or restarts after a day of downtime loses no minutes and
stores no duplicates. A pass that cannot reach one market records the rest:
losing a market is a gap in one tape, and aborting is a gap in all of them.
"""
from __future__ import annotations

import argparse
import bisect
import json
import logging
import math
import os
import sqlite3
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import requests

log = logging.getLogger(__name__)

GAMMA_HOST = os.environ.get("GAMMA_HOST", "https://gamma-api.polymarket.com")
CLOB_HOST = os.environ.get("CLOB_HOST", "https://clob.polymarket.com")

#: The venue serves at most this many market rows in one page, whatever larger
#: `limit` is asked for. Measured 2026-09-07: `--limit 250` returned 100
#: markets. Reading one reply as the whole universe silently truncates it, so
#: every caller pages instead.
DISCOVERY_PAGE = 100

#: A stop on the paging loop, so a venue that keeps serving rows cannot spin it.
MAX_DISCOVERY_PAGES = 40

#: Minute fidelity is the point. A coarser tape smooths away the very moves the
#: drift test measures, and the venue silently ignores `fidelity` unless the
#: request carries an explicit `startTs`/`endTs` window.
TAPE_FIDELITY_MINUTES = 1

#: The venue honours `fidelity` only inside a bounded window, and serves a day
#: of minutes per request, so a deeper reach is walked back a day at a time.
CHUNK_SECONDS = 86_400

#: How far back each pass reaches. Comfortably longer than the poll interval so
#: a late or skipped pass still closes its own gap.
DEFAULT_BACKFILL_HOURS = 6.0

#: How far back the FIRST pass reaches. The tape already exists on the venue for
#: markets that are still open -- 20 live markets held 438 market-days of it on
#: 2026-09-07 -- so the opening pass claims it instead of waiting to record it.
DEFAULT_SEED_DAYS = 30.0

#: Default cadence. The store dedups, so polling more often only costs requests.
DEFAULT_POLL_SECONDS = 1800.0

DEFAULT_MIN_VOLUME = 15_000.0
DEFAULT_LIMIT = 120
HTTP_TIMEOUT = 25.0

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TAPE_PATH = PROJECT_ROOT / "data" / "price_tape.db"

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS markets (
        token_id     TEXT PRIMARY KEY,
        condition_id TEXT NOT NULL,
        question     TEXT NOT NULL,
        slug         TEXT NOT NULL,
        volume_24h   REAL NOT NULL,
        first_seen   INTEGER NOT NULL,
        up_wins      INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ticks (
        token_id TEXT NOT NULL,
        ts       INTEGER NOT NULL,
        price    REAL NOT NULL,
        PRIMARY KEY (token_id, ts)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ticks_by_token ON ticks (token_id, ts)",
    """
    CREATE TABLE IF NOT EXISTS findings (
        label       TEXT PRIMARY KEY,
        trigger     REAL NOT NULL,
        lookback_m  INTEGER NOT NULL,
        horizon_m   INTEGER NOT NULL,
        n           INTEGER NOT NULL,
        mean        REAL NOT NULL,
        t_stat      REAL NOT NULL,
        computed_at INTEGER NOT NULL
    )
    """,
)


class TapeStoreError(RuntimeError):
    """The tape store could not be opened or written."""


@dataclass(frozen=True)
class TapeMarket:
    """A market whose tape is being recorded."""

    token_id: str
    condition_id: str
    question: str
    slug: str
    volume_24h: float
    up_wins: Optional[bool] = None


@dataclass(frozen=True)
class TapeSummary:
    """How much tape has been collected so far."""

    markets: int
    resolved: int
    ticks: int
    first_ts: Optional[int]
    last_ts: Optional[int]


@dataclass(frozen=True)
class PollResult:
    """What one pass managed to record."""

    markets: int
    ticks: int
    failures: int


@dataclass(frozen=True)
class DriftCell:
    """One question asked of the tape.

    "After price moved at least `trigger` over the last `lookback` minutes, what
    did it do over the next `horizon` minutes?"
    """

    trigger: float
    lookback: int
    horizon: int

    @property
    def label(self) -> str:
        return (f"{self.trigger * 100:.0f}c/{self.lookback}m/{self.horizon}m")


@dataclass(frozen=True)
class CellStat:
    """A cell's answer: how many samples, the mean, and how sure."""

    n: int
    mean: float
    t: float


@dataclass(frozen=True)
class Finding:
    """One stored cell answer, as the dashboard reads it."""

    label: str
    trigger: float
    lookback_min: int
    horizon_min: int
    n: int
    mean: float
    t: float
    computed_at: int


#: The grid the analysis answers. Three trigger sizes against three horizons,
#: seen from two lookbacks -- wide enough that a real effect shows up in
#: neighbouring cells rather than in one, which is what tells a signal from a
#: multiple-testing artefact.
DEFAULT_CELLS: tuple[DriftCell, ...] = tuple(
    DriftCell(trigger, lookback, horizon)
    for trigger in (0.03, 0.05, 0.10)
    for lookback in (60, 240)
    for horizon in (60, 240, 1440)
)

#: Once price is this close to an end the market is decided, and the remaining
#: move is the resolution rather than anything a quote could have traded.
DECIDED_LO, DECIDED_HI = 0.03, 0.97

#: p<0.05 after Bonferroni across the grid, fixed before any of it was run so
#: the bar cannot be moved to fit an answer.
SIGNIFICANCE_T = 3.0

#: Below this a cell reports no t-statistic: with a handful of observations
#: any t says more about the sample size than about the tape.
MIN_CELL_SAMPLES = 5

#: A market with less tape than this cannot host a full horizon, so asking it
#: anything only adds noise.
MIN_TAPE_TICKS = 200

#: How far from a window's endpoint a tick may sit and still answer for it.
#: A cell names minutes, so it must measure minutes; but the tape's own
#: spacing wanders (57-62s between neighbours is normal) and the venue skips
#: minutes outright, so an exact match would discard nearly every sample.
#: 90 seconds admits the wander and refuses a real gap.
ENDPOINT_TOLERANCE_SEC = 90


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class TapeStore:
    """Append-only SQLite tape, deduped on (token_id, ts)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as conn:
                for statement in _SCHEMA:
                    conn.execute(statement)
        except sqlite3.Error as exc:
            raise TapeStoreError(f"Cannot open the price tape at {self.path}: {exc}") from exc

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def record_market(self, market: TapeMarket) -> None:
        """Register a market, keeping the row already stored if there is one.

        A pass re-discovers the same markets every time; re-registering must not
        wipe a resolution already stamped on, so this inserts and does nothing
        on conflict rather than replacing.
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO markets "
                    "(token_id, condition_id, question, slug, volume_24h, first_seen, up_wins) "
                    "VALUES (?, ?, ?, ?, ?, ?, NULL) "
                    "ON CONFLICT(token_id) DO NOTHING",
                    (market.token_id, market.condition_id, market.question,
                     market.slug, market.volume_24h, int(time.time())),
                )
        except sqlite3.Error as exc:
            raise TapeStoreError(f"Cannot record market {market.token_id}: {exc}") from exc

    def append_ticks(self, token_id: str, ticks: Iterable[tuple[int, float]]) -> int:
        """Store ticks, skipping every minute already held. Returns the count added."""
        rows = [(token_id, ts, price) for ts, price in ticks]
        if not rows:
            return 0
        try:
            with self._connect() as conn:
                before = conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
                conn.executemany(
                    "INSERT INTO ticks (token_id, ts, price) VALUES (?, ?, ?) "
                    "ON CONFLICT(token_id, ts) DO NOTHING",
                    rows,
                )
                after = conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
        except sqlite3.Error as exc:
            raise TapeStoreError(f"Cannot append ticks for {token_id}: {exc}") from exc
        return after - before

    def mark_resolved(self, token_id: str, up_wins: bool) -> None:
        try:
            with self._connect() as conn:
                conn.execute("UPDATE markets SET up_wins = ? WHERE token_id = ?",
                             (1 if up_wins else 0, token_id))
        except sqlite3.Error as exc:
            raise TapeStoreError(f"Cannot resolve {token_id}: {exc}") from exc

    def get_market(self, token_id: str) -> Optional[TapeMarket]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT token_id, condition_id, question, slug, volume_24h, up_wins "
                    "FROM markets WHERE token_id = ?", (token_id,)).fetchone()
        except sqlite3.Error as exc:
            raise TapeStoreError(f"Cannot read market {token_id}: {exc}") from exc
        if row is None:
            return None
        return TapeMarket(
            token_id=row["token_id"], condition_id=row["condition_id"],
            question=row["question"], slug=row["slug"],
            volume_24h=row["volume_24h"],
            up_wins=None if row["up_wins"] is None else bool(row["up_wins"]),
        )

    def tracked_tokens(self) -> list[str]:
        """Tokens still worth polling: everything without a resolution."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT token_id FROM markets WHERE up_wins IS NULL "
                    "ORDER BY first_seen, token_id").fetchall()
        except sqlite3.Error as exc:
            raise TapeStoreError(f"Cannot list tracked markets: {exc}") from exc
        return [row["token_id"] for row in rows]

    def tape_for(self, token_id: str) -> list[tuple[int, float]]:
        """One market's ticks, oldest first."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT ts, price FROM ticks WHERE token_id = ? ORDER BY ts",
                    (token_id,)).fetchall()
        except sqlite3.Error as exc:
            raise TapeStoreError(f"Cannot read the tape for {token_id}: {exc}") from exc
        return [(row["ts"], row["price"]) for row in rows]

    def tick_tokens(self) -> list[str]:
        """Every token that has at least one tick."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT token_id FROM ticks ORDER BY token_id").fetchall()
        except sqlite3.Error as exc:
            raise TapeStoreError(f"Cannot list recorded tokens: {exc}") from exc
        return [row["token_id"] for row in rows]

    def replace_findings(self, findings: Iterable[Finding]) -> int:
        """Store the analysis as a snapshot: the previous one is replaced.

        A findings log would need a reader to work out which run it is looking
        at, and there is only ever one current answer.
        """
        rows = [(f.label, f.trigger, f.lookback_min, f.horizon_min,
                 f.n, f.mean, f.t, f.computed_at) for f in findings]
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM findings")
                conn.executemany(
                    "INSERT INTO findings (label, trigger, lookback_m, horizon_m, "
                    "n, mean, t_stat, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    rows)
        except sqlite3.Error as exc:
            raise TapeStoreError(f"Cannot store findings: {exc}") from exc
        return len(rows)

    def findings(self) -> list[Finding]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT label, trigger, lookback_m, horizon_m, n, mean, "
                    "t_stat, computed_at FROM findings "
                    "ORDER BY trigger, lookback_m, horizon_m").fetchall()
        except sqlite3.Error as exc:
            raise TapeStoreError(f"Cannot read findings: {exc}") from exc
        return [Finding(label=r["label"], trigger=r["trigger"],
                        lookback_min=r["lookback_m"], horizon_min=r["horizon_m"],
                        n=r["n"], mean=r["mean"], t=r["t_stat"],
                        computed_at=r["computed_at"]) for r in rows]

    def summary(self) -> TapeSummary:
        try:
            with self._connect() as conn:
                markets = conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
                resolved = conn.execute(
                    "SELECT COUNT(*) FROM markets WHERE up_wins IS NOT NULL").fetchone()[0]
                ticks, first_ts, last_ts = conn.execute(
                    "SELECT COUNT(*), MIN(ts), MAX(ts) FROM ticks").fetchone()
        except sqlite3.Error as exc:
            raise TapeStoreError(f"Cannot summarise the tape: {exc}") from exc
        return TapeSummary(markets=markets, resolved=resolved, ticks=ticks,
                           first_ts=first_ts, last_ts=last_ts)


def parse_history(payload: Any) -> list[tuple[int, float]]:
    """The venue's history payload as ticks, dropping every point it mangled.

    A single unusable point is a gap of one minute; refusing the whole payload
    is a gap of hours, so the bad points are dropped and the rest is kept.
    """
    if not isinstance(payload, dict):
        return []
    history = payload.get("history")
    if not isinstance(history, list):
        return []
    ticks: list[tuple[int, float]] = []
    for point in history:
        if not isinstance(point, dict):
            continue
        ts = _as_int(point.get("t"))
        price = _as_float(point.get("p"))
        # A binary share is worth $0.00 to $1.00. `float()` accepts -1, 2,
        # "NaN" and "inf" happily, and one of those in the tape corrupts every
        # statistic computed from it later; the range check rejects the
        # non-finite values too, because no comparison with NaN is ever true.
        if ts is None or price is None or not 0.0 <= price <= 1.0:
            continue
        ticks.append((ts, price))
    return ticks


def parse_market_row(row: Any) -> Optional[TapeMarket]:
    """One gamma market row as a TapeMarket, or None when it is unusable."""
    if not isinstance(row, dict):
        return None
    condition_id = str(row.get("conditionId") or "")
    if not condition_id:
        return None
    raw_tokens = row.get("clobTokenIds")
    if isinstance(raw_tokens, str):
        try:
            tokens: Sequence[Any] = json.loads(raw_tokens)
        except (ValueError, TypeError):
            return None
    elif isinstance(raw_tokens, list):
        tokens = raw_tokens
    else:
        return None
    if not isinstance(tokens, list) or len(tokens) != 2:
        return None
    return TapeMarket(
        token_id=str(tokens[0]),
        condition_id=condition_id,
        question=str(row.get("question") or ""),
        slug=str(row.get("slug") or ""),
        volume_24h=_as_float(row.get("volume24hr")) or 0.0,
    )


def _new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    for scheme in ("https://", "http://"):
        session.mount(scheme, requests.adapters.HTTPAdapter(
            pool_connections=8, pool_maxsize=8, max_retries=0))
    return session


def _iter_market_pages(session: Any, gamma_host: str, params: dict[str, Any]):
    """Yield gamma market rows a page at a time, advancing `offset`.

    The endpoint caps a page at DISCOVERY_PAGE rows however large a `limit` is
    asked for, so a single request is a truncated universe, not the whole one.
    Paging stops on the first empty or short page.
    """
    offset = 0
    for _page in range(MAX_DISCOVERY_PAGES):
        response = session.get(
            f"{gamma_host}/markets",
            params={**params, "limit": DISCOVERY_PAGE, "offset": offset},
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            return
        yield rows
        if len(rows) < DISCOVERY_PAGE:
            return
        offset += len(rows)


def discover_markets(
    *,
    session: Any,
    min_volume: float = DEFAULT_MIN_VOLUME,
    limit: int = DEFAULT_LIMIT,
    gamma_host: str = GAMMA_HOST,
) -> list[TapeMarket]:
    """Live markets worth recording, most traded first."""
    markets: list[TapeMarket] = []
    for rows in _iter_market_pages(
        session, gamma_host,
        {"closed": "false", "active": "true",
         "order": "volume24hr", "ascending": "false"},
    ):
        for row in rows:
            if len(markets) >= limit:
                return markets
            market = parse_market_row(row)
            if market is None or market.volume_24h < min_volume:
                continue
            markets.append(market)
        if len(markets) >= limit:
            break
    return markets


def backfill(
    store: TapeStore,
    token_id: str,
    *,
    session: Any,
    hours: float = DEFAULT_BACKFILL_HOURS,
    clob_host: str = CLOB_HOST,
    now: Optional[int] = None,
) -> int:
    """Pull the last `hours` of minute tape for one token. Returns ticks added.

    The window is explicit because `interval=max` silently ignores `fidelity`
    and hands back a coarse tape; only `startTs`/`endTs` honours minute detail.
    That honouring is per bounded window, so reaching back further than a day
    means walking the request back a day at a time rather than asking once.

    It is worth reaching. Measured 2026-09-07, 20 live markets held 438
    market-days of minute tape already available, and most long-dated markets
    served the full 30 days. Pulling only the last few hours is what made the
    drift question look like it needed weeks of forward recording.

    Each window is stored as it arrives, so a window the venue refuses raises
    after the earlier ones are already safe on disk.
    """
    end_ts = int(time.time()) if now is None else int(now)
    start_ts = end_ts - int(hours * 3600)
    written = 0
    cursor = end_ts
    while cursor > start_ts:
        window_start = max(start_ts, cursor - CHUNK_SECONDS)
        response = session.get(
            f"{clob_host}/prices-history",
            params={"market": token_id, "startTs": window_start, "endTs": cursor,
                    "fidelity": TAPE_FIDELITY_MINUTES},
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        written += store.append_ticks(token_id, parse_history(response.json()))
        cursor = window_start
    return written


def poll_once(
    store: TapeStore,
    *,
    session: Any = None,
    min_volume: float = DEFAULT_MIN_VOLUME,
    limit: int = DEFAULT_LIMIT,
    hours: float = DEFAULT_BACKFILL_HOURS,
    gamma_host: str = GAMMA_HOST,
    clob_host: str = CLOB_HOST,
) -> PollResult:
    """One recording pass: discover, then backfill each market's tape.

    A market the venue refuses is counted and skipped. Aborting the pass would
    turn one missing tape into a gap across every market being recorded.
    """
    session = session or _new_session()
    markets = discover_markets(session=session, min_volume=min_volume,
                               limit=limit, gamma_host=gamma_host)
    ticks = failures = 0
    for market in markets:
        store.record_market(market)
        try:
            ticks += backfill(store, market.token_id, session=session,
                              hours=hours, clob_host=clob_host)
        except (requests.RequestException, ValueError) as exc:
            failures += 1
            log.warning("tape backfill failed for %s: %s", market.token_id, exc)
    return PollResult(markets=len(markets), ticks=ticks, failures=failures)


def refresh_resolutions(
    store: TapeStore,
    *,
    session: Any = None,
    gamma_host: str = GAMMA_HOST,
) -> int:
    """Stamp resolutions onto tracked markets. Returns how many were stamped.

    Only a clean binary outcome is recorded. An ambiguous or partial resolution
    cannot score a position, so it is left unresolved rather than guessed at.
    """
    session = session or _new_session()
    tracked = set(store.tracked_tokens())
    if not tracked:
        return 0
    stamped = 0
    for rows in _iter_market_pages(
        session, gamma_host,
        {"closed": "true", "order": "endDate", "ascending": "false"},
    ):
        for row in rows:
            market = parse_market_row(row)
            if market is None or market.token_id not in tracked:
                continue
            if not row.get("closed"):
                continue
            raw_prices = row.get("outcomePrices")
            if isinstance(raw_prices, str):
                try:
                    prices = json.loads(raw_prices)
                except (ValueError, TypeError):
                    continue
            elif isinstance(raw_prices, list):
                prices = raw_prices
            else:
                continue
            if not isinstance(prices, list) or not prices:
                continue
            up_final = _as_float(prices[0])
            if up_final not in (0.0, 1.0):
                continue
            store.mark_resolved(market.token_id, up_final == 1.0)
            tracked.discard(market.token_id)
            stamped += 1
        if not tracked:
            break       # nothing left to look for; stop paging the venue
    return stamped


def _price_at(
    tape: Sequence[tuple[int, float]],
    times: Sequence[int],
    target: int,
) -> Optional[float]:
    """The price at `target`, or None when the tape has no tick near it.

    The venue skips minutes and `parse_history` drops points it mangled, so a
    tape is not one row per minute and its timestamps are not evenly spaced --
    measured gaps of 57 to 62 seconds are normal. Requiring an exact match
    would discard almost every sample, so the nearest tick within
    `ENDPOINT_TOLERANCE_SEC` answers and anything further away is a hole.
    """
    idx = bisect.bisect_left(times, target)
    best: Optional[tuple[int, float]] = None
    for j in (idx - 1, idx):
        if 0 <= j < len(times):
            distance = abs(times[j] - target)
            if distance <= ENDPOINT_TOLERANCE_SEC and (best is None or distance < best[0]):
                best = (distance, tape[j][1])
    return None if best is None else best[1]


def signed_forward_returns(
    tape: Sequence[tuple[int, float]],
    cell: DriftCell,
) -> list[float]:
    """What price did next, signed by the direction it had just moved.

    A positive value means the move continued, a negative one that it came back.
    Samples are taken NON-OVERLAPPING -- each consumes its whole horizon --
    because overlapping windows share most of their price path and would inflate
    the t-statistic computed from them without adding information.

    A sample never starts once price has left [DECIDED_LO, DECIDED_HI]: past
    that the market is decided and the rest of the path is resolution, not a
    move anything could have traded against.
    """
    out: list[float] = []
    times = [ts for ts, _ in tape]
    lookback_sec = cell.lookback * 60
    horizon_sec = cell.horizon * 60
    next_allowed_ts = -1
    for ts, price_now in tape:
        if ts < next_allowed_ts:
            continue
        if price_now <= DECIDED_LO or price_now >= DECIDED_HI:
            next_allowed_ts = ts + horizon_sec
            continue
        past = _price_at(tape, times, ts - lookback_sec)
        if past is None:
            continue
        move = price_now - past
        if abs(move) < cell.trigger:
            continue
        forward = _price_at(tape, times, ts + horizon_sec)
        if forward is None:
            continue
        out.append(math.copysign(1.0, move) * (forward - price_now))
        next_allowed_ts = ts + horizon_sec
    return out


def summarise(values: Sequence[float]) -> CellStat:
    """Sample size, mean, and the t-statistic against a mean of zero.

    The spread is the SAMPLE standard deviation. A sample too thin to have one
    reports t=0 rather than a number: with two observations any t is an artefact
    of having two observations.
    """
    n = len(values)
    if n == 0:
        return CellStat(n=0, mean=0.0, t=0.0)
    mean = statistics.fmean(values)
    if n < MIN_CELL_SAMPLES:
        return CellStat(n=n, mean=mean, t=0.0)
    # Sample sd, not population: these are a sample of the tape's moments, and
    # dividing by n instead of n-1 understates the standard error and overstates
    # t -- which on a grid read against a fixed bar is a manufactured signal.
    sd = statistics.stdev(values)
    if sd == 0.0:
        return CellStat(n=n, mean=mean, t=0.0)
    return CellStat(n=n, mean=mean, t=mean / (sd / math.sqrt(n)))


def analyse(
    store: TapeStore,
    *,
    cells: Sequence[DriftCell] = DEFAULT_CELLS,
    min_ticks: int = MIN_TAPE_TICKS,
) -> int:
    """Answer every cell against the recorded tape and store the answers.

    Reads each market's tape once and asks every cell of it, because loading the
    store is the expensive part -- 2.8M ticks took 33 seconds on 2026-09-07,
    which is why this is an explicit pass and not something a page does.
    """
    pooled: dict[str, list[float]] = {cell.label: [] for cell in cells}
    for token_id in store.tick_tokens():
        tape = store.tape_for(token_id)
        if len(tape) < min_ticks:
            continue
        for cell in cells:
            pooled[cell.label].extend(signed_forward_returns(tape, cell))
    computed_at = int(time.time())
    findings = []
    for cell in cells:
        stat = summarise(pooled[cell.label])
        findings.append(Finding(
            label=cell.label, trigger=cell.trigger, lookback_min=cell.lookback,
            horizon_min=cell.horizon, n=stat.n, mean=stat.mean, t=stat.t,
            computed_at=computed_at))
    return store.replace_findings(findings)


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_TAPE_PATH),
                        help="where the tape is written")
    parser.add_argument("--min-volume", type=float, default=DEFAULT_MIN_VOLUME,
                        help="24h volume a market must clear to be recorded")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help="stop after this many markets per pass")
    parser.add_argument("--hours", type=float, default=DEFAULT_BACKFILL_HOURS,
                        help="how far back each pass reaches")
    parser.add_argument("--seed-days", type=float, default=0.0,
                        help=f"reach back this many days on the FIRST pass and "
                             f"claim the tape the venue already holds "
                             f"(try {DEFAULT_SEED_DAYS:.0f})")
    parser.add_argument("--interval", type=float, default=DEFAULT_POLL_SECONDS,
                        help="seconds between passes")
    parser.add_argument("--once", action="store_true",
                        help="record a single pass and exit")
    parser.add_argument("--status", action="store_true",
                        help="print what has been collected and exit")
    parser.add_argument("--analyse", action="store_true",
                        help="answer the drift grid against the recorded tape, "
                             "store the answers, and exit")
    parser.add_argument("--resolve-only", action="store_true",
                        help="stamp resolutions onto the recorded tape, "
                             "record no ticks, and exit")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = TapeStore(args.db)

    if args.status:
        s = store.summary()
        span = "" if s.first_ts is None else \
            f"  span {(s.last_ts - s.first_ts) / 86400.0:.1f}d"
        print(f"markets {s.markets} ({s.resolved} resolved)  ticks {s.ticks:,}{span}")
        return 0

    if args.analyse:
        written = analyse(store)
        print(f"{'cell':<16}{'n':>8}{'mean':>10}{'t':>8}   verdict")
        print("-" * 54)
        for f in store.findings():
            verdict = ("continues" if f.t >= SIGNIFICANCE_T else
                       "comes back" if f.t <= -SIGNIFICANCE_T else "no signal")
            print(f"{f.label:<16}{f.n:>8}{f.mean * 100:>9.3f}c{f.t:>8.2f}   {verdict}")
        print(f"\n{written} cells stored. Bar for a signal: |t| >= {SIGNIFICANCE_T}")
        return 0

    if args.resolve_only:
        session = _new_session()
        stamped = refresh_resolutions(store, session=session)
        pending = len(store.tracked_tokens())
        print(f"resolutions stamped {stamped}  pending {pending}")
        return 0

    session = _new_session()
    hours = args.seed_days * 24.0 if args.seed_days else args.hours
    while True:
        result = poll_once(store, session=session, min_volume=args.min_volume,
                           limit=args.limit, hours=hours)
        stamped = refresh_resolutions(store, session=session)
        s = store.summary()
        log.info("pass: %d markets, +%d ticks, %d failures, %d newly resolved "
                 "| total %d markets (%d resolved), %d ticks",
                 result.markets, result.ticks, result.failures, stamped,
                 s.markets, s.resolved, s.ticks)
        if args.once:
            return 0
        hours = args.hours          # the deep seed is for the first pass only
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(_main())
