"""Detailed fill-by-fill comparison between tau=0 and tau=150ms."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy.config import load as load_cfg
from strategy.fills import QueueFillEngine
from strategy.quotes import Inventory
from scripts.measure_fill_rate import load_windows, Window, traded_between, targets_for


def analyze_fills():
    db_path = ROOT / "archive" / "20260729" / "books.db"
    raw_windows = load_windows(db_path)
    windows = [w for w in raw_windows if w.coverage >= 0.5]
    cfg = load_cfg()
    
    def run_replay(tau_p):
        eng = QueueFillEngine(net_oneway_ms=tau_p, cancel_venue_ack_ms=0.0, post_venue_accept_ms=0.0)
        inv = Inventory()
        fills_list = []
        open_orders = {}
        order_post_meta = {} # id(o) -> (posted_ts, poll_idx)
        poll_idx = 0
        
        for w_idx, w in enumerate(windows):
            prev_ts = None
            mid_hist = {"UP": [], "DOWN": []}
            for poll in w.polls:
                poll_idx += 1
                ts = poll.ts
                t_rem = w.end_ts - ts
                span = max(1e-9, w.end_ts - w.start_ts)
                frac = (ts - w.start_ts) / span

                for lbl, bk in (("UP", poll.up), ("DOWN", poll.dn)):
                    if bk["best_bid"] is not None and bk["best_ask"] is not None:
                        mid_hist[lbl].append((ts, (bk["best_bid"] + bk["best_ask"]) / 2.0))

                for lbl, bk in (("UP", poll.up), ("DOWN", poll.dn)):
                    tp = (traded_between(w, bk["token_id"], prev_ts, ts)
                          if prev_ts is not None else None)
                    observed = eng.on_book(bk["token_id"], bk["bids"], ts, tp)
                    for f in observed:
                        inv.fills += 1
                        # find order
                        for o in eng.orders:
                            if o.token_id == f.token_id and abs(o.price - f.price) < 1e-4 and o.side == f.side:
                                post_ts, p_idx = order_post_meta.get(id(o), (ts, poll_idx))
                                dt_poll = (ts - prev_ts) if prev_ts else 0.0
                                lbl_side = "UP" if f.side == "UP" else "DOWN"
                                last_mid = mid_hist[lbl_side][-1][1] if mid_hist[lbl_side] else f.price
                                mo_cents = (last_mid - f.price) * 100.0
                                fills_list.append({
                                    "window": w_idx,
                                    "token": f.token_id,
                                    "side": f.side,
                                    "price": f.price,
                                    "size": f.size,
                                    "ts": ts,
                                    "dt_poll_ms": dt_poll * 1000.0,
                                    "order_age_polls": poll_idx - p_idx,
                                    "time_since_post_ms": (ts - post_ts) * 1000.0,
                                    "last_mid": last_mid,
                                    "markout_cents_per_sh": mo_cents,
                                    "total_markout_usd": (last_mid - f.price) * f.size,
                                })
                                break

                prev_ts = ts
                if t_rem < cfg.min_t_remaining_sec:
                    eng.cancel(ts=ts, reason="halted")
                    continue

                want = targets_for(cfg, "strategy", poll.up, poll.dn, inv, t_rem, frac)
                for side in ("UP", "DOWN"):
                    tgt = want.get(side)
                    o = open_orders.get(side)
                    if tgt is not None:
                        tid, p, s = tgt
                        bk = poll.up if side == "UP" else poll.dn
                        if o is None or round(o.price, 4) != round(p, 4) or o.size != s:
                            if o is not None:
                                o.cancel(ts=ts, reason="requote")
                            new_o = eng.post(tid, side, p, s, bk["bids"], ts)
                            open_orders[side] = new_o
                            order_post_meta[id(new_o)] = (ts, poll_idx)
        return fills_list

    base_fills = run_replay(0.0)
    tau150_fills = run_replay(150.0)
    
    print(f"Base fills count:   {len(base_fills)}, total shares: {sum(f['size'] for f in base_fills):.1f}")
    print(f"Tau150 fills count: {len(tau150_fills)}, total shares: {sum(f['size'] for f in tau150_fills):.1f}\n")
    
    print("--- BASELINE (tau=0) ALL FILLS ---")
    for i, f in enumerate(base_fills):
        print(f"[{i:2d}] Win {f['window']} | {f['side']} p={f['price']:.2f} | size={f['size']:5.1f} sh | age={f['order_age_polls']} polls | dt_poll={f['dt_poll_ms']:5.0f}ms | mid={f['last_mid']:.2f} | MO={f['markout_cents_per_sh']:+6.2f}¢/sh (${f['total_markout_usd']:+6.2f})")

    print("\n--- TAU=150ms ALL FILLS ---")
    for i, f in enumerate(tau150_fills):
        print(f"[{i:2d}] Win {f['window']} | {f['side']} p={f['price']:.2f} | size={f['size']:5.1f} sh | age={f['order_age_polls']} polls | dt_poll={f['dt_poll_ms']:5.0f}ms | mid={f['last_mid']:.2f} | MO={f['markout_cents_per_sh']:+6.2f}¢/sh (${f['total_markout_usd']:+6.2f})")


if __name__ == "__main__":
    analyze_fills()
