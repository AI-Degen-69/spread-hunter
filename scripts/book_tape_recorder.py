"""Record what the book offers and what the tape actually takes, per price level.

Read-only, and deliberately outside the money path: it opens no client that can
sign, writes its own store, and never touches `data/orders.db` or a shadow
store. It exists to answer two questions the `reward_offset` sweep could not,
because both are properties of the venue rather than of our quoting
(issue #145).

  1. **Reachability.** How much tape volume prints at each distance from mid.
     A sweep measures three offsets and spends an hour doing it; a tape
     histogram measures every offset at once, from the same minutes, with no
     arm to confound with the hour. It is also the direct measurement of
     `spread_capture_frac`, the 0.25 assumption every `est_income` figure in
     the market filter rests on.

  2. **The touch pair.** `best_bid(UP) + best_bid(DOWN)` is the cheapest pair
     two resting bids can assemble. On the current universe -- 1c tick, 1c
     spread, `mid_UP + mid_DOWN` measured at 1.0000 -- that is $0.99, exactly
     the `max_pair_cost` the risk gate refuses with `>=`. Whether it is ALWAYS
     $0.99 or merely usually decides whether the strategy has an edge to take.

Distances are recorded in TICKS, not cents. The reward window, the offset knobs
and the config defaults are all written in cents, but the book is quantised in
ticks and a level is either reachable or it is not; a cent bucket on a 1c-tick
book is a tick bucket wearing the wrong label, and on a 0.001 book it silently
merges ten distinct levels.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("book_tape")

DEFAULT_TICK = 0.01
REFUSED_STORES = ("orders.db",)

SCHEMA = """
CREATE TABLE IF NOT EXISTS book_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    run_id TEXT,
    condition_id TEXT NOT NULL,
    market_slug TEXT,
    tick REAL,
    best_bid_up REAL, best_ask_up REAL,
    best_bid_down REAL, best_ask_down REAL,
    mid_up REAL, mid_down REAL,
    mid_sum REAL,
    touch_pair_cost REAL,
    touch_size_up REAL, touch_size_down REAL
);
CREATE TABLE IF NOT EXISTS tape_buckets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    run_id TEXT,
    condition_id TEXT NOT NULL,
    market_slug TEXT,
    token_id TEXT NOT NULL,
    side TEXT,
    price REAL NOT NULL,
    ticks_from_mid INTEGER,
    volume REAL NOT NULL,
    is_bootstrap INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_tape_ticks ON tape_buckets(ticks_from_mid);
CREATE INDEX IF NOT EXISTS ix_book_cid ON book_samples(condition_id);
"""


# --- pure core ----------------------------------------------------------------


def mid_of(book: dict) -> Optional[float]:
    """Midpoint, or None when either side of the book is missing."""
    bb, ba = (book or {}).get("best_bid"), (book or {}).get("best_ask")
    if bb is None or ba is None:
        return None
    return (float(bb) + float(ba)) / 2.0


def ticks_from_mid(mid: Optional[float], price: float, tick: float) -> Optional[int]:
    """How many ticks BELOW mid a print landed. Negative means above mid.

    Rounded rather than floored: a price is a lattice point and the only
    question is which one, so the nearest integer is the answer. Flooring
    biases every above-mid print one tick further out.
    """
    if mid is None or not tick or tick <= 0:
        return None
    return int(round((float(mid) - float(price)) / float(tick)))


def touch_pair_cost(up_book: dict, down_book: dict) -> Optional[float]:
    """`best_bid(UP) + best_bid(DOWN)`: the cheapest pair two resting bids make.

    Not the pair we would post -- the quoter rests under mid, not at the touch
    -- but the FLOOR of what any maker-only pair can cost. If this sits at or
    above `max_pair_cost` there is no maker-only pair to assemble at any offset.
    """
    bu, bd = (up_book or {}).get("best_bid"), (down_book or {}).get("best_bid")
    if bu is None or bd is None:
        return None
    return round(float(bu) + float(bd), 4)


def bucket_trades(traded: dict, mids: dict, tick: float) -> list[dict]:
    """Flatten `recent_trades` output into rows carrying distance from mid.

    `traded` is token -> price -> volume; `mids` is token -> mid. A token with
    no readable mid still yields rows, with `ticks_from_mid` None: the volume
    is real and dropping it would under-count the tape on exactly the markets
    whose books were briefly one-sided.
    """
    rows: list[dict] = []
    for token_id, by_price in (traded or {}).items():
        mid = mids.get(str(token_id))
        for price, volume in (by_price or {}).items():
            vol = float(volume)
            if vol <= 0:
                continue
            rows.append({
                "token_id": str(token_id),
                "price": round(float(price), 4),
                "ticks_from_mid": ticks_from_mid(mid, float(price), tick),
                "volume": vol,
            })
    return rows


def reachable_volume(rows: list[dict], ticks: int) -> float:
    """Tape volume that could reach a resting bid `ticks` under mid.

    A TAIL sum, not a forward one, and the direction matters. Our bid sits at
    a price; a market sell walks the book from the top down and prints against
    our level only after exhausting every better-priced bid. A print NEARER
    mid than our order therefore filled somebody else, and a print FURTHER
    from mid means the book traded through our level and we were filled on the
    way down. What can reach a bid at `ticks` is everything at
    `ticks_from_mid >= ticks`.

    Summing from mid down to our level instead -- volume at `0 <= k <= ticks`
    -- counts exactly the prints that went to better-priced bids, and reverses
    the curve: it makes a deeper order look MORE reachable when it is less.
    """
    return sum(r["volume"] for r in rows
               if r["ticks_from_mid"] is not None
               and r["ticks_from_mid"] >= ticks)


def reachable_fraction(rows: list[dict], ticks: int) -> float:
    """`reachable_volume` as a share of ALL tape, above-mid prints included.

    The denominator is every print, because that is the denominator
    `spread_capture_frac` uses when it asserts we capture 0.25 of a market's
    volume. A resting bid at `ticks` under mid can only ever be reached by
    this fraction, and only then if it also clears the queue ahead of it -- so
    the figure is an upper bound on capture, never an estimate of it.
    """
    total = sum(r["volume"] for r in rows)
    if total <= 0:
        return 0.0
    return reachable_volume(rows, ticks) / total


def split_bootstrap(rows: list[dict], seen_before: bool) -> bool:
    """Whether this pass's rows carry a meaningful distance from mid.

    False on a market's first pass: `recent_trades` starts with an empty
    `seen` set and returns the venue's whole recent window at once, so those
    prices are stamped against a mid that was not live when they traded.
    """
    return not seen_before


# --- store --------------------------------------------------------------------


def refuse_production_store(db_path: Path) -> None:
    """Refuse to open the production registry, by name, before connecting.

    Same rule and same reason as `core_brain.shadow_run`: this process has no
    business in `data/orders.db`, and a typo in `--db` must fail loudly rather
    than append recorder rows to the money registry.
    """
    if db_path.name in REFUSED_STORES:
        raise SystemExit(f"refusing to write {db_path}: that is the production registry")


def open_store(db_path: Path) -> sqlite3.Connection:
    refuse_production_store(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def write_sample(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        "INSERT INTO book_samples (ts, run_id, condition_id, market_slug, tick,"
        " best_bid_up, best_ask_up, best_bid_down, best_ask_down,"
        " mid_up, mid_down, mid_sum, touch_pair_cost, touch_size_up, touch_size_down)"
        " VALUES (:ts,:run_id,:condition_id,:market_slug,:tick,"
        ":best_bid_up,:best_ask_up,:best_bid_down,:best_ask_down,"
        ":mid_up,:mid_down,:mid_sum,:touch_pair_cost,:touch_size_up,:touch_size_down)",
        row)


def write_tape(conn: sqlite3.Connection, ts: float, run_id: str, cid: str,
               slug: str, sides: dict, rows: list[dict],
               is_bootstrap: bool = False) -> None:
    """Persist one pass's tape rows, flagging the first pass for what it is.

    `recent_trades` de-duplicates against a `seen` set that starts empty, so a
    market's FIRST pass returns the venue's whole recent window -- hundreds of
    trades printed over hours -- and every one of them gets stamped against the
    mid we happen to read now. Those rows are real trades at real prices, but
    their distance from mid is meaningless: the first live pass of this
    recorder produced buckets spanning 27 ticks either side of mid on a market
    whose spread was one tick.

    Flagged rather than dropped. The bootstrap window is a usable picture of
    where a market has traded over its life, which is a different and also
    interesting question; what it must never do is contaminate the
    reachability curve, which needs the mid that was live when the print
    happened.
    """
    conn.executemany(
        "INSERT INTO tape_buckets (ts, run_id, condition_id, market_slug,"
        " token_id, side, price, ticks_from_mid, volume, is_bootstrap)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(ts, run_id, cid, slug, r["token_id"], sides.get(r["token_id"]),
          r["price"], r["ticks_from_mid"], r["volume"], int(is_bootstrap))
         for r in rows])


# --- loop ---------------------------------------------------------------------


def load_universe(path: Path) -> list[dict]:
    """The market list the fleet is quoting, read from the filter's own output.

    Read fresh on every pass rather than once at start: `scripts.filter_loop`
    rewrites this file on its own interval, and a recorder pinned to the list
    it saw at startup would silently describe a different universe than the
    rehearsal beside it -- the exact drift that left two blocks of the #138
    sweep unattributable.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("cannot read %s: %s", path, e)
        return []
    return [m for m in (data or []) if isinstance(m, dict) and m.get("cid")]


def sample_market(market: dict, fetch_market: Callable[[str], Any],
                  fetch_book: Callable[[str, str], dict],
                  fetch_tape: Callable[[str, set], dict],
                  seen: set, clob_host: str):
    """One market's book sample and tape rows, or None when it is unreadable."""
    cid = str(market["cid"])
    resolved = fetch_market(cid)
    up = getattr(resolved, "up_token", None)
    dn = getattr(resolved, "down_token", None)
    if not up or not dn:
        return None
    up_book = fetch_book(clob_host, up)
    dn_book = fetch_book(clob_host, dn)
    tick = float(market.get("tick") or DEFAULT_TICK)
    mid_up, mid_dn = mid_of(up_book), mid_of(dn_book)
    sample = {
        "ts": time.time(),
        "run_id": None,
        "condition_id": cid,
        "market_slug": market.get("slug") or "",
        "tick": tick,
        "best_bid_up": up_book.get("best_bid"), "best_ask_up": up_book.get("best_ask"),
        "best_bid_down": dn_book.get("best_bid"), "best_ask_down": dn_book.get("best_ask"),
        "mid_up": mid_up, "mid_down": mid_dn,
        "mid_sum": (round(mid_up + mid_dn, 4)
                    if mid_up is not None and mid_dn is not None else None),
        "touch_pair_cost": touch_pair_cost(up_book, dn_book),
        "touch_size_up": (up_book.get("bids") or {}).get(up_book.get("best_bid")),
        "touch_size_down": (dn_book.get("bids") or {}).get(dn_book.get("best_bid")),
    }
    traded = fetch_tape(cid, seen)
    rows = bucket_trades(traded, {str(up): mid_up, str(dn): mid_dn}, tick)
    return sample, rows, {str(up): "UP", str(dn): "DOWN"}


def run(minutes: float, interval: float, db_path: Path, run_id: str,
        markets_path: Path, clob_host: str,
        fetch_market: Callable[[str], Any],
        fetch_book: Callable[[str, str], dict],
        fetch_tape: Callable[[str, set], dict],
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep) -> int:
    """Poll every market on the list until the time box expires.

    Every fetch failure is logged and skipped rather than raised. A recorder
    that dies on one unreachable market records nothing for the rest of the
    hour, and the hour is the expensive part.
    """
    conn = open_store(db_path)
    deadline = now() + minutes * 60.0
    seen_by_cid: dict[str, set] = {}
    passes = samples = tape_rows = 0
    try:
        while now() < deadline:
            for market in load_universe(markets_path):
                cid = str(market["cid"])
                seen = seen_by_cid.setdefault(cid, set())
                # Read from `seen`, not from the presence of the key. A failed
                # book fetch aborts the pass AFTER `setdefault` has created
                # the empty set but BEFORE `fetch_tape` fills it, so keying on
                # the dict would mark the next pass as live -- and that pass
                # returns the venue's whole recent window, which would land in
                # the reachability curve as if it were current tape.
                first_pass = not seen
                try:
                    got = sample_market(market, fetch_market, fetch_book,
                                        fetch_tape, seen, clob_host)
                except Exception as e:                      # noqa: BLE001
                    log.warning("%s unreadable: %s", market.get("slug") or cid[:12], e)
                    # Drop the tape baseline. `fetch_tape` runs after the book
                    # fetches, so a pass that dies here never read the tape,
                    # and `recent_trades` keeps accumulating on the venue side
                    # meanwhile. Left alone, the recovery pass returns every
                    # trade from the missed interval with a non-empty `seen`
                    # behind it -- so it is written as LIVE and stamped
                    # against the recovery-time mid, which is the bootstrap
                    # bug again by a slower route. Clearing makes the recovery
                    # pass bootstrap, which is what it is.
                    seen.clear()
                    continue
                if got is None:
                    continue
                sample, rows, sides = got
                sample["run_id"] = run_id
                write_sample(conn, sample)
                write_tape(conn, sample["ts"], run_id, cid,
                           sample["market_slug"], sides, rows,
                           is_bootstrap=first_pass)
                conn.commit()
                samples += 1
                if not first_pass:
                    tape_rows += len(rows)
            passes += 1
            log.info("pass %d: %d samples, %d tape rows", passes, samples, tape_rows)
            sleep(interval)
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        conn.close()
    log.info("done: %d passes, %d book samples, %d tape rows -> %s",
             passes, samples, tape_rows, db_path)
    return 0


def _parse_args(argv: Optional[list[str]] = None):
    ap = argparse.ArgumentParser(
        description="Record book touch and tape-by-distance-from-mid. Read-only; "
                    "no signer is loaded and data/orders.db is refused.")
    ap.add_argument("--minutes", type=float, default=60.0,
                    help="time box in minutes (default: 60)")
    ap.add_argument("--interval", type=float, default=15.0,
                    help="seconds between passes over the market list (default: 15)")
    ap.add_argument("--db", default="data/booktape.db",
                    help="store path (default: data/booktape.db)")
    ap.add_argument("--run-id", default=None, help="run id stamped on every row")
    ap.add_argument("--markets", default="runtime/markets.json",
                    help="market list to follow (default: runtime/markets.json)")
    return ap.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    args = _parse_args(argv)
    from core_brain.markets import full_book, recent_trades
    from core_brain.trader_loop import _fetch_market

    run_id = args.run_id or f"booktape-{int(time.time())}"
    clob_host = os.environ.get("CLOB_HOST", "https://clob.polymarket.com")
    log.info("recording %s markets for %.0f min every %.0fs -> %s (run %s)",
             args.markets, args.minutes, args.interval, args.db, run_id)
    # The WHOLE tape, both taker sides. `recent_trades` defaults to taker
    # sells because that is the only volume that can fill a resting bid, which
    # is what the shadow fill model needs -- but `reachable_fraction` divides
    # by ALL tape, above-mid prints included, so a filtered feed here would
    # silently shrink the denominator and inflate every reachability number
    # this recorder has ever written.
    def whole_tape(condition_id, seen, limit=500):
        return recent_trades(condition_id, seen, limit=limit, taker_side=None)

    return run(args.minutes, args.interval, Path(args.db), run_id,
               Path(args.markets), clob_host,
               _fetch_market, full_book, whole_tape)


if __name__ == "__main__":
    raise SystemExit(main())
