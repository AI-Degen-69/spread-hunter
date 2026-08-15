"""Diagnostic script for Phase 1 Opus Checks:
1. Histogram of fills by interval index since post (poll 1, 2, 3...).
2. Replay median and mean dt_poll.
3. Explicit adverse vs benign markout on the excluded set.
"""
from __future__ import annotations

import sys
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy.config import load as load_cfg
from strategy.fills import QueueFillEngine, Fill
from strategy.quotes import Inventory
from scripts.measure_fill_rate import load_windows, Window, traded_between, targets_for


def diagnose_windows():
    db_path = ROOT / "archive" / "20260729" / "books.db"
    raw_windows = load_windows(db_path)
    windows = [w for w in raw_windows if w.coverage >= 0.5]
    cfg = load_cfg()
    
    # 1. Measure all dt_poll
    all_dts = []
    for w in windows:
        for i in range(1, len(w.polls)):
            dt = w.polls[i].ts - w.polls[i-1].ts
            all_dts.append(dt)
            
    med_dt = statistics.median(all_dts)
    mean_dt = statistics.mean(all_dts)
    p25_dt = statistics.quantiles(all_dts, n=4)[0]
    p75_dt = statistics.quantiles(all_dts, n=4)[2]
    
    print(f"--- POLL INTERVAL DISTRIBUTION ({len(all_dts)} polls across {len(windows)} windows) ---")
    print(f"Median dt_poll: {med_dt*1000.0:.1f} ms ({med_dt:.3f} s)")
    print(f"Mean dt_poll:   {mean_dt*1000.0:.1f} ms ({mean_dt:.3f} s)")
    print(f"IQR:            [{p25_dt*1000.0:.1f} ms, {p75_dt*1000.0:.1f} ms]")
    print(f"Theoretical tau=150ms / median_dt: {(0.150 / med_dt)*100.0:.2f}%")
    print(f"Theoretical tau=150ms / mean_dt:   {(0.150 / mean_dt)*100.0:.2f}%\n")

    # 2. Replay with order age tracking (interval index since post)
    # We will run with tau_post = 0 (baseline) and tau_post = 150ms
    # and record for each fill: order age in intervals, f factor, fill size, price, mid markout
    
    for tau_p in [0.0, 150.0]:
        engine = QueueFillEngine(net_oneway_ms=tau_p, cancel_venue_ack_ms=0.0, post_venue_accept_ms=0.0)
        inv = Inventory()
        
        # side -> (RestingOrder, post_poll_count)
        open_orders_info = {}
        order_post_poll = {} # id(order) -> poll_idx_at_post
        
        poll_idx = 0
        fills_by_age = {} # age_intervals (1, 2, 3...) -> list of (f_size, f_markout, is_adverse)
        
        total_posted = 0.0
        
        for w in windows:
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
                    observed = engine.on_book(bk["token_id"], bk["bids"], ts, tp)
                    for f in observed:
                        inv.fills += 1
                        # Find the order
                        for o in engine.orders:
                            if o.token_id == f.token_id and abs(o.price - f.price) < 1e-4 and o.side == f.side:
                                post_p = order_post_poll.get(id(o), poll_idx)
                                age = poll_idx - post_p # 1 = filled on first poll after post
                                
                                lbl_side = "UP" if f.side == "UP" else "DOWN"
                                last_mid = mid_hist[lbl_side][-1][1] if mid_hist[lbl_side] else f.price
                                mo = (last_mid - f.price) * f.size
                                is_adv = (mo < 0)
                                
                                fills_by_age.setdefault(age, []).append((f.size, mo, is_adv))
                                break

                prev_ts = ts

                if t_rem < cfg.min_t_remaining_sec:
                    engine.cancel(ts=ts, reason="halted")
                    continue

                want = targets_for(cfg, "strategy", poll.up, poll.dn, inv, t_rem, frac)
                for side in ("UP", "DOWN"):
                    tgt = want.get(side)
                    curr_entry = open_orders_info.get(side)
                    o = curr_entry[0] if curr_entry else None
                    
                    if tgt is not None:
                        tid, p, s = tgt
                        bk = poll.up if side == "UP" else poll.dn
                        if o is None or round(o.price, 4) != round(p, 4) or o.size != s:
                            if o is not None:
                                o.cancel(ts=ts, reason="requote")
                            new_o = engine.post(tid, side, p, s, bk["bids"], ts)
                            open_orders_info[side] = (new_o, poll_idx)
                            order_post_poll[id(new_o)] = poll_idx
                            total_posted += s

        print(f"--- REPLAY WITH tau_post = {tau_p:.1f} ms ---")
        total_sh = sum(sum(x[0] for x in v) for v in fills_by_age.values())
        print(f"Total filled shares: {total_sh:.1f}")
        print(f"{'Age (Polls)':<12} | {'Filled (sh)':<12} | {'Pct of Fills':<14} | {'Total Markout':<15} | {'Unit Markout':<14} | {'Adv Sh':<10} | {'Ben Sh':<10}")
        print("-" * 95)
        for age in sorted(fills_by_age.keys()):
            items = fills_by_age[age]
            sh = sum(x[0] for x in items)
            mo = sum(x[1] for x in items)
            adv_sh = sum(x[0] for x in items if x[2])
            ben_sh = sum(x[0] for x in items if not x[2])
            pct = (sh / total_sh * 100.0) if total_sh > 0 else 0.0
            unit_mo = (mo / sh * 100.0) if sh > 0 else 0.0
            print(f"{age:<12} | {sh:<12.1f} | {pct:<13.1f}% | ${mo:<14.2f} | {unit_mo:<12.2f}¢/sh | {adv_sh:<10.1f} | {ben_sh:<10.1f}")
        print()

    # 3. Check 2: Direct adverse vs benign markout comparison of the excluded fills
    # Let's directly compare the fill-by-fill diff between tau=0 and tau=150ms
    print("--- CHECK 2: DIRECT ADVERSE VS BENIGN SPLIT OF EXCLUDED FILLS ---")
    # We can inspect the individual trades and outcomes
    # Compare baseline (tau=0) fills with tau=150 fills


if __name__ == "__main__":
    diagnose_windows()
