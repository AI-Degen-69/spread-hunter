"""Record raw order books for the live BTC 5-min market to a standalone DB.

WHY A RECORDER AND NOT JUST INSTRUMENTING THE BOT
-------------------------------------------------
The open question is the real fill rate now that the phantom-fill bug is gone.
Measuring it by running the bot would answer it for exactly ONE quoting rule,
and every variant (rest at ask-1tick vs at the bid, chase the ask vs sit still,
price band on vs off) would need another hour of live data. Worse, the strategy
is about to change (price band + timing rule), so a bot-derived number would be
obsolete on arrival.

Raw books are strategy-independent. Record once, replay any rule offline,
compare rules on the SAME market data instead of on different hours. It also
means the fill-rate claim is reproducible: the input is on disk, not gone.

Writes to books.db, never to hunter.db. Does not import strategy.store, so it
cannot touch the bot's data even by accident.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional, Dict

import requests
import websockets

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy.markets import fetch_live_market, parse_book, LiveMarket  # noqa: E402
from strategy.net_config import load_net                                # noqa: E402

log = logging.getLogger("recorder")

WS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,           -- wall clock of the event/poll
    condition_id TEXT NOT NULL,
    market_slug TEXT,
    start_ts REAL,              -- window open
    end_ts REAL,                -- window close
    token_id TEXT NOT NULL,
    side TEXT NOT NULL,         -- UP | DOWN (which outcome this token is)
    bids TEXT NOT NULL,         -- json {price: size}
    asks TEXT NOT NULL,
    ts_request_sent REAL,       -- request/event dispatch ts
    ts_response_recv REAL,      -- response arrival ts
    ts_venue REAL,              -- venue-supplied timestamp
    is_rollover INTEGER DEFAULT 0 -- 1 if during rollover/init phase, 0 steady state
);
CREATE INDEX IF NOT EXISTS idx_s_cond ON snapshots(condition_id, ts);
CREATE INDEX IF NOT EXISTS idx_s_ts ON snapshots(ts);

CREATE TABLE IF NOT EXISTS windows (
    condition_id TEXT PRIMARY KEY,
    market_slug TEXT,
    start_ts REAL,
    end_ts REAL,
    up_token TEXT,
    down_token TEXT,
    first_seen REAL,
    last_seen REAL,
    polls INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ntp_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    server TEXT NOT NULL,
    offset_ms REAL NOT NULL,
    rtt_ms REAL NOT NULL
);
"""


def sample_ntp(server: str = "time.cloudflare.com", timeout: float = 2.0) -> Optional[tuple[float, float]]:
    """Sample NTP offset and RTT via RFC 4330 RTT-compensated formula:
    offset = ((T2 - T1) + (T3 - T4)) / 2
    rtt = (T4 - T1) - (T3 - T2)
    """
    import socket
    import struct
    NTP_DELTA = 2208988800
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.settimeout(timeout)
    data = bytearray(48)
    data[0] = 0x1B
    try:
        t0 = time.time()  # T1
        client.sendto(data, (server, 123))
        msg, _ = client.recvfrom(1024)
        t3 = time.time()  # T4
        recv_s, recv_f = struct.unpack("!II", msg[32:40])
        trans_s, trans_f = struct.unpack("!II", msg[40:48])
        t1 = (recv_s - NTP_DELTA) + (recv_f / 2**32)  # T2
        t2 = (trans_s - NTP_DELTA) + (trans_f / 2**32)  # T3
        rtt_ms = ((t3 - t0) - (t2 - t1)) * 1000.0
        offset_ms = (((t1 - t0) + (t2 - t3)) / 2.0) * 1000.0
        return offset_ms, rtt_ms
    except Exception:
        return None
    finally:
        client.close()


def db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(snapshots)")
    cols = {row[1] for row in cur.fetchall()}
    for col, ctype in (("ts_request_sent", "REAL"),
                       ("ts_response_recv", "REAL"),
                       ("ts_venue", "REAL"),
                       ("is_rollover", "INTEGER DEFAULT 0")):
        if col not in cols:
            conn.execute(f"ALTER TABLE snapshots ADD COLUMN {col} {ctype}")
    conn.commit()
    return conn


def book(clob_host: str, token_id: str) -> tuple[dict, float, float]:
    url = f"{clob_host}/book"
    t_sent = time.time()
    r = _SESSION.get(url, params={"token_id": token_id}, timeout=MARKET_TIMEOUT)
    t_recv = time.time()
    r.raise_for_status()
    return parse_book(r.json(), token_id), t_sent, t_recv


class WSMarketRecorder:
    def __init__(self, out: Path, interval: float, minutes: float):
        self.out = out
        self.interval = interval
        self.minutes = minutes
        self.conn = db(out)
        self.net = load_net()
        self.deadline = time.time() + minutes * 60 if minutes > 0 else float("inf")
        self.books: Dict[str, Dict[str, Dict[float, float]]] = {}
        self.market: Optional[LiveMarket] = None
        self.polls = 0
        self.windows = set()
        self.write_queue: asyncio.Queue = asyncio.Queue()
        self.current_window_opened_at = 0.0
        self.latest_ntp_offset_ms = -541.31  # fallback default

    def parse_initial_book(self, token_data: dict) -> tuple[dict[float, float], dict[float, float]]:
        bids, asks = {}, {}
        for b in token_data.get("bids", []):
            p, s = float(b["price"]), float(b["size"])
            if s > 1e-9:
                bids[round(p, 4)] = s
        for a in token_data.get("asks", []):
            p, s = float(a["price"]), float(a["size"])
            if s > 1e-9:
                asks[round(p, 4)] = s
        return bids, asks

    def apply_price_change(self, change: dict):
        asset_id = str(change["asset_id"])
        price = round(float(change["price"]), 4)
        size = float(change["size"])
        side = change.get("side", "").upper()
        if asset_id not in self.books:
            self.books[asset_id] = {"bids": {}, "asks": {}}
        target = self.books[asset_id]["bids"] if side == "BUY" else self.books[asset_id]["asks"]
        if size <= 1e-9:
            target.pop(price, None)
        else:
            target[price] = size

    def queue_snapshot(self, t_sent: float, t_recv: float, t_venue: Optional[float]):
        if not self.market:
            return
        m = self.market
        ts = t_recv
        up_bids = self.books.get(m.up_token, {}).get("bids", {})
        up_asks = self.books.get(m.up_token, {}).get("asks", {})
        dn_bids = self.books.get(m.down_token, {}).get("bids", {})
        dn_asks = self.books.get(m.down_token, {}).get("asks", {})
        is_rollover = 1 if (ts - self.current_window_opened_at) < 5.0 else 0

        rows = [
            (ts, m.condition_id, m.market_slug, m.start_ts, m.end_ts,
             m.up_token, "UP", json.dumps(up_bids), json.dumps(up_asks),
             t_sent, t_recv, t_venue, is_rollover),
            (ts, m.condition_id, m.market_slug, m.start_ts, m.end_ts,
             m.down_token, "DOWN", json.dumps(dn_bids), json.dumps(dn_asks),
             t_sent, t_recv, t_venue, is_rollover),
        ]
        win_row = (m.condition_id, m.market_slug, m.start_ts, m.end_ts,
                   m.up_token, m.down_token, ts, ts)
        self.write_queue.put_nowait((rows, win_row))
        self.polls += 1
        self.windows.add(m.condition_id)
        if self.polls % 500 == 0:
            log.info("%d events/snapshots queued | %d windows", self.polls, len(self.windows))

    async def db_writer_loop(self):
        batch = []
        win_updates = {}
        last_flush = time.time()
        while True:
            try:
                try:
                    item = await asyncio.wait_for(self.write_queue.get(), timeout=0.2)
                    rows, win_row = item
                    batch.extend(rows)
                    win_updates[win_row[0]] = win_row
                    self.write_queue.task_done()
                except asyncio.TimeoutError:
                    pass

                now = time.time()
                if batch and ((now - last_flush) >= 0.5 or len(batch) >= 200):
                    self.conn.executemany(
                        "INSERT INTO snapshots (ts, condition_id, market_slug, start_ts, "
                        "end_ts, token_id, side, bids, asks, ts_request_sent, ts_response_recv, "
                        "ts_venue, is_rollover) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
                    for w in win_updates.values():
                        self.conn.execute(
                            "INSERT INTO windows (condition_id, market_slug, start_ts, end_ts, "
                            "up_token, down_token, first_seen, last_seen, polls) "
                            "VALUES (?,?,?,?,?,?,?,?,1) "
                            "ON CONFLICT(condition_id) DO UPDATE SET last_seen=excluded.last_seen, "
                            "polls = polls + 1", w)
                    self.conn.commit()
                    batch.clear()
                    win_updates.clear()
                    last_flush = now
            except asyncio.CancelledError:
                if batch:
                    self.conn.executemany(
                        "INSERT INTO snapshots (ts, condition_id, market_slug, start_ts, "
                        "end_ts, token_id, side, bids, asks, ts_request_sent, ts_response_recv, "
                        "ts_venue, is_rollover) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
                    self.conn.commit()
                break
            except Exception as e:
                log.error("DB writer error: %s", e)

    async def ntp_sampler_loop(self):
        servers = ["time.cloudflare.com", "time.google.com", "pool.ntp.org"]
        while True:
            for s in servers:
                res = sample_ntp(s)
                if res:
                    offset_ms, rtt_ms = res
                    self.latest_ntp_offset_ms = offset_ms
                    ts = time.time()
                    try:
                        self.conn.execute(
                            "INSERT INTO ntp_samples (ts, server, offset_ms, rtt_ms) VALUES (?,?,?,?)",
                            (ts, s, offset_ms, rtt_ms))
                        self.conn.commit()
                    except Exception:
                        pass
                    break
            await asyncio.sleep(60.0)

    async def run_ws(self):
        log.info("Starting non-blocking WS stream recording to %s for %s",
                 self.out, f"{self.minutes:.0f}m" if self.minutes > 0 else "ever")
        last_market_fetch = 0.0

        writer_task = asyncio.create_task(self.db_writer_loop())
        ntp_task = asyncio.create_task(self.ntp_sampler_loop())

        try:
            while time.time() < self.deadline:
                now = time.time()
                if self.market is None or now >= self.market.end_ts or (now - last_market_fetch) > 60:
                    try:
                        m = fetch_live_market(self.net.gamma_host, self.net.series_slug)
                        last_market_fetch = now
                        if m and (self.market is None or m.condition_id != self.market.condition_id):
                            log.info("window %s  t_remaining=%.0fs", m.market_slug, m.end_ts - now)
                            self.current_window_opened_at = now
                        self.market = m
                    except Exception as e:
                        log.warning("market fetch failed: %s", e)

                if self.market is None:
                    await asyncio.sleep(1.0)
                    continue

                m = self.market
                try:
                    t0 = time.time()
                    async with websockets.connect(WS_MARKET_URL, ping_interval=20, ping_timeout=20) as ws:
                        t1 = time.time()
                        log.info("Connected to CLOB WS (handshake: %.1fms)", (t1 - t0) * 1000)
                        sub = {"type": "market", "assets_ids": [m.up_token, m.down_token]}
                        t_sub_sent = time.time()
                        await ws.send(json.dumps(sub))

                        while time.time() < self.deadline and time.time() < m.end_ts:
                            t_sent = time.time()
                            msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
                            t_recv = time.time()
                            data = json.loads(msg)
                            t_venue = None

                            if isinstance(data, list):
                                for item in data:
                                    asset_id = str(item.get("asset_id"))
                                    bids, asks = self.parse_initial_book(item)
                                    self.books[asset_id] = {"bids": bids, "asks": asks}
                                    if item.get("timestamp"):
                                        t_venue = float(item["timestamp"]) / 1000.0
                                self.queue_snapshot(t_sub_sent, t_recv, t_venue)
                            elif isinstance(data, dict):
                                if "timestamp" in data:
                                    t_venue = float(data["timestamp"]) / 1000.0
                                changes = data.get("price_changes", [])
                                if not changes and "price" in data:
                                    changes = [data]
                                for ch in changes:
                                    self.apply_price_change(ch)
                                self.queue_snapshot(t_sent, t_recv, t_venue)

                except Exception as e:
                    log.warning("WS stream dropped (%s), reconnecting in 0.5s...", e)
                    await asyncio.sleep(0.5)
        finally:
            writer_task.cancel()
            ntp_task.cancel()
            await asyncio.gather(writer_task, ntp_task, return_exceptions=True)

        log.info("done: %d events over %d windows", self.polls, len(self.windows))


def run_rest(out: Path, interval: float, minutes: float) -> None:
    net = load_net()
    conn = db(out)
    deadline = time.time() + minutes * 60 if minutes > 0 else float("inf")
    market = None
    last_market_fetch = 0.0
    polls = 0
    windows = set()

    log.info("recording via REST to %s | interval %.1fs | for %s",
             out, interval, f"{minutes:.0f}m" if minutes > 0 else "ever")

    while time.time() < deadline:
        now = time.time()
        if market is None or now >= market.end_ts or (now - last_market_fetch) > 60:
            try:
                m = fetch_live_market(net.gamma_host, net.series_slug)
                last_market_fetch = now
                if m and (market is None or m.condition_id != market.condition_id):
                    log.info("window %s  t_remaining=%.0fs", m.market_slug,
                             m.end_ts - now)
                market = m
            except Exception as e:
                log.warning("market fetch failed: %s", e)
        if market is None:
            time.sleep(1.0)
            continue

        try:
            up, t_up_sent, t_up_recv = book(net.clob_host, market.up_token)
            dn, t_dn_sent, t_dn_recv = book(net.clob_host, market.down_token)
        except Exception as e:
            log.warning("book fetch failed: %s", e)
            time.sleep(interval)
            continue

        ts = time.time()
        rows = [
            (ts, market.condition_id, market.market_slug, market.start_ts,
             market.end_ts, market.up_token, "UP",
             json.dumps(up["bids"]), json.dumps(up["asks"]),
             t_up_sent, t_up_recv, None),
            (ts, market.condition_id, market.market_slug, market.start_ts,
             market.end_ts, market.down_token, "DOWN",
             json.dumps(dn["bids"]), json.dumps(dn["asks"]),
             t_dn_sent, t_dn_recv, None),
        ]
        conn.executemany(
            "INSERT INTO snapshots (ts, condition_id, market_slug, start_ts, "
            "end_ts, token_id, side, bids, asks, ts_request_sent, ts_response_recv, ts_venue) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.execute(
            "INSERT INTO windows (condition_id, market_slug, start_ts, end_ts, "
            "up_token, down_token, first_seen, last_seen, polls) "
            "VALUES (?,?,?,?,?,?,?,?,1) "
            "ON CONFLICT(condition_id) DO UPDATE SET last_seen=excluded.last_seen, "
            "polls = polls + 1",
            (market.condition_id, market.market_slug, market.start_ts,
             market.end_ts, market.up_token, market.down_token, ts, ts))
        conn.commit()

        polls += 1
        windows.add(market.condition_id)
        if polls % 60 == 0:
            log.info("%d polls | %d windows", polls, len(windows))

        time.sleep(max(0.0, interval - (time.time() - ts)))

    log.info("done: %d polls over %d windows", polls, len(windows))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(ROOT / "books.db"))
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--minutes", type=float, default=0.0, help="0 = run forever")
    p.add_argument("--rest", action="store_true", help="Force REST polling mode instead of WebSocket")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    try:
        if a.rest:
            run_rest(Path(a.out), a.interval, a.minutes)
        else:
            rec = WSMarketRecorder(Path(a.out), a.interval, a.minutes)
            asyncio.run(rec.run_ws())
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
