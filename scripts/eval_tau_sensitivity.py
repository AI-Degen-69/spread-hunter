"""Evaluates tau_cancel sensitivity across [0, 150, 250, 500] ms on recorded books."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy.config import load as load_cfg
from strategy.fills import QueueFillEngine
from strategy.quotes import Inventory
from scripts.measure_fill_rate import load_windows, Window, traded_between, targets_for


def replay_with_tau(w: Window, cfg, tau_ms: float) -> dict:
    net_oneway = tau_ms / 2.0
    venue_ack = tau_ms / 2.0
    engine = QueueFillEngine(cancel_net_oneway_ms=net_oneway, cancel_venue_ack_ms=venue_ack)
    inv = Inventory()
    
    posted_shares = 0.0
    queue_fills = []
    race_fills = []
    
    prev_ts = None
    open_orders = {}
    mid_hist = {"UP": [], "DOWN": []}
    
    for poll in w.polls:
        ts = poll.ts
        t_rem = w.end_ts - ts
        span = max(1e-9, w.end_ts - w.start_ts)
        frac = (ts - w.start_ts) / span

        for lbl, bk in (("UP", poll.up), ("DOWN", poll.dn)):
            if bk["best_bid"] is not None and bk["best_ask"] is not None:
                mid_hist[lbl].append((ts, (bk["best_bid"] + bk["best_ask"]) / 2.0))

        # 1. on_book
        for lbl, bk in (("UP", poll.up), ("DOWN", poll.dn)):
            tp = (traded_between(w, bk["token_id"], prev_ts, ts)
                  if prev_ts is not None else None)
            observed = engine.on_book(bk["token_id"], bk["bids"], ts, tp)
            for f in observed:
                inv.fills += 1
                if f.reason == "race":
                    race_fills.append(f)
                else:
                    queue_fills.append(f)

        prev_ts = ts

        if t_rem < cfg.min_t_remaining_sec:
            engine.cancel(ts=ts, reason="halted")
            continue

        # decide quotes
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
                    new_o = engine.post(tid, side, p, s, bk["bids"], ts)
                    open_orders[side] = new_o
                    posted_shares += s

    q_sh = sum(f.size for f in queue_fills)
    r_sh = sum(f.size for f in race_fills)
    q_cost = sum(f.size * f.price for f in queue_fills)
    r_cost = sum(f.size * f.price for f in race_fills)
    
    # Markout against last mid
    q_markout = 0.0
    for f in queue_fills:
        lbl = "UP" if f.side == "UP" else "DOWN"
        if mid_hist[lbl]:
            last_mid = mid_hist[lbl][-1][1]
            q_markout += (last_mid - f.price) * f.size

    r_markout = 0.0
    for f in race_fills:
        lbl = "UP" if f.side == "UP" else "DOWN"
        if mid_hist[lbl]:
            last_mid = mid_hist[lbl][-1][1]
            r_markout += (last_mid - f.price) * f.size
            
    return {
        "posted_shares": posted_shares,
        "queue_shares": q_sh,
        "race_shares": r_sh,
        "total_shares": q_sh + r_sh,
        "queue_fills_n": len(queue_fills),
        "race_fills_n": len(race_fills),
        "queue_cost": q_cost,
        "race_cost": r_cost,
        "queue_markout": q_markout,
        "race_markout": r_markout,
    }


def main():
    db_path = ROOT / "archive" / "20260729" / "books.db"
    raw_windows = load_windows(db_path)
    windows = [w for w in raw_windows if w.coverage >= 0.5]
    cfg = load_cfg()
    
    print(f"Loaded {len(windows)} usable windows (>=50% coverage) from {db_path.name}")
    print("\n" + "="*100)
    print(f"{'tau (ms)':<10} | {'Posted (sh)':<12} | {'Queue (sh)':<11} | {'Race (sh)':<10} | {'Total (sh)':<11} | {'Fill Rate':<10} | {'Q Markout':<11} | {'Race Markout':<12}")
    print("="*100)
    
    for tau in [0.0, 150.0, 250.0, 500.0]:
        tot_posted = 0.0
        tot_q = 0.0
        tot_r = 0.0
        tot_q_mo = 0.0
        tot_r_mo = 0.0
        
        for w in windows:
            res = replay_with_tau(w, cfg, tau)
            tot_posted += res["posted_shares"]
            tot_q += res["queue_shares"]
            tot_r += res["race_shares"]
            tot_q_mo += res["queue_markout"]
            tot_r_mo += res["race_markout"]
            
        tot_filled = tot_q + tot_r
        fr = (tot_filled / tot_posted * 100.0) if tot_posted > 0 else 0.0
        print(f"{tau:<10.1f} | {tot_posted:<12,.0f} | {tot_q:<11,.1f} | {tot_r:<10,.1f} | {tot_filled:<11,.1f} | {fr:<9.3f}% | ${tot_q_mo:<10.2f} | ${tot_r_mo:<11.2f}")
    print("="*100)


if __name__ == "__main__":
    main()
