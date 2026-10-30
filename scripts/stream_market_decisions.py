"""Live single-market decision stream (read-only observer).

Streams the real-time decision inputs the bot acts on, formatted to 2 decimals,
for ONE pinned market. It mirrors what the engine computes each rotation but
places NO orders and holds NO signer -- it only reads the venue and the config,
so it is safe to run beside a shadow or live fleet.

Per cycle it prints:
  * the live book (bid / mid / ask)
  * recent tape prints + the measured 1h price range
  * the calculated dynamic offset
  * the resting quote it would place (UP + DOWN), on the whole-cent grid
  * the active position (if any) and the stop-loss trigger level

Usage:
  python scripts/stream_market_decisions.py --cid <condition_id> --interval 5
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests

from core_brain.config import MakerConfig
from core_brain.quotes import (
    Inventory, dynamic_offset_for, mid_price, quote_resting_price,
)
from core_brain.single_leg_lifecycle import stop_trigger

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "spread-hunter-stream"})
    return s


def _market_tokens(s, cid):
    r = s.get(f"{GAMMA}/markets", params={"condition_ids": [cid], "limit": 1}, timeout=20)
    rows = r.json()
    if isinstance(rows, dict):
        rows = rows.get("data") or []
    if not rows:
        return None
    m = rows[0]
    return {
        "question": m.get("question", ""),
        "tokens": json.loads(m.get("clobTokenIds") or "[]"),
        "end": m.get("endDate"),
    }


def _book(s, token):
    r = s.get(f"{CLOB}/book", params={"token_id": token}, timeout=12).json()
    bids = [(float(x["price"]), float(x["size"])) for x in r.get("bids", [])]
    asks = [(float(x["price"]), float(x["size"])) for x in r.get("asks", [])]
    return bids, asks


def _tape(s, cid, window_sec=3600, limit=500):
    r = s.get(f"{DATA}/trades", params={"market": cid, "limit": limit}, timeout=12).json()
    if not isinstance(r, list):
        return [], 0.0
    now = time.time()
    cutoff = now - window_sec
    prices, prints = [], []
    for t in r:
        if not isinstance(t, dict):
            continue
        try:
            ts = float(t.get("timestamp") or 0)
            price = float(t.get("price") or 0)
            size = float(t.get("size") or 0)
        except (TypeError, ValueError):
            continue
        if ts < cutoff or not (0 <= price <= 1):
            continue
        oi = t.get("outcomeIndex")
        ostr = str(t.get("outcome") or "").strip().lower()
        norm = (1.0 - price) if (oi == 1 or ostr in ("no", "down")) else price
        prices.append(norm)
        prints.append((ts, price, size))
    rng = round((max(prices) - min(prices)) * 100.0, 2) if prices else 0.0
    lo = round(min(prices), 4) if prices else None
    hi = round(max(prices), 4) if prices else None
    return {"prints": prints, "range_cents": rng, "lo": lo, "hi": hi,
            "count": len(prints)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cid", required=True, help="condition_id to observe")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--window", type=float, default=3600.0, help="range lookback sec")
    args = ap.parse_args()

    s = _session()
    meta = _market_tokens(s, args.cid)
    if not meta or len(meta["tokens"]) != 2:
        print(f"market {args.cid} not found or not binary")
        return
    up_tok, dn_tok = str(meta["tokens"][0]), str(meta["tokens"][1])
    print(f"OBSERVING (read-only, no orders): {meta['question']}")
    print(f"  cid={args.cid[:20]}...  ends={meta['end']}")
    print(f"  grid: whole cent · offset: dynamic range x0.5 · stop $0.04 flat")
    print("-" * 68)

    cycle = 0
    while True:
        cycle += 1
        ts = time.strftime("%H:%M:%S")
        try:
            up_bids, up_asks = _book(s, up_tok)
            dn_bids, dn_asks = _book(s, dn_tok)
            if not (up_bids and up_asks and dn_bids and dn_asks):
                print(f"[{ts}] cycle {cycle}: book not two-sided yet, waiting")
                time.sleep(args.interval)
                continue
            up_book = {"best_bid": max(p for p, _ in up_bids),
                       "best_ask": min(p for p, _ in up_asks)}
            dn_book = {"best_bid": max(p for p, _ in dn_bids),
                       "best_ask": min(p for p, _ in dn_asks)}
            up_mid = mid_price(up_book["best_bid"], up_book["best_ask"])
            dn_mid = mid_price(dn_book["best_bid"], dn_book["best_ask"])

            tape = _tape(s, args.cid, window_sec=args.window)
            rng = tape["range_cents"]
            cfg = MakerConfig(bot_quote_telemetry_range_cents=rng, bot_quote_telemetry_velocity_measured_at=time.time())
            dyn_off, dyn_tag = dynamic_offset_for(cfg)

            print(f"[{ts}] cycle {cycle}")
            print(f"  BOOK  up {up_book['best_bid']:.2f}/{up_mid:.2f}/{up_book['best_ask']:.2f}"
                  f"   dn {dn_book['best_bid']:.2f}/{dn_mid:.2f}/{dn_book['best_ask']:.2f}")
            # Full-window tape, not last-3: show the min/max print that actually
            # drove the range, so a fill credited on a trade-through is never a
            # "phantom" -- the print that caused it is on screen (directive
            # 2026-10-12). Count + last 5 prints for texture.
            recent = ", ".join(f"{p:.2f}x{int(sz)}"
                               for _, p, sz in tape["prints"][-5:])
            print(f"  TAPE  1h: {tape['count']} prints, range {rng:.2f}c "
                  f"[lo {tape['lo']:.2f} / hi {tape['hi']:.2f}]")
            print(f"        last5: {recent}")
            print(f"  OFFSET dynamic = {dyn_off*100:.2f}c ({dyn_tag})")
            inv = Inventory()
            up_q, *_ = quote_resting_price(cfg, inv, "UP", up_book)
            dn_q, *_ = quote_resting_price(cfg, inv, "DOWN", dn_book)
            print(f"  QUOTE up rest {up_q:.2f}   dn rest {dn_q:.2f}   "
                  f"(pair cost {up_q + dn_q:.2f})")
            # Would-fill transparency: flag if any print in the window traded at
            # or below our resting bids -- that is exactly what credit_fills
            # treats as a fill (trade-through), so it is shown explicitly.
            if tape["lo"] is not None:
                up_hit = "HIT" if tape["lo"] <= up_q + 1e-9 else "-"
                dn_hit = "HIT" if tape["lo"] <= dn_q + 1e-9 else "-"
                print(f"  FILL? up bid {up_q:.2f} {up_hit} / dn bid {dn_q:.2f} {dn_hit} "
                      f"(window lo {tape['lo']:.2f})")
            # Stop illustration off the UP resting quote. Entry-relative since
            # #619: the cut is derived from that fill's own average price, with
            # the emergency floor as the backstop, so a $0.10-$0.14 fill is no
            # longer cut the instant it fills.
            trig, trig_source = stop_trigger(
                up_q, cfg.bot_risk_stop_mode, cfg.bot_risk_stop_adverse_drop_usd, cfg.bot_risk_stop_max_drawdown_pct,
                cfg.bot_risk_circuit_max_drawdown_pct,
            )
            print(f"  STOP  if up fills {up_q:.2f}, naked-leg cut at bid <= {trig:.2f} "
                  f"({cfg.bot_risk_stop_mode} / {trig_source})")
            print("-" * 68)
        except Exception as e:  # noqa: BLE001 - observer never dies on one bad read
            print(f"[{ts}] cycle {cycle}: read error {type(e).__name__}: {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstream stopped")
