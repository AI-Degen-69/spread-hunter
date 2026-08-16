"""strategy/order_registry.py - Live order and fill registry backed by SQLite (run/live.db).

Stage 2 Architecture Constraints:
- Stored separately in run/live.db, never in run/fleet.db (simulator state).
- orders.id is a local uuid4 string written BEFORE submitting to the venue.
- orders.order_id is the venue order id, unique and nullable, attached after POST response.
- size_matched is strictly derived from SUM(fills.size), never stored as a mutable column in orders.
- PRAGMA foreign_keys=ON on every connection to enforce fills.order_uuid -> orders.id.
- PRAGMA journal_mode=WAL for non-blocking concurrent reads during poll writes.
- Timestamps are integer epoch milliseconds (UTC).
- Atomic fail-closed transaction boundaries.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

DEFAULT_DB_PATH = Path("run/live.db")
BUSY_TIMEOUT_SEC = 5.0

# 30 seconds match window: covers HTTP roundtrip and CLOB ingestion skew
# while preventing false-positive matches against sequential order updates.
DEFAULT_ORPHAN_MATCH_WINDOW_MS: int = 30_000

# Sizes are REAL, so size_matched accumulates float error across partial fills.
# Never compare it to original_size with == or >=: forty fills summing to 100.0
# in decimal can land at 99.99999999999999 in binary, and an order that is full
# would never be marked filled. Compare with this epsilon instead. Polymarket
# sizes carry at most six decimal places, so 1e-9 sits far below the smallest
# real difference and far above the accumulated representation error.
SIZE_EPS: float = 1e-9

# Every status a row may hold, enforced by a CHECK constraint in the schema.
# Without the constraint a typo inserts cleanly and the row becomes invisible to
# get_active_orders: a resting order, real money, and nothing tracking it.
ORDER_STATUSES = ("pending", "open", "partial", "filled", "cancelled", "unattributed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    order_id TEXT UNIQUE,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    price REAL NOT NULL,
    original_size REAL NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'open', 'partial', 'filled', 'cancelled', 'unattributed')
    ),
    posted_ts INTEGER NOT NULL,
    last_polled_ts INTEGER NOT NULL,
    pair_id TEXT,
    max_pair_cost_at_post REAL
);

CREATE TABLE IF NOT EXISTS fills (
    trade_id TEXT PRIMARY KEY,
    order_uuid TEXT NOT NULL,
    size REAL NOT NULL,
    price REAL NOT NULL,
    venue_ts INTEGER NOT NULL,
    FOREIGN KEY (order_uuid) REFERENCES orders(id)
);

CREATE INDEX IF NOT EXISTS idx_orders_order_id ON orders(order_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_pair_id ON orders(pair_id);
CREATE INDEX IF NOT EXISTS idx_fills_order_uuid ON fills(order_uuid);

CREATE VIEW IF NOT EXISTS order_summary AS
SELECT
    o.id,
    o.order_id,
    o.condition_id,
    o.token_id,
    o.side,
    o.price,
    o.original_size,
    o.status,
    o.posted_ts,
    o.last_polled_ts,
    o.pair_id,
    o.max_pair_cost_at_post,
    COALESCE(SUM(f.size), 0.0) AS size_matched
FROM orders o
LEFT JOIN fills f ON f.order_uuid = o.id
GROUP BY o.id;
"""

_schema_ready: dict[str, tuple[int, int, int]] = {}
_lock = threading.RLock()


def get_connection(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Return a configured sqlite3.Connection with WAL and foreign_keys=ON."""
    path = str(Path(db_path))
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SEC)
    conn.row_factory = sqlite3.Row
    # Foreign keys must be enabled per-connection on every connection
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        st = os.stat(path)
        file_id = (st.st_ino, st.st_ctime_ns, st.st_mtime_ns)
    except OSError:
        file_id = (0, 0, 0)

    if _schema_ready.get(path) != file_id:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        conn.executescript(SCHEMA)
        conn.commit()
        _schema_ready[path] = file_id

    return conn


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """Initialize schema for the database file."""
    with _lock:
        conn = get_connection(db_path)
        conn.close()


@dataclass(frozen=True)
class OrderRecord:
    id: str
    condition_id: str
    token_id: str
    side: str
    price: float
    original_size: float
    status: str
    posted_ts: int
    last_polled_ts: int
    order_id: Optional[str] = None
    pair_id: Optional[str] = None
    max_pair_cost_at_post: Optional[float] = None


@dataclass(frozen=True)
class FillRecord:
    trade_id: str
    order_uuid: str
    size: float
    price: float
    venue_ts: int


class OrderRegistry:
    """Thread-safe SQLite-backed registry for live orders and fills."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        init_db(self.db_path)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with _lock:
            conn = get_connection(self.db_path)
            try:
                yield conn
            except BaseException:
                # Roll back explicitly rather than relying on close() to discard
                # the open transaction. A write path that fails must leave no
                # partial state, and that must be true by construction, not by
                # a side effect of the driver's teardown.
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                raise
            finally:
                conn.close()

    def create_order(self, order: OrderRecord) -> None:
        """Insert a new order row before sending to venue."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO orders (
                    id, order_id, condition_id, token_id, side, price,
                    original_size, status, posted_ts, last_polled_ts,
                    pair_id, max_pair_cost_at_post
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.id,
                    order.order_id,
                    order.condition_id,
                    order.token_id,
                    order.side,
                    order.price,
                    order.original_size,
                    order.status,
                    order.posted_ts,
                    order.last_polled_ts,
                    order.pair_id,
                    order.max_pair_cost_at_post,
                ),
            )
            conn.commit()

    def attach_venue_order_id(
        self,
        local_id: str,
        venue_order_id: str,
        status: str = "open",
        last_polled_ts: Optional[int] = None,
    ) -> None:
        """Attach venue order ID to a pending order upon receiving response.

        Raises `KeyError` when no row matches `local_id`. A silent zero-row
        UPDATE is the worst available outcome here: the venue has accepted an
        order, we hold its id, and nothing in the registry references it. That
        order rests until it fills, invisible to every reconcile pass. Fail
        closed so the caller can record it as unattributed instead.
        """
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if last_polled_ts is not None:
                cur = conn.execute(
                    """
                    UPDATE orders
                    SET order_id = ?, status = ?, last_polled_ts = ?
                    WHERE id = ?
                    """,
                    (venue_order_id, status, last_polled_ts, local_id),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE orders
                    SET order_id = ?, status = ?
                    WHERE id = ?
                    """,
                    (venue_order_id, status, local_id),
                )
            if cur.rowcount != 1:
                conn.rollback()
                raise KeyError(
                    f"attach_venue_order_id: no order row {local_id!r} for venue "
                    f"order {venue_order_id!r}; {cur.rowcount} rows matched"
                )
            conn.commit()

    def get_order(self, local_id: str) -> Optional[OrderRecord]:
        """Fetch order record by local uuid."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE id = ?",
                (local_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_order(row)

    def get_order_by_venue_id(self, venue_order_id: str) -> Optional[OrderRecord]:
        """Fetch order record by venue order_id."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE order_id = ?",
                (venue_order_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_order(row)

    def get_size_matched(self, order_uuid: str) -> float:
        """Derive size_matched from SUM(size) over fills."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(size), 0.0) AS total FROM fills WHERE order_uuid = ?",
                (order_uuid,),
            ).fetchone()
            return float(row["total"]) if row else 0.0

    def record_fill(self, fill: FillRecord) -> bool:
        """Record a fill idempotently. True if inserted, False if already present.

        A duplicate `trade_id` is the normal case, not an error. The reconcile
        loop re-reads trades with a deliberate 60-second overlap because our
        clock and the venue's differ, so every poll after the first re-presents
        trades already recorded. Raising here would kill the poll loop on its
        second cycle. Deduping on the venue's own `trade_id` is what makes the
        overlap safe, and that is the whole design.

        A fill referencing an unknown order still raises: that is a foreign key
        violation, it means an orphan the reconciler failed to adopt, and it
        must fail closed rather than sum into `size_matched` for no order.
        """
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            already = conn.execute(
                "SELECT 1 FROM fills WHERE trade_id = ?",
                (fill.trade_id,),
            ).fetchone()
            if already is not None:
                conn.rollback()
                return False
            conn.execute(
                """
                INSERT INTO fills (trade_id, order_uuid, size, price, venue_ts)
                VALUES (?, ?, ?, ?, ?)
                """,
                (fill.trade_id, fill.order_uuid, fill.size, fill.price, fill.venue_ts),
            )
            conn.commit()
            return True

    def update_order_status(
        self, local_id: str, status: str, last_polled_ts: int
    ) -> None:
        """Update order status and last_polled_ts."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE orders
                SET status = ?, last_polled_ts = ?
                WHERE id = ?
                """,
                (status, last_polled_ts, local_id),
            )
            conn.commit()

    def get_active_orders(self) -> list[OrderRecord]:
        """Return all orders in pending, open, or partial status."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM orders WHERE status IN ('pending', 'open', 'partial') ORDER BY posted_ts ASC"
            ).fetchall()
            return [self._row_to_order(r) for r in rows]

    @staticmethod
    def _row_to_order(row: sqlite3.Row) -> OrderRecord:
        return OrderRecord(
            id=row["id"],
            order_id=row["order_id"],
            condition_id=row["condition_id"],
            token_id=row["token_id"],
            side=row["side"],
            price=float(row["price"]),
            original_size=float(row["original_size"]),
            status=row["status"],
            posted_ts=int(row["posted_ts"]),
            last_polled_ts=int(row["last_polled_ts"]),
            pair_id=row["pair_id"],
            max_pair_cost_at_post=(
                float(row["max_pair_cost_at_post"])
                if row["max_pair_cost_at_post"] is not None
                else None
            ),
        )


@dataclass
class ReconcileSummary:
    polled_ts: int = 0
    open_orders_count: int = 0
    trades_polled: int = 0
    fills_recorded: int = 0
    duplicates_ignored: int = 0
    orders_filled: int = 0
    orders_partially_filled: int = 0
    orders_cancelled: int = 0
    orphans_adopted: int = 0
    unattributed_recorded: int = 0
    transitions: list[str] = None

    def __post_init__(self):
        if self.transitions is None:
            self.transitions = []


def compute_backoff_delay(
    err_count: int, base_sec: float = 2.0, max_sec: float = 60.0
) -> float:
    """Compute exponential backoff delay capped at max_sec."""
    if err_count <= 0:
        return 0.0
    delay = base_sec * (2 ** (err_count - 1))
    return min(delay, max_sec)


def reconcile_orders(
    client,
    registry: OrderRegistry,
    maker_address: Optional[str] = None,
    current_ts_ms: Optional[int] = None,
    orphan_window_ms: int = DEFAULT_ORPHAN_MATCH_WINDOW_MS,
) -> ReconcileSummary:
    """Reconcile registry state against venue open orders and trades."""
    import time
    import uuid

    now_ms = current_ts_ms if current_ts_ms is not None else int(time.time() * 1000)
    summary = ReconcileSummary(polled_ts=now_ms)

    # 1. Fetch open orders from venue
    try:
        from py_clob_client_v2.clob_types import OpenOrderParams
        venue_open_orders_raw = client.get_open_orders()
    except Exception:
        venue_open_orders_raw = client.get_open_orders()

    summary.open_orders_count = len(venue_open_orders_raw) if venue_open_orders_raw else 0
    venue_order_map: dict[str, dict] = {}
    if venue_open_orders_raw:
        for item in venue_open_orders_raw:
            v_id = str(item.get("id") or item.get("order_id") or "")
            if v_id:
                venue_order_map[v_id] = item

    # 2. Orphan adoption: check venue open orders not currently in registry
    active_orders = registry.get_active_orders()
    for v_id, item in venue_order_map.items():
        existing = registry.get_order_by_venue_id(v_id)
        if existing is None:
            v_token = str(item.get("asset_id") or item.get("token_id") or "")
            v_price = float(item.get("price", 0.0))
            v_size = float(item.get("size") or item.get("original_size") or 0.0)
            v_ts_raw = item.get("timestamp") or item.get("created_at") or item.get("posted_ts")
            v_ts = int(v_ts_raw) if v_ts_raw is not None else None
            if v_ts is not None and v_ts < 10_000_000_000:
                v_ts *= 1000

            matched_pending = None
            for pending in active_orders:
                if pending.status == "pending":
                    token_match = (not v_token) or (pending.token_id == v_token)
                    price_match = abs(pending.price - v_price) <= 1e-6
                    size_match = abs(pending.original_size - v_size) <= SIZE_EPS
                    ts_match = True
                    if v_ts is not None:
                        ts_match = abs(v_ts - pending.posted_ts) <= orphan_window_ms
                    if token_match and price_match and size_match and ts_match:
                        matched_pending = pending
                        break

            if matched_pending is not None:
                try:
                    registry.attach_venue_order_id(
                        matched_pending.id, v_id, status="open", last_polled_ts=now_ms
                    )
                    summary.orphans_adopted += 1
                    summary.transitions.append(f"ADOPT {matched_pending.id[:8]} -> {v_id}")
                except KeyError:
                    unattr_order = OrderRecord(
                        id=str(uuid.uuid4()),
                        order_id=v_id,
                        condition_id=str(item.get("market") or item.get("condition_id") or "unknown"),
                        token_id=v_token or "unknown",
                        side=str(item.get("side", "BUY")).upper(),
                        price=v_price,
                        original_size=v_size,
                        status="unattributed",
                        posted_ts=now_ms,
                        last_polled_ts=now_ms,
                    )
                    registry.create_order(unattr_order)
                    summary.unattributed_recorded += 1
                    summary.transitions.append(f"UNATTRIBUTED {v_id}")
            else:
                unattr_order = OrderRecord(
                    id=str(uuid.uuid4()),
                    order_id=v_id,
                    condition_id=str(item.get("market") or item.get("condition_id") or "unknown"),
                    token_id=v_token or "unknown",
                    side=str(item.get("side", "BUY")).upper(),
                    price=v_price,
                    original_size=v_size,
                    status="unattributed",
                    posted_ts=now_ms,
                    last_polled_ts=now_ms,
                )
                registry.create_order(unattr_order)
                summary.unattributed_recorded += 1
                summary.transitions.append(f"UNATTRIBUTED {v_id}")

    # 3. Poll trades with 60s overlap
    earliest_polled_ts = now_ms - 60_000
    current_active = registry.get_active_orders()
    if current_active:
        min_active_ts = min(o.last_polled_ts for o in current_active)
        earliest_polled_ts = min(earliest_polled_ts, min_active_ts)

    after_sec = max(0, int((earliest_polled_ts - 60_000) / 1000))
    trades_raw = []
    try:
        from py_clob_client_v2.clob_types import TradeParams
        p = TradeParams(maker_address=maker_address, after=after_sec)
        trades_raw = client.get_trades(params=p)
    except TypeError:
        trades_raw = client.get_trades()
    except Exception:
        try:
            trades_raw = client.get_trades()
        except Exception:
            trades_raw = []

    summary.trades_polled = len(trades_raw) if trades_raw else 0
    if trades_raw:
        for t in trades_raw:
            t_id = str(t.get("id") or t.get("trade_id") or "")
            t_order_id = str(t.get("order_id") or t.get("maker_order_id") or "")
            t_size = float(t.get("size", 0.0))
            t_price = float(t.get("price", 0.0))
            t_ts_raw = t.get("timestamp") or t.get("venue_ts") or t.get("created_at")
            t_ts = int(t_ts_raw) if t_ts_raw is not None else now_ms
            if t_ts < 10_000_000_000:
                t_ts *= 1000

            order = None
            if t_order_id:
                order = registry.get_order_by_venue_id(t_order_id)
                if order is None:
                    order = registry.get_order(t_order_id)
            if order is not None:
                fill_rec = FillRecord(
                    trade_id=t_id,
                    order_uuid=order.id,
                    size=t_size,
                    price=t_price,
                    venue_ts=t_ts,
                )
                if registry.record_fill(fill_rec):
                    summary.fills_recorded += 1
                    summary.transitions.append(f"FILL {order.id[:8]} ({order.order_id}): +{t_size} @ {t_price}")
                else:
                    summary.duplicates_ignored += 1

    # 4. Update order statuses for all active orders
    updated_active = registry.get_active_orders()
    for order in updated_active:
        size_matched = registry.get_size_matched(order.id)
        is_resting = bool(order.order_id and order.order_id in venue_order_map)
        is_full = (size_matched >= order.original_size - SIZE_EPS)

        if is_full:
            new_status = "filled"
            summary.orders_filled += 1
        elif is_resting:
            new_status = "partial" if size_matched > SIZE_EPS else "open"
            if new_status == "partial" and order.status != "partial":
                summary.orders_partially_filled += 1
        else:
            # Absent from open orders and not full
            # Only mark cancelled if it had an order_id on venue or was open/partial
            if order.status != "pending":
                new_status = "cancelled"
                summary.orders_cancelled += 1
            else:
                new_status = order.status

        if new_status != order.status:
            registry.update_order_status(order.id, new_status, now_ms)
            summary.transitions.append(f"STATUS {order.id[:8]} ({order.order_id or 'local'}): {order.status} -> {new_status}")
        else:
            registry.update_order_status(order.id, order.status, now_ms)

    return summary


