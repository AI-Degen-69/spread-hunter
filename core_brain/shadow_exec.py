"""The shadow store's writers: rest, fill, and close, without a venue.

Everything here writes `data/shadow.db` through the same `OrderRegistry` the
live path uses, so the rows downstream stages read have the shape those stages
expect. What differs is where the facts come from -- a model instead of a venue
-- and the rows written HERE say so: order ids start `shadow-`, trade ids start
`shadow-`, and a merge close carries `method='shadow_merge'`.

One close in a shadow store is not written here and is not labelled: the exit
recorded by `core_brain/single_buy_saver.py` when the rehearsed pairs pass sells
a naked leg carries `method='single_buy_exit'`, the same string a live exit
carries. Labelling it would mean editing live money-path code to serve a
rehearsal. The store file is the boundary that always holds --
`data/shadow.db`, never `data/orders.db` -- and the row labels are a second
line on top of it, not a replacement for it.

`core_brain/shadow_guard.py` keeps this honest from the other side: the venue
object a shadow run holds cannot sign, and `data/orders.db` is refused as a
store before any of these functions can be reached.
"""
from __future__ import annotations

import logging
import math
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Callable, Optional

from core_brain.order_registry import (
    SIZE_EPS, CloseRecord, FillRecord, MarkoutRecord, OrderRecord, OrderRegistry,
    QuoteRecord, get_connection,
)
from core_brain.shadow_fills import (
    ShadowFill, ShadowRestingOrder, credit_fills, queue_ahead_at,
)

_log = logging.getLogger(__name__)

SHADOW_ORDER_PREFIX = "shadow-"
SHADOW_TRADE_PREFIX = "shadow-"
# The worst price above the touch a completion BUY may pay. This mirrors the
# SELL-side limit; the shadow client applies it because the caller passes raw
# best-ask touch pricing without a cushion.
MAX_BUY_SLIPPAGE = 0.02

# Mirrors `MakerConfig.pairs_exit_window_sec`. A fallback only: the session
# passes its own configured value in.
DEFAULT_PAIRS_WINDOW_SEC = 900.0


class ShadowOrderRefused(RuntimeError):
    """A simulated order broke a live cap and was not written."""


def _record_taker_completion(
    db_path: Path | str, pair_id: str, token_id: str, run_id: Optional[str]
) -> None:
    """Mark (pair_id, token_id) as completed by a taker order.

    The pnl_by_fill_path classifier reads this marker instead of guessing
    from order counts, which cancel-churn (post, cancel, repost) makes
    indistinguishable from a real maker-to-taker replacement. Writing it at
    the moment the completion buy is booked is the only place the fact is
    known first-hand.
    """
    try:
        with closing(get_connection(Path(db_path))) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO shadow_taker_completions "
                "(run_id, pair_id, token_id, ts) VALUES (?, ?, ?, ?)",
                (run_id or "", str(pair_id), str(token_id), time.time()),
            )
            conn.commit()
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        _log.warning("could not record taker completion marker: %s", exc)


def ensure_shadow_tables(db_path: Path | str) -> None:
    """Create the shadow-only tables beside the registry's own schema.

    Queue position is a property of the model, not of the venue, so it does not
    belong in `orders`. `shadow_merge_legs` is here for the same reason: the
    `closes` table records a merge's cost as one number plus an even UP/DOWN
    split, which is all a live merge can attribute, and the remaining-average
    cost of each leg needs the per-token figure the split throws away. Keeping
    both in their own tables also means a shadow store opened by live code
    reads as a registry with some odd ids, never as a registry with columns
    that do not exist.
    """
    with closing(get_connection(Path(db_path))) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_queue (
                run_id TEXT NOT NULL DEFAULT '',
                local_id TEXT NOT NULL,
                queue_ahead REAL NOT NULL,
                PRIMARY KEY (run_id, local_id)
            )
            """
        )
        # `shadow_queue` predates run scoping: earlier stores keyed queue state
        # by `local_id` alone, so a fresh run could read another run's leftover
        # queue-ahead and under-credit itself. Migrate it once -- old rows get
        # `run_id = ''`, which is "nobody's run" exactly like a NULL `run_id`
        # order in `settle_market`. A store that already has the column (new or
        # already migrated) is left alone.
        qcols = {r[1] for r in conn.execute("PRAGMA table_info(shadow_queue)")}
        if "run_id" not in qcols:
            conn.execute("ALTER TABLE shadow_queue RENAME TO shadow_queue_legacy")
            conn.execute(
                """
                CREATE TABLE shadow_queue (
                    run_id TEXT NOT NULL DEFAULT '',
                    local_id TEXT NOT NULL,
                    queue_ahead REAL NOT NULL,
                    PRIMARY KEY (run_id, local_id)
                )
                """
            )
            conn.execute(
                "INSERT INTO shadow_queue (run_id, local_id, queue_ahead) "
                "SELECT '', local_id, queue_ahead FROM shadow_queue_legacy"
            )
            conn.execute("DROP TABLE shadow_queue_legacy")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS queue_marks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                condition_id TEXT,
                market_slug TEXT,
                token_id TEXT NOT NULL,
                price REAL NOT NULL,
                level_size REAL NOT NULL,
                traded REAL NOT NULL DEFAULT 0,
                cancel_decay REAL,
                queue_minutes REAL,
                run_id TEXT,
                best_bid REAL,
                best_bid_size REAL
            )
            """
        )
        # `best_bid` was added after the first runs recorded thousands of
        # marks, and that history is the asset -- migrate the table rather
        # than asking for a store to be thrown away. Old rows keep NULL: they
        # were never observed, which is not the same as "no bid".
        cols = {r[1] for r in conn.execute("PRAGMA table_info(queue_marks)")}
        if "best_bid" not in cols:
            conn.execute("ALTER TABLE queue_marks ADD COLUMN best_bid REAL")
        if "best_bid_size" not in cols:
            conn.execute("ALTER TABLE queue_marks ADD COLUMN best_bid_size REAL")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_queue_marks_level "
            "ON queue_marks (token_id, price, ts)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_taker_completions (
                run_id TEXT NOT NULL DEFAULT '',
                pair_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                ts REAL NOT NULL,
                PRIMARY KEY (pair_id, token_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_merge_legs (
                tx_hash TEXT NOT NULL,
                pair_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                shares REAL NOT NULL,
                cost REAL NOT NULL,
                PRIMARY KEY (tx_hash, token_id)
            )
            """
        )
        conn.commit()


def write_queue_ahead(
    db_path: Path | str, run_id: str, local_id: str, queue_ahead: float
) -> None:
    """Persist one order's queue-ahead under THIS run's id.

    Keying by `(run_id, local_id)` rather than `local_id` alone keeps one
    rehearsal's queue position invisible to the next: `data/shadow.db` is
    reused between runs, and a fresh order reading a stale order's queue-ahead
    (a leftover half-eaten number from an earlier run) would under-credit its
    own tape.
    """
    with closing(get_connection(Path(db_path))) as conn:
        conn.execute(
            "INSERT INTO shadow_queue (run_id, local_id, queue_ahead) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(run_id, local_id) DO UPDATE SET "
            "queue_ahead = excluded.queue_ahead",
            (run_id, local_id, float(queue_ahead)),
        )
        conn.commit()


def read_queue_ahead(db_path: Path | str, run_id: str, local_id: str) -> float:
    """The queue-ahead this run recorded for this order, or 0.0 if this run
    never recorded one. Never another run's row: a stale value here would
    consume tape that belonged to the order as if it had already been carved
    out by someone else's queue."""
    with closing(get_connection(Path(db_path))) as conn:
        row = conn.execute(
            "SELECT queue_ahead FROM shadow_queue "
            "WHERE run_id = ? AND local_id = ?",
            (run_id, local_id),
        ).fetchone()
    return float(row["queue_ahead"]) if row else 0.0



def record_submit(
    _client,
    registry: OrderRegistry,
    market,
    intents: list,
    cfg,
    *,
    db_path: Path | str,
    book_fn: Callable[[str, str], dict],
    clob_host: str = "https://clob.polymarket.com",
    now_fn: Callable[[], float] = time.time,
) -> int:
    """Rest each decided intent as an order row, under the live caps.

    Mirrors `core_brain/trader_loop.py:_submit_intents` in everything that is
    not the venue call: the same per-leg `MAX_ORDER_USD` check, the same shared
    `pair_id`, the same `max_pair_cost_at_post` stamp, and a `QuoteRecord` per
    leg. A rehearsal under looser caps rehearses something we do not ship.

    The quote row is not bookkeeping. The quotes ledger is the registry's only
    record of which token is the UP leg and which the DOWN -- the closes table
    has no token column -- so `single_buy_saver._token_side` reads it, and
    `exit_single_buy` refuses the whole exit when it comes back None. Without
    these rows the half of the pairs pass that owns the pair-over-$1.00 case
    is structurally unreachable in a rehearsal: every pair returns
    `action: 'error'`, "the quotes ledger has no side for it".

    Fields the venue supplies live and a rehearsal does not (`latency_ms`) are
    left unset rather than filled with a plausible number.
    """
    from core_brain.venue import MAX_ORDER_USD, MAX_TOTAL_USD

    if not intents:
        return 0

    for i in intents:
        if i.price * i.size > MAX_ORDER_USD:
            raise ShadowOrderRefused(
                f"leg {i.side} ${i.price * i.size:.2f} exceeds "
                f"MAX_ORDER_USD ${MAX_ORDER_USD:.2f}")

    open_usd = sum(o.price * o.original_size for o in registry.get_active_orders())
    total_cost = sum(i.price * i.size for i in intents)
    if open_usd + total_cost > MAX_TOTAL_USD:
        raise ShadowOrderRefused(
            f"open ${open_usd:.2f} + ${total_cost:.2f} exceeds "
            f"MAX_TOTAL_USD ${MAX_TOTAL_USD:.2f}")

    now_ms = int(now_fn() * 1000)
    pair_id = f"pair-{uuid.uuid4().hex[:12]}"
    max_pair_cost = float(getattr(cfg, "max_pair_cost", 0.995))

    # Same carry `trader_loop._submit_intents` applies: a replacement leg
    # tagged by `plan_orders` joins the pair whose complement still rests,
    # so a rehearsal splits no pairs the live loop would no longer split.
    carried = {getattr(i, "pair_id", None) for i in intents} - {None}
    if len(carried) == 1:
        pair_id = carried.pop()

    placed = 0
    created_local_ids: list[str] = []
    try:
        for i in intents:
            local_id = str(uuid.uuid4())
            order_id = f"{SHADOW_ORDER_PREFIX}{uuid.uuid4().hex[:12]}"
            registry.create_order(OrderRecord(
                id=local_id,
                order_id=order_id,
                condition_id=market.condition_id, token_id=str(i.token_id),
                side="BUY", price=i.price, original_size=i.size, status="open",
                posted_ts=now_ms, last_polled_ts=now_ms, pair_id=pair_id,
                max_pair_cost_at_post=max_pair_cost,
            ))
            created_local_ids.append(local_id)
            try:
                book = book_fn(clob_host, str(i.token_id))
            except (OSError, ValueError):
                # Degrade, do not stop. An unread book means the queue is unknown,
                # and a queue of zero is the optimistic direction, so assume a level
                # as deep as this order instead: that never over-credits.
                book = {"bids": {round(float(i.price), 4): float(i.size)}}
            queue_ahead = queue_ahead_at(book, i.price)
            write_queue_ahead(db_path, registry._run_id(), local_id, queue_ahead)
            registry.log_quote(QuoteRecord(
                ts=now_fn(),
                market_slug=getattr(market, "market_slug", None),
                condition_id=market.condition_id,
                token_id=str(i.token_id),
                side=i.side,
                price=i.price,
                size=i.size,
                queue_ahead=queue_ahead,
                mid=getattr(i, "mid", None),
                edge_vs_mid=getattr(i, "edge_vs_mid", None),
                order_id=order_id,
                local_id=local_id,
            ))
            placed += 1
    except Exception:
        # Any exception mid-loop: cancel all created orders and re-raise.
        for local_id in created_local_ids:
            try:
                registry.update_order_status(local_id, status="cancelled", last_polled_ts=now_ms)
            except (KeyError, sqlite3.Error) as e:
                _log.exception(f"Failed to cancel order {local_id}: {e}")
        raise

    return placed



def write_queue_mark(db_path: Path | str, *, ts: float, condition_id, market_slug,
                     token_id: str, price: float, level_size: float,
                     traded: float, cancel_decay, queue_minutes,
                     run_id=None, best_bid=None, best_bid_size=None) -> None:
    """One observation of the queue at one price on one token.

    `best_bid` is the book's touch at the moment of the mark, and it is what
    makes "we were passed" observable: size joining at our own price sits
    BEHIND us under price-time priority and already shows as a negative
    `cancel_decay`, while a better bid appearing means every share of tape now
    clears against someone else first. From our level's size alone the two are
    identical. None means the book had no bid -- a real state, and not the
    same as being outbid by everything. `best_bid_size` is the depth at the
    touch, separate from the resting order's `level_size`.
    """
    with closing(get_connection(Path(db_path))) as conn:
        conn.execute(
            """
            INSERT INTO queue_marks (
                ts, condition_id, market_slug, token_id, price, level_size,
                traded, cancel_decay, queue_minutes, run_id, best_bid,
                best_bid_size
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (float(ts), condition_id, market_slug, str(token_id),
             round(float(price), 4), float(level_size), float(traded),
             cancel_decay, queue_minutes, run_id,
             None if best_bid is None else round(float(best_bid), 4),
             None if best_bid_size is None else float(best_bid_size)),
        )
        conn.commit()


def read_last_queue_mark(db_path: Path | str, token_id: str, price: float,
                         run_id=None):
    """The previous observation of this exact level in this run, or None.

    Keyed on (token, price) and not on token alone: two levels on one token are
    two independent queues, and differencing across them would invent decay
    that never happened.

    Scoped to `run_id` for the same reason, one gap larger. `data/shadow.db` is
    reused between rehearsals by design, so without the run filter the first
    cycle of a new run differences its level against the LAST cycle of an older
    one -- hours or days earlier. The decay and the clear time that come out of
    that gap are fiction, and they land in the first rows an operator reads.
    """
    sql = ("SELECT * FROM queue_marks WHERE token_id = ? AND price = ?")
    args: list = [str(token_id), round(float(price), 4)]
    if run_id is not None:
        sql += " AND run_id = ?"
        args.append(run_id)
    with closing(get_connection(Path(db_path))) as conn:
        row = conn.execute(sql + " ORDER BY ts DESC, id DESC LIMIT 1",
                           tuple(args)).fetchone()
    return dict(row) if row else None


def read_last_token_snapshot(db_path: Path | str, token_id: str,
                             run_id=None):
    """The most recent book observation of this token in this run, or None.

    Token-only, deliberately: the exit path has no single resting price to
    key on (a token position can come from fills at several prices), so the
    (token, price) key `read_last_queue_mark` uses cannot serve it. The
    `best_bid` and `level_size` values are the same top-of-book facts no
    matter which resting-price row they arrived on.

    Scoped to `run_id` for the reason `read_last_queue_mark` documents:
    `data/shadow.db` is reused between rehearsals, and another run's book is
    not this run's pre-decision book. Returns the row as a dict, or None when
    the run has never observed this token.
    """
    sql = "SELECT * FROM queue_marks WHERE token_id = ?"
    args: list = [str(token_id)]
    if run_id is not None:
        sql += " AND run_id = ?"
        args.append(run_id)
    with closing(get_connection(Path(db_path))) as conn:
        row = conn.execute(sql + " ORDER BY ts DESC, id DESC LIMIT 1",
                           tuple(args)).fetchone()
    return dict(row) if row else None


def _record_queue_marks(db_path, market, orders, traded, book_fn, now,
                        run_id=None, clob_host=None) -> None:
    """Observe every level we rest at: its size, its tape, and what cancelled.

    Runs inside `settle_market` because that is the only place the tape exists.
    `markets.recent_trades` de-duplicates against a per-market `seen` set, so a
    second call in the same cycle returns nothing at all.

    The split is a subtraction. The level fell by `before - now`; the tape
    explains `traded` of it; the residual is cancels. That residual is the
    number the whole fill model is missing -- it credits queue progress only
    from trades, so it cannot see the mechanism most likely to move a maker up
    a 13,000-share queue.

    Telemetry, therefore never fatal. A book read that fails costs one cycle of
    observation; raising here would take down a rehearsal to protect a
    measurement.
    """
    from scoring.selector import cancel_decay as _decay
    from scoring.selector import queue_minutes_at

    seen_levels = set()
    for o in orders:
        key = (str(o.token_id), round(float(o.price), 4))
        if key in seen_levels:
            continue
        seen_levels.add(key)
        token_id, price = key
        try:
            book = book_fn(clob_host, token_id)
            level_size = queue_ahead_at(book, price)
        except Exception as exc:  # noqa: BLE001 - telemetry degrades, never stops
            _log.warning("queue mark skipped for %s @ %.4f: %s",
                         str(token_id)[:12], price, exc)
            continue

        at_level = float((traded or {}).get(token_id, {}).get(price, 0.0))
        prev = read_last_queue_mark(db_path, token_id, price, run_id=run_id)
        decay = qmin = None
        if prev is not None:
            decay = _decay(prev["level_size"], level_size, at_level)
            elapsed_min = (float(now) - float(prev["ts"])) / 60.0
            qmin = queue_minutes_at(level_size, at_level, elapsed_min)

        bb = (book or {}).get("best_bid")
        bb_size = queue_ahead_at(book, bb) if bb is not None else None
        write_queue_mark(
            db_path, ts=float(now),
            condition_id=getattr(market, "condition_id", None),
            market_slug=getattr(market, "market_slug", None),
            token_id=token_id, price=price, level_size=level_size,
            traded=at_level, cancel_decay=decay, queue_minutes=qmin,
            run_id=run_id, best_bid=bb, best_bid_size=bb_size)


def _filled_size(db_path: Path | str, local_id: str) -> float:
    with closing(get_connection(Path(db_path))) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(size), 0) AS s FROM fills WHERE order_uuid = ?",
            (local_id,),
        ).fetchone()
    return float(row["s"]) if row else 0.0


def _market_slug(market) -> Optional[str]:
    """The market's slug, under whichever name this object carries it.

    The market specs the loop rotates expose `market_slug` -- the name the
    quotes, orders and queue-mark writers all read. `slug` is what the pinned
    market objects and the filter's own rows use. Reading only one of the two
    is why the first sweep's markout rows came back with a NULL slug while
    every quote row beside them was labelled.
    """
    for attr in ("market_slug", "slug"):
        value = getattr(market, attr, None)
        if value:
            return str(value)
    return None


def _log_shadow_markout(
    registry: OrderRegistry,
    order,
    market,
    *,
    price: float,
    size: float,
    fill_sec: float,
) -> None:
    """Open the adverse-selection row a shadow fill would otherwise never get.

    Live, every fill gets one of these from `order_registry.reconcile_orders`,
    which reads the venue's trade tape -- a path a rehearsal has no equivalent
    of. Without a row here `markouts` stays empty in every shadow store no
    matter how long the session runs, and the sampler has nothing to mature.
    Both completed runs show exactly that: fills present, `markouts` empty.

    `ref_mid` is the fill price with `ref_mid_source='contaminated'`, the same
    fallback the live writer uses, so the two paths produce comparable rows.

    Never raises. A rehearsal that cannot write telemetry must still rehearse.
    """
    if order is None:
        return
    condition_id = getattr(order, "condition_id", None) or getattr(
        market, "condition_id", None)
    if not condition_id:
        return
    try:
        registry.log_markout(MarkoutRecord(
            ts=fill_sec,
            condition_id=str(condition_id),
            side=str(getattr(order, "side", "BUY") or "BUY"),
            token_id=getattr(order, "token_id", None),
            market_slug=_market_slug(market),
            fill_price=price,
            size=size,
            ref_mid=price,
            ref_mid_source="contaminated",
            run_id=registry._run_id(),
        ))
    except (sqlite3.Error, OSError, ValueError) as e:
        _log.warning("shadow markout row not written: %s", e)


def settle_market(
    registry: OrderRegistry,
    market,
    *,
    db_path: Path | str,
    traded_fn: Callable[[str, set], dict],
    seen: set,
    now_fn: Callable[[], float] = time.time,
    book_fn: Optional[Callable] = None,
    clob_host: Optional[str] = None,
) -> list[ShadowFill]:
    """Credit this market's resting rows from the tape, then persist the result.

    One `seen` set per market for the whole session: `markets.recent_trades`
    de-duplicates by trade identity against it, and a fresh set every cycle
    would re-credit the same trades until the order looked full.

    `book_fn` is OPT-IN queue telemetry. Passing one records a `queue_marks`
    row per level we rest at: the size there now, the tape at that exact price,
    the residual that cancelled, and the implied minutes to clear. It lives
    here rather than in its own pass because the tape is consumed once -- the
    `seen` set above means a second `recent_trades` call this cycle returns
    nothing. Omitting `book_fn` keeps the previous behaviour exactly.
    """
    traded = traded_fn(market.condition_id, seen)

    # Scoped to THIS run's orders. `data/shadow.db` is reused between
    # rehearsals by design, and `get_active_orders` reads every open row in
    # the store: without this filter a run started while an older run's
    # orders were still resting measures THOSE as its own -- six marks of the
    # 08-26 rehearsal (shadow-c52e533f5725) sat at the previous rehearsal's
    # prices, four minutes before it quoted anything, and a verification pass
    # reasoned from them to a false conclusion about touch-resting.
    mine_run_id = registry._run_id()
    resting = [
        o for o in registry.get_active_orders()
        if o.condition_id == market.condition_id and o.status in ("open", "partial")
        and (o.run_id or mine_run_id) == mine_run_id
    ]
    if not resting:
        return []

    # Observation (queue marks) covers every resting order we can see, including
    # unowned legacy rows: a NULL run_id is "nobody's order" but still sitting in
    # the book, and dropping it would silently shrink the resting set. Fill credit
    # is stricter -- only orders this run actually owns may consume this run's
    # tape. Claiming a NULL-run row's tape would let a stale, foreign order
    # absorb volume that belongs to a current-run order resting at the same price.
    owned = [o for o in resting if o.run_id == mine_run_id]
    orders = [
        ShadowRestingOrder(
            local_id=o.id, token_id=o.token_id, price=o.price,
            size=o.original_size, filled=_filled_size(db_path, o.id),
            queue_ahead=read_queue_ahead(db_path, mine_run_id, o.id),
        )
        for o in owned
    ]
    marks = [
        ShadowRestingOrder(
            local_id=o.id, token_id=o.token_id, price=o.price,
            size=o.original_size, filled=_filled_size(db_path, o.id),
            queue_ahead=read_queue_ahead(db_path, mine_run_id, o.id),
        )
        for o in resting
    ]
    fills, queues = credit_fills(orders, traded)

    for local_id, queue_ahead in queues.items():
        write_queue_ahead(db_path, mine_run_id, local_id, queue_ahead)

    if book_fn is not None:
        _record_queue_marks(db_path, market, marks, traded, book_fn,
                            now_fn(), run_id=registry._run_id(),
                            clob_host=clob_host)

    now_ms = int(now_fn() * 1000)
    by_id = {o.local_id: o for o in orders}
    owned_by_id = {o.id: o for o in owned}
    for f in fills:
        registry.record_fill(FillRecord(
            trade_id=f"{SHADOW_TRADE_PREFIX}{uuid.uuid4().hex[:16]}",
            order_uuid=f.local_id, size=f.size, price=f.price,
            venue_ts=now_ms, recorded_ts=now_ms,
        ))
        _log_shadow_markout(
            registry, owned_by_id.get(f.local_id), market,
            price=f.price, size=f.size, fill_sec=now_ms / 1000.0,
        )
        order = by_id[f.local_id]
        total_filled = order.filled + f.size
        status = "filled" if total_filled >= order.size - 1e-9 else "partial"
        registry.update_order_status(f.local_id, status=status,
                                     last_polled_ts=now_ms)

    return fills


def _book_side_to_levels(side, *, reverse: bool) -> list:
    """A price-keyed dict side (`markets.parse_book`'s canonical shape) ->
    a list of `{"price", "size"}` levels, sorted the way the venue returns
    them.

    `single_buy_saver._book_levels` iterates `bids`/`asks` expecting a list;
    handed the canonical dict instead it iterates the dict's float keys, and
    every one is discarded (`getattr(0.51, "price", None)` is `None`), so
    `best_ask`/`best_bid` silently come back `None` for every book. Bids sort
    best-first (highest price), asks best-first (lowest price) -- the same
    order the CLOB's own `/book` endpoint returns.

    A side that is not a dict (already a list, or absent) passes through
    unchanged: `book_fn` need not always be `markets.parse_book`, and a
    caller already handing over levels should not be reshaped.
    """
    if not isinstance(side, dict):
        return list(side or [])
    prices = sorted(side.keys(), reverse=reverse)
    return [{"price": p, "size": side[p]} for p in prices]


def pessimistic_completion_price(base_price: float, tick: float) -> float:
    """One completion, priced one tick worse than the base ask.

    The base path fills a completion at the ask the caller passes in (see
    `ShadowExecutionClient.create_and_post_market_order`) -- the optimistic
    end for a taker. This is the what-if price the pessimistic report variant
    recosts that completion at: ask + one tick. It is a pure helper, and it
    does NOT change how `create_and_post_market_order` fills the recorded
    base path; it only feeds the sensitivity column.
    """
    return float(base_price) + float(tick)


class ShadowExecutionClient:
    """The four client methods `single_buy_saver` calls, backed by the store.

    Deliberately narrow: an SDK method this class does not implement raises
    `AttributeError` at the call site -- ordinary Python attribute lookup on a
    class that never defined it -- which is a loud failure rather than a
    silent no-op that would make a rehearsal look successful.

    A market order here is a fill, immediately, against the book levels the
    caller's touch price permits. That models the optimistic timing of a
    rehearsal while still exposing thin depth and weighted execution prices.
    """

    def __init__(self, registry: OrderRegistry, db_path: Path | str, *,
                 book_fn: Callable[[str, str], dict],
                 clob_host: str = "https://clob.polymarket.com",
                 window_sec: float = DEFAULT_PAIRS_WINDOW_SEC,
                 now_fn: Callable[[], float] = time.time) -> None:
        self._registry = registry
        self._db_path = Path(db_path)
        self._book_fn = book_fn
        self._clob_host = clob_host
        # The same window `auto_manage_pairs` discovers pairs by. It has to
        # match, or this shim books a completion to a pair the pass is not
        # acting on -- see `_naked_pair_for_token`. The caller passes the
        # session's own `cfg.pairs_exit_window_sec`; the default here is the
        # one `MakerConfig` carries.
        self._window_sec = float(window_sec)
        self._now_fn = now_fn

    def get_order_book(self, token_id: str) -> dict:
        """The book, adapted for `single_buy_saver._book_levels`.

        `book_fn` (in production, `seam.fetch_books`, ultimately
        `markets.parse_book`) returns the canonical shape this repo uses
        everywhere else: `bids`/`asks` as price-keyed dicts, which is what
        `queue_ahead_at` (this store's own queue-position math) wants.
        `single_buy_saver._book_levels` wants a list of `{"price", "size"}`
        levels per side instead. Both are real consumers of the same book
        source, so the adaptation happens here, on the way out to the one
        that needs it -- `markets.parse_book` and the production book source
        stay untouched.
        """
        book = self._book_fn(self._clob_host, str(token_id))
        adapted = dict(book)
        adapted["bids"] = _book_side_to_levels(book.get("bids"), reverse=True)
        adapted["asks"] = _book_side_to_levels(book.get("asks"), reverse=False)
        return adapted

    def get_order(self, order_id: str) -> dict:
        """Venue-shaped order state, read by venue order id.

        Looked up with `get_order_by_venue_id`, which queries by id
        regardless of status -- not `get_active_orders`, which filters to
        pending/open/partial. `single_buy_saver` always calls this right
        after `cancel_order` on the same order, so by the time it asks the
        row is already `cancelled`; a status-filtered lookup would see
        nothing and the caller would refuse the whole pass believing the
        venue returned an unrecognisable response.
        """
        order = self._registry.get_order_by_venue_id(str(order_id))
        if order is None:
            return {}
        return {
            "id": order.order_id or order.id,
            "status": order.status,
            "size_matched": _filled_size(self._db_path, order.id),
            "original_size": order.original_size,
            "price": order.price,
        }

    def cancel_order(self, payload) -> dict:
        target = (getattr(payload, "orderID", None)
                  or getattr(payload, "order_id", None))
        order = self._registry.get_order_by_venue_id(str(target)) if target else None
        if order is None:
            return {"success": False, "orderID": target}
        now_ms = int(time.time() * 1000)
        self._registry.update_order_status(
            order.id, status="cancelled", last_polled_ts=now_ms)
        return {"success": True, "orderID": target}

    def _naked_pair_for_token(self, token_id: str) -> tuple[str, str] | None:
        """(condition_id, pair_id) of the pair on this token the pairs pass is
        currently acting on, or `None` if there is no such pair.

        `MarketOrderArgsV2` carries no condition id or pair id -- only
        `token_id`, `amount`, `side`, `price` -- so a completion buy has no
        way to name its pair directly; this reconstructs it.

        Scoping this to "the most recent order row on this token" (the first
        version) is wrong whenever a token carries rows under more than one
        `pair_id`, which is ordinary here: `record_submit` mints a fresh
        `pair_id` every call, and a market re-quoted across rotations
        accumulates several.

        Picking the OLDEST naked pair (the version after that) is wrong for
        the opposite reason. It assumed `auto_manage_pairs` reaches the oldest
        naked pair first; it does not -- it SKIPS any pair whose last fill is
        older than `pairs_exit_window_sec`, so the oldest naked pair is
        precisely the one it never acts on. `data/shadow.db` survives between
        sessions, so stale naked pairs accumulate, and every completion made
        for a fresh pair was booked to a stale one instead: the fresh pair
        read naked again next cycle and was bought again. With N stale naked
        pairs that is N+1 completion buys.

        So this reproduces `auto_manage_pairs`' own discovery rule instead: of
        the pairs actually naked on this token, keep those whose LAST FILL is
        inside the window, and take the most recent of them (`posted_ts`
        breaks a tie). An undated fill is left out, exactly as the pass leaves
        it out. When nothing qualifies the caller refuses -- loudly -- rather
        than crediting shares to a position nothing is managing.
        """
        with closing(get_connection(self._db_path)) as conn:
            rows = conn.execute(
                """
                SELECT o.pair_id AS pair_id, MIN(o.condition_id) AS condition_id,
                       o.token_id AS token_id, MIN(o.posted_ts) AS posted_ts,
                       COALESCE(SUM(f.size), 0) AS matched,
                       COALESCE(MAX(f.venue_ts), 0) AS last_fill_ts
                FROM orders o
                LEFT JOIN fills f ON f.order_uuid = o.id
                WHERE o.pair_id IN (
                    SELECT DISTINCT pair_id FROM orders
                    WHERE token_id = ? AND pair_id IS NOT NULL
                )
                GROUP BY o.pair_id, o.token_id
                """,
                (str(token_id),),
            ).fetchall()

        by_pair: dict[str, dict] = {}
        for r in rows:
            pid = r["pair_id"]
            slot = by_pair.setdefault(pid, {
                "condition_id": r["condition_id"], "legs": {},
                "posted_ts": r["posted_ts"], "last_fill_ts": 0.0,
            })
            slot["legs"][r["token_id"]] = float(r["matched"])
            slot["posted_ts"] = min(slot["posted_ts"], r["posted_ts"])
            slot["last_fill_ts"] = max(
                slot["last_fill_ts"], float(r["last_fill_ts"] or 0.0))

        target = str(token_id)
        window_ms = self._window_sec * 1000.0
        now_ms = self._now_fn() * 1000.0
        naked_candidates = []
        for pid, info in by_pair.items():
            legs = info["legs"]
            this_matched = legs.get(target, 0.0)
            other_matched = max(
                (v for k, v in legs.items() if k != target), default=0.0)
            naked = other_matched - this_matched
            if naked <= SIZE_EPS:
                continue
            last_fill_ms = info["last_fill_ts"]
            if last_fill_ms <= 0 or (now_ms - last_fill_ms) > window_ms:
                continue
            naked_candidates.append(
                (last_fill_ms, info["posted_ts"], pid, info["condition_id"]))

        if not naked_candidates:
            return None
        naked_candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
        _, _, pid, cid = naked_candidates[0]

        if not cid:
            raise ShadowOrderRefused(
                f"cannot record completion buy for {str(token_id)[:12]}: pair "
                f"{pid} has empty condition_id, which would make the "
                f"completion row invisible to inventory tracking")

        return str(cid), pid

    def _sell_into_bids(self, token_id: str, shares: float,
                        *, floor: float) -> tuple[float, float]:
        """Rest a market SELL against the pre-decision bid ladder.

        Returns `(shares_filled, weighted_average_price)`.

        The book filled against is the PRIOR ROTATION's snapshot, not a fresh
        read at post time. Seconds pass between the sweep's decision and the
        taker SELL landing, and the fills we get live are precisely the
        adversely-selected ones -- shadow-01 showed h1 markout drift of -17c
        to -37c on 4 of 12 exits. Filling the rehearsal against the book it
        reads at the moment of the simulated post books an exit price the
        venue never gave. The snapshot source is `queue_marks`, which
        `settle_market` writes per rotation BEFORE the exit sweep runs, so the
        newest row per token is the book the decision was made against. The
        snapshot carries one level -- `best_bid` at the stored `level_size`
        depth -- so a short fill at the touch is the honest answer to a thin
        snapshot.

        With no snapshot for this run (first rotation on a token, or a session
        that never recorded marks) the fresh book is used, which is the
        previous behaviour and still better than the floor.

        This used to return the caller's `floor` as though it were the fill.
        The floor is `best_bid - MAX_SELL_SLIPPAGE`, a flat 2c -- the worst
        price `single_buy_saver` will accept, not the price a taker gets. So
        every rehearsed exit was charged a 2c concession the venue never took,
        and that charge is most of the "2-3c per stranded leg" the pair-fill
        report attributes to the market. A real market SELL takes the touch
        first and only walks down when the touch is thin.

        The floor stays a real limit in both paths: depth beneath it is not
        ours to take, so a book that is thin above it fills short rather than
        selling through. With no book at all there is no better information
        than the floor, and the old conservative answer stands.
        """
        snapshot_ladder: list[tuple[float, float]] = []
        try:
            snap = read_last_token_snapshot(
                self._db_path, str(token_id), run_id=self._registry._run_id())
        except sqlite3.Error as e:
            _log.warning("exit snapshot read failed for %s: %s",
                         str(token_id)[:12], e)
            snap = None
        if snap is not None and snap.get("best_bid") is not None:
            best_bid = float(snap["best_bid"])
            depth = float(snap.get("best_bid_size") or 0.0)
            if math.isfinite(best_bid) and best_bid > 0 and depth > 0:
                snapshot_ladder = [(best_bid, depth)]

        if snapshot_ladder:
            levels = snapshot_ladder
        else:
            try:
                book = self.get_order_book(token_id)
            except Exception:  # noqa: BLE001 - a bookless rehearsal keeps the floor
                book = {}
            levels = [
                (float(lvl["price"]), float(lvl["size"]))
                for lvl in (book or {}).get("bids") or []
                if float(lvl.get("size") or 0.0) > 0
            ]
            levels.sort(key=lambda pl: pl[0], reverse=True)

        remaining = float(shares)
        notional = 0.0
        taken = 0.0
        for lvl_price, lvl_size in levels:
            if remaining <= 0 or lvl_price < float(floor):
                break
            fill = min(remaining, lvl_size)
            notional += fill * lvl_price
            taken += fill
            remaining -= fill

        if taken <= 0:
            return float(shares), float(floor)
        return taken, notional / taken

    def _buy_from_asks(self, token_id: str, amount: float,
                       *, price: float) -> tuple[float, float]:
        """Cross the ask ladder up to the BUY slippage ceiling.

        Returns `(shares_filled, weighted_average_price)`. The caller's
        `price` is the touch, not a guaranteed fill: eligible asks are walked
        from best to worse until the requested notional is spent. Thin depth
        therefore produces a short fill. If no usable book is available, the
        old touch-price fallback remains in place.
        """
        try:
            book = self.get_order_book(token_id)
        except Exception:  # noqa: BLE001 - a bookless rehearsal keeps the fallback
            book = {}

        ceiling = float(price) + MAX_BUY_SLIPPAGE
        remaining = float(amount)
        notional = 0.0
        taken = 0.0
        levels = []
        for lvl in (book or {}).get("asks") or []:
            try:
                lvl_price = float(lvl.get("price"))
                lvl_size = float(lvl.get("size") or 0.0)
            except (AttributeError, TypeError, ValueError):
                continue
            if (not math.isfinite(lvl_price) or lvl_price <= 0
                    or not math.isfinite(lvl_size) or lvl_size <= 0):
                continue
            levels.append((lvl_price, lvl_size))

        # Canonical dict books are already ascending, but injected list-shaped
        # books are not guaranteed to be ordered.
        levels.sort(key=lambda level: level[0])
        for lvl_price, lvl_size in levels:
            if remaining <= 0 or lvl_price > ceiling:
                break
            fill = min(lvl_size, remaining / lvl_price)
            notional += fill * lvl_price
            taken += fill
            remaining -= fill * lvl_price

        if taken <= 0:
            return float(amount) / float(price), float(price)
        return taken, notional / taken

    def create_and_post_market_order(self, order_args) -> dict:
        """Cross the book in the rehearsal: called with one positional
        `MarketOrderArgsV2(token_id=..., amount=..., side=..., price=...)`.

        `amount` is not shares on both sides. On a BUY it is notional dollars
        -- the maker amount the SDK's `get_market_order_amounts` treats as
        what we give -- so shares received are `amount / price`. On a SELL
        `amount` already is shares. Reading a BUY's dollar amount as a share
        count is exactly the bug `single_buy_saver.py:905-915` documents:
        the guards that validated the dollar figure would not catch it.

        A SELL writes nothing to the store: `single_buy_saver._record_exit_close`
        records the close as the sole ledger entry for that trade (the
        docstring on `exit_single_buy` explains why -- a taker SELL has no
        resting row for reconcile to adopt), and a fill row here would
        double-count the same exit. A BUY writes both an order and a fill row
        itself: a shadow run has no poll loop or reconcile to pick a
        completion fill up otherwise.
        """
        token_id = str(getattr(order_args, "token_id", "") or "")
        amount = float(getattr(order_args, "amount", 0.0) or 0.0)
        side = str(getattr(order_args, "side", "") or "").upper()
        raw_price = getattr(order_args, "price", None)
        price = float(raw_price) if raw_price is not None else None

        if (not token_id or not math.isfinite(amount) or amount <= 0
            or price is None or not math.isfinite(price) or price <= 0):
            raise ShadowOrderRefused(
                f"cannot cross {token_id[:12] or '?'} for amount={amount}: "
                f"missing token, amount or price")

        if side == "SELL":
            filled, avg = self._sell_into_bids(token_id, amount, floor=price)
            return {
                "success": True,
                "orderID": f"{SHADOW_ORDER_PREFIX}{uuid.uuid4().hex[:12]}",
                "status": "matched", "price": avg, "size": filled,
            }

        if side != "BUY":
            raise ShadowOrderRefused(
                f"unrecognised order side {side!r} for {token_id[:12]}")

        shares, fill_price = self._buy_from_asks(token_id, amount, price=price)

        found = self._naked_pair_for_token(token_id)
        if found is None:
            raise ShadowOrderRefused(
                f"cannot record completion buy for {token_id[:12]}: no pair "
                f"on this token reads as naked in the store within the last "
                f"{self._window_sec:.0f}s, so there is nothing the pairs pass "
                f"could be completing and nothing to attach the fill to")
        condition_id, pair_id = found

        now_ms = int(time.time() * 1000)
        local_id = str(uuid.uuid4())
        completion = OrderRecord(
            id=local_id,
            order_id=f"{SHADOW_ORDER_PREFIX}{uuid.uuid4().hex[:12]}",
            condition_id=condition_id, token_id=token_id, side="BUY",
            price=fill_price, original_size=shares, status="filled",
            posted_ts=now_ms, last_polled_ts=now_ms, pair_id=pair_id,
        )
        self._registry.create_order(completion)
        _record_taker_completion(self._registry.db_path, pair_id, token_id,
                                 self._registry.run_id)
        self._registry.record_fill(FillRecord(
            trade_id=f"{SHADOW_TRADE_PREFIX}{uuid.uuid4().hex[:16]}",
            order_uuid=local_id, size=shares, price=fill_price,
            venue_ts=now_ms, recorded_ts=now_ms,
        ))
        # A completion buy is a fill like any other, and the live path writes a
        # markout for it too. Leaving it out would make the taker leg of every
        # completed pair invisible to the adverse-selection measurement.
        _log_shadow_markout(self._registry, completion, None,
                    price=fill_price, size=shares,
                            fill_sec=now_ms / 1000.0)
        return {"success": True, "orderID": local_id, "status": "matched",
                "price": fill_price, "size": shares}


def _side_by_token(registry: OrderRegistry) -> dict[tuple[str, str], str]:
    """UP/DOWN per (condition_id, token_id), from the quotes ledger.

    The same mapping `single_buy_saver._token_side` reads, and the same one
    `kpi.token_side_map` renders from: the closes table has no token column, so
    every path that has to say WHICH leg something happened to resolves it from
    the side each posted order logged with its quote. Newest quote wins.

    Built once per caller rather than per lookup -- `get_all_quotes` walks the
    whole ledger, and the merge pass needs a side for both legs of every pair
    it closes.
    """
    side_of: dict[tuple[str, str], str] = {}
    seen_ts: dict[tuple[str, str], float] = {}
    for q in registry.get_all_quotes():
        cid, token = q.get("condition_id"), q.get("token_id")
        side = str(q.get("side") or "").upper()
        if not cid or not token or side not in ("UP", "DOWN"):
            continue
        key = (str(cid), str(token))
        ts = float(q.get("ts") or 0.0)
        if ts >= seen_ts.get(key, -1.0):
            seen_ts[key] = ts
            side_of[key] = side
    return side_of


def _leg_cost_removed(leg_costs: list[tuple[str, float]],
                      side_of: dict[tuple[str, str], str],
                      condition_id: str) -> tuple[float, float]:
    """A merged pair's cost basis, split into the UP and the DOWN column.

    Each leg is charged what it actually cost. On a spread pair the two are
    never equal -- 0.252 + 0.618 is the whole point of the strategy -- and the
    live merge already records the real figures (`order_manager`, the
    `method="merge"` close, `amt * up_unit_cost` / `amt * dn_unit_cost`). This
    is what makes the rehearsal agree with it.

    It is not presentation. `kpi` subtracts `up_cost_removed` from the running
    UP inventory cost when a merge closes, so an even split leaves BOTH legs
    carrying a basis nothing paid: on shadow-01 close #50 the legs cost $1.512
    and $3.708 and were recorded as $2.61 each, over-charging UP by $1.098 and
    under-charging DOWN by the same.

    The fallback is the even split, taken whenever the two legs do not resolve
    to one UP and one DOWN. The quotes ledger is the only side source here, and
    guessing which leg is which would put the whole discrepancy on the wrong
    one -- worse than the imprecision it replaced. Returns the total unchanged
    either way: this function moves cost between the columns, never creates or
    loses any.
    """
    total = sum(float(cost) for _token, cost in leg_costs)
    labelled: dict[str, float] = {}
    for token, cost in leg_costs:
        side = side_of.get((str(condition_id), str(token)))
        if side in ("UP", "DOWN"):
            labelled[side] = labelled.get(side, 0.0) + float(cost)
    if set(labelled) == {"UP", "DOWN"}:
        return labelled["UP"], labelled["DOWN"]
    half = total / 2.0 if total else 0.0
    return half, half


def shadow_positions(registry: OrderRegistry, db_path: Path | str) -> dict[str, float]:
    """Positions as the shadow store knows them, shaped like `fetch_positions`.

    Passed to `auto_manage_pairs` explicitly so the pass never reaches for the
    Data API. That read fails the pass closed on any error, and failing closed
    on every cycle would keep the pairs pass from ever running in a rehearsal
    that has no funder to read a live position from.
    """
    with closing(get_connection(Path(db_path))) as conn:
        # A SELL never reaches this store today -- `record_submit` writes BUY
        # rows and a shadow exit writes no order at all -- but reading the side
        # rather than assuming it keeps the sum honest if that ever changes.
        rows = conn.execute(
            """
            SELECT o.condition_id AS condition_id, o.token_id AS token_id,
                   COALESCE(SUM(CASE WHEN o.side = 'SELL'
                                     THEN -f.size ELSE f.size END), 0) AS shares
            FROM orders o LEFT JOIN fills f ON f.order_uuid = o.id
            GROUP BY o.condition_id, o.token_id
            """
        ).fetchall()
        close_rows = conn.execute(
            """
            SELECT condition_id, method, shares, up_price
            FROM closes WHERE condition_id IS NOT NULL
            """
        ).fetchall()

    held: dict[tuple[str, str], float] = {
        (str(r["condition_id"]), str(r["token_id"])): float(r["shares"])
        for r in rows
    }

    # An exit records its leg by which price field is set, so it needs to know
    # which token is UP.
    side_of = _side_by_token(registry)

    for cr in close_rows:
        shares = float(cr["shares"] or 0.0)
        if shares <= 0:
            continue
        cid = str(cr["condition_id"])
        legs = [k for k in held if k[0] == cid]
        if cr["method"] in ("merge", "shadow_merge"):
            # Both legs leave together at a dollar a share.
            pass
        elif cr["method"] in ("single_buy_exit", "naked_exit"):
            want = "UP" if cr["up_price"] is not None else "DOWN"
            sold = [k for k in legs if side_of.get(k) == want]
            # An unlabelled leg cannot happen -- the exit path refuses to sell
            # a token the quotes ledger has no side for -- but if one ever
            # appears, taking the shares off BOTH legs under-reports the
            # position, and an under-reported position is what makes the
            # oversell pre-flight refuse rather than sell twice.
            legs = sold or legs
        else:
            continue
        for key in legs:
            held[key] = max(0.0, held[key] - shares)

    return {token: shares for (_cid, token), shares in held.items() if shares > 0}


def record_shadow_merges(
    registry: OrderRegistry,
    db_path: Path | str,
    *,
    now_fn: Callable[[], float] = time.time,
) -> list[str]:
    """Close every balanced pair at exactly $1.00 a share.

    The merge itself is a signed on-chain transaction and does not happen here:
    a shadow run has no key. What is recorded is its arithmetic, which is the
    part a rehearsal can honestly reproduce -- a merged pair pays $1.00, so the
    result is `shares * (1.00 - pair cost)`, and `method='shadow_merge'` says
    where the row came from.

    Tracks merged shares per pair rather than per-pair boolean, allowing
    multiple merges as a pair fills incrementally. If a pair filled 10/10
    shares and merged, then fills another 10/10, a second close records for
    the newly merged 10 shares.
    """
    with closing(get_connection(Path(db_path))) as conn:
        # Current fills per pair per leg
        rows = conn.execute(
            """
            SELECT o.pair_id AS pair_id, o.condition_id AS condition_id,
                   o.token_id AS token_id,
                   COALESCE(SUM(f.size), 0) AS shares,
                   COALESCE(SUM(f.size * f.price), 0) AS cost
            FROM orders o LEFT JOIN fills f ON f.order_uuid = o.id
            WHERE o.pair_id IS NOT NULL
            GROUP BY o.pair_id, o.token_id
            """
        ).fetchall()

        # Already-merged shares per pair: tx_hash is prefixed pair_id. One row
        # per close, so counting the rows of a pair also gives the version
        # number the next tx_hash needs -- read once here rather than with a
        # per-pair query inside the loop below.
        merged_rows = conn.execute(
            """
            SELECT tx_hash AS pair_tx, COALESCE(SUM(shares), 0) AS merged_shares
            FROM closes
            WHERE method = 'shadow_merge' AND tx_hash IS NOT NULL
            GROUP BY tx_hash
            """
        ).fetchall()

        # What each leg has already given up, per token. Written by this
        # function; see `ensure_shadow_tables` for why it cannot live in
        # `closes`.
        consumed_rows = conn.execute(
            """
            SELECT pair_id, token_id,
                   COALESCE(SUM(shares), 0) AS shares,
                   COALESCE(SUM(cost), 0) AS cost
            FROM shadow_merge_legs
            GROUP BY pair_id, token_id
            """
        ).fetchall()

    by_pair: dict[str, list] = {}
    for r in rows:
        by_pair.setdefault(str(r["pair_id"]), []).append(r)

    # Build maps from pair_id to already-merged shares and cost. tx_hash is formatted
    # as "pair-xxx" for the first merge, "pair-xxx:2" for the second, etc.
    already_merged: dict[str, float] = {}
    merge_counts: dict[str, int] = {}
    for r in merged_rows:
        tx = str(r["pair_tx"])
        # Extract pair_id from tx_hash (everything before the optional ":N" suffix)
        pair_id = tx.split(":")[0]
        already_merged[pair_id] = (already_merged.get(pair_id, 0.0)
                                   + float(r["merged_shares"]))
        merge_counts[pair_id] = merge_counts.get(pair_id, 0) + 1

    consumed: dict[tuple[str, str], tuple[float, float]] = {
        (str(r["pair_id"]), str(r["token_id"])): (float(r["shares"]), float(r["cost"]))
        for r in consumed_rows
    }

    merged: list[str] = []
    # Every per-leg row this call produces, written in one transaction after
    # the loop. `closes` is the authority on what merged; `shadow_merge_legs`
    # is a cost cache derived from it, and `log_close` commits on its own
    # connection, so the two cannot be made one transaction from here. What
    # keeps that safe is the backfill below: a cache row lost between the two
    # commits costs the pair one apportioned merge, not every merge after it.
    leg_writes: list[tuple[str, str, str, float, float]] = []
    # Which token is the UP leg, so each leg is charged its own cost below.
    # Read at most once per call, and only once a pair is actually being
    # closed: `get_all_quotes` is an unindexed scan of the whole quotes table,
    # this function runs every rotation next to `shadow_positions` which
    # already pays that scan, and most rotations close nothing. Paying it up
    # front would double a cost that grows for the length of the run in order
    # to answer a question no pair asked.
    side_of: dict[tuple[str, str], str] | None = None
    for pair_id, legs in by_pair.items():
        if len(legs) != 2:
            continue
        min_fills = min(float(leg["shares"]) for leg in legs)
        if min_fills <= 0:
            continue
        already_shares = already_merged.get(pair_id, 0.0)
        mergeable = min_fills - already_shares
        if mergeable <= SIZE_EPS:
            continue
        # Cost basis for the newly merged shares: each leg gives up `mergeable`
        # shares at the average price of the shares it still holds.
        #
        # What this replaced derived the figure by subtracting the cost earlier
        # closes charged from a recomputed cumulative target, where that target
        # scaled each leg's cost by `min_fills / that leg's shares`. A later
        # round of cheap fills on one leg could pull the target BELOW the
        # amount already charged, and the floor that stopped the difference
        # going negative then booked the whole $1.00 payout as profit -- on
        # shares bought with money. A remaining average cannot go negative, and
        # cannot reach zero while the leg still holds something that cost
        # something, so the floor is gone with the arithmetic that needed it.
        leg_costs: list[tuple[str, float]] = []
        for leg in legs:
            token = str(leg["token_id"])
            leg_shares = float(leg["shares"])
            gone_shares, gone_cost = consumed.get((pair_id, token), (0.0, 0.0))
            # `closes` says how many shares this pair has given up; the cache
            # says what they cost. Any shortfall between them is a cache row
            # that never landed -- a store older than the table, or a close
            # whose rows were lost after it committed. It can be the whole
            # history of the pair or one merge out of several, so the test is
            # the shortfall, not the absence.
            #
            # The missing shares are priced from what the leg has left, which
            # is the apportionment the older arithmetic used, and the result is
            # written back. That way the estimate is paid once instead of being
            # re-derived on every rotation for the rest of the run, and the
            # cached total returns to the authoritative one.
            missing = already_shares - gone_shares
            if missing > SIZE_EPS:
                unpriced_shares = leg_shares - gone_shares
                unpriced_cost = float(leg["cost"]) - gone_cost
                missing_cost = (unpriced_cost * (missing / unpriced_shares)
                                if unpriced_shares > SIZE_EPS else 0.0)
                gone_shares += missing
                gone_cost += missing_cost
                # Keyed by how many closes this pair already has, so a second
                # shortfall on a later rotation adds a row rather than
                # replacing the one that repaired the first.
                leg_writes.append(
                    (f"{pair_id}:backfill:{merge_counts.get(pair_id, 0)}",
                     pair_id, token, missing, missing_cost))
            remaining_shares = leg_shares - gone_shares
            remaining_cost = float(leg["cost"]) - gone_cost
            avg = (remaining_cost / remaining_shares
                   if remaining_shares > SIZE_EPS else 0.0)
            leg_costs.append((token, max(0.0, mergeable * avg)))
        cost_basis = sum(c for _token, c in leg_costs)
        if side_of is None:
            side_of = _side_by_token(registry)
        up_removed, dn_removed = _leg_cost_removed(
            leg_costs, side_of, str(legs[0]["condition_id"]))
        proceeds = mergeable * 1.0
        # Version the tx_hash to ensure uniqueness within (condition_id, tx_hash)
        # constraint: first merge uses pair_id as-is, second uses "pair_id:2", etc.
        merge_count = merge_counts.get(pair_id, 0)
        tx_hash = pair_id if merge_count == 0 else f"{pair_id}:{merge_count + 1}"
        registry.log_close(CloseRecord(
            ts=now_fn(), condition_id=str(legs[0]["condition_id"]),
            method="shadow_merge", shares=mergeable, cost_basis=cost_basis,
            proceeds=proceeds, fee=0.0, gas=0.0,
            realized_pnl=proceeds - cost_basis,
            # Cost has to leave the inventory with the shares, or the decision
            # keeps paying for a position it no longer holds -- and it has to
            # leave the leg that paid it. `_leg_cost_removed` charges each leg
            # its own cost, which is what the live merge records
            # (`core_brain/order_manager.py`, the `method="merge"` close:
            # `amt * up_unit_cost` / `amt * dn_unit_cost`). The two columns
            # already exist; the even split this replaced was a divergence from
            # live, not a limit of the schema.
            up_cost_removed=up_removed,
            dn_cost_removed=dn_removed,
            # The pair id rides in tx_hash, versioned to satisfy the unique
            # constraint on (condition_id, tx_hash). Multiple closes reference
            # the same logical pair by sharing a common prefix in tx_hash.
            tx_hash=tx_hash,
        ))
        # What each leg gave up, kept per token so the next merge on this pair
        # can price the shares still in it. `closes` cannot hold this: its
        # UP/DOWN split is even by design, matching what a live merge records.
        leg_writes.extend(
            (tx_hash, pair_id, token, mergeable, leg_cost)
            for token, leg_cost in leg_costs
        )
        merged.append(pair_id)

    if leg_writes:
        with closing(get_connection(Path(db_path))) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO shadow_merge_legs "
                "(tx_hash, pair_id, token_id, shares, cost) VALUES (?, ?, ?, ?, ?)",
                leg_writes,
            )
            conn.commit()

    return merged
