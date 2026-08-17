"""Evaluates tau_post sensitivity across [0, 75, 150, 300] ms and combined Phase 1 on recorded books."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy.config import load as load_cfg
from strategy.fills import QueueFillEngine
from strategy.quotes import Inventory
from scripts.measure_fill_rate import load_windows, Window, traded_between, targets_for


def replay_engine(w: Window, cfg, net_oneway_ms: float, cancel_ack_ms: float, post_accept_ms: float) -> dict:
    engine = QueueFillEngine(
        net_oneway_ms=net_oneway_ms,
        cancel_venue_ack_ms=cancel_ack_ms,
        post_venue_accept_ms=post_accept_ms,
    )
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
    
    # Markout against last mid
    q_markout = 0.0
    adverse_q_mo = 0.0
    benign_q_mo = 0.0
    for f in queue_fills:
        lbl = "UP" if f.side == "UP" else "DOWN"
        if mid_hist[lbl]:
            last_mid = mid_hist[lbl][-1][1]
            diff = (last_mid - f.price) * f.size
            q_markout += diff
            if diff < 0:
                adverse_q_mo += diff
            else:
                benign_q_mo += diff

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
        "queue_fills": queue_fills,
        "race_fills": race_fills,
        "queue_markout": q_markout,
        "adverse_queue_markout": adverse_q_mo,
        "benign_queue_markout": benign_q_mo,
        "race_markout": r_markout,
        "reconciliation": engine.reconciliation,
    }


def main():
    db_path = ROOT / "archive" / "20260729" / "books.db"
    raw_windows = load_windows(db_path)
    windows = [w for w in raw_windows if w.coverage >= 0.5]
    cfg = load_cfg()
    
    print(f"Loaded {len(windows)} usable windows (>=50% coverage) from {db_path.name}")
    
    # 1. tau_post sensitivity sweep (with tau_cancel = 0 to isolate tau_post effect)
    print("\n" + "="*115)
    print(f"1. TAU_POST SENSITIVITY SWEEP (Isolated, tau_cancel = 0ms)")
    print("="*115)
    print(f"{'tau_post (ms)':<14} | {'Posted (sh)':<12} | {'Filled (sh)':<11} | {'Fill Rate':<10} | {'Excluded (sh)':<13} | {'Excl %':<8} | {'Surviving Markout':<18} | {'Markout/sh':<11}")
    print("-"*115)
    
    base_filled = None
    base_mo = None
    for tau_p in [0.0, 75.0, 150.0, 300.0]:
        tot_posted = 0.0
        tot_filled = 0.0
        tot_mo = 0.0
        
        for w in windows:
            res = replay_engine(w, cfg, net_oneway_ms=tau_p, cancel_ack_ms=0.0, post_accept_ms=0.0)
            tot_posted += res["posted_shares"]
            tot_filled += res["total_shares"]
            tot_mo += res["queue_markout"]
            
        if base_filled is None:
            base_filled = tot_filled
            base_mo = tot_mo
            
        excl_sh = base_filled - tot_filled
        excl_pct = (excl_sh / base_filled * 100.0) if base_filled > 0 else 0.0
        fr = (tot_filled / tot_posted * 100.0) if tot_posted > 0 else 0.0
        mo_per_sh = (tot_mo / tot_filled * 100.0) if tot_filled > 0 else 0.0
        
        print(f"{tau_p:<14.1f} | {tot_posted:<12,.0f} | {tot_filled:<11,.1f} | {fr:<9.3f}% | {excl_sh:<13.1f} | {excl_pct:<7.2f}% | ${tot_mo:<17.2f} | {mo_per_sh:<10.2f}¢/sh")
    print("="*115)
    
    # 2. Combined Phase 1 evaluation (Naive vs Full Phase 1 model)
    print("\n" + "="*115)
    print(f"2. COMBINED PHASE 1 MODEL EVALUATION")
    print("="*115)
    
    # A) Naive Baseline: tau_cancel = 0, tau_post = 0
    tot_posted_naive = 0.0
    tot_filled_naive = 0.0
    tot_mo_naive = 0.0
    tot_adv_naive = 0.0
    tot_ben_naive = 0.0
    for w in windows:
        res = replay_engine(w, cfg, net_oneway_ms=0.0, cancel_ack_ms=0.0, post_accept_ms=0.0)
        tot_posted_naive += res["posted_shares"]
        tot_filled_naive += res["total_shares"]
        tot_mo_naive += res["queue_markout"]
        tot_adv_naive += res["adverse_queue_markout"]
        tot_ben_naive += res["benign_queue_markout"]

    # B) Full Phase 1: tau_cancel = 250ms (100+150), tau_post = 150ms (100+50)
    tot_posted_p1 = 0.0
    tot_filled_p1 = 0.0
    tot_q_p1 = 0.0
    tot_r_p1 = 0.0
    tot_mo_p1 = 0.0
    tot_q_mo_p1 = 0.0
    tot_r_mo_p1 = 0.0
    tot_adv_p1 = 0.0
    tot_ben_p1 = 0.0
    for w in windows:
        res = replay_engine(w, cfg, net_oneway_ms=100.0, cancel_ack_ms=150.0, post_accept_ms=50.0)
        tot_posted_p1 += res["posted_shares"]
        tot_filled_p1 += res["total_shares"]
        tot_q_p1 += res["queue_shares"]
        tot_r_p1 += res["race_shares"]
        tot_q_mo_p1 += res["queue_markout"]
        tot_r_mo_p1 += res["race_markout"]
        tot_mo_p1 += (res["queue_markout"] + res["race_markout"])
        tot_adv_p1 += res["adverse_queue_markout"]
        tot_ben_p1 += res["benign_queue_markout"]

    fr_naive = tot_filled_naive / tot_posted_naive * 100.0
    fr_p1 = tot_filled_p1 / tot_posted_p1 * 100.0
    
    print(f"Metric                               | Naive Baseline     | Phase 1 Combined Model")
    print(f"-------------------------------------+--------------------+-----------------------")
    print(f"Posted Shares                        | {tot_posted_naive:>18,.0f} | {tot_posted_p1:>21,.0f}")
    print(f"Total Filled Shares                  | {tot_filled_naive:>18.1f} | {tot_filled_p1:>21.1f}")
    print(f"  - Queue-credited shares            | {tot_filled_naive:>18.1f} | {tot_q_p1:>21.1f}")
    print(f"  - Race-loss shares (tau_cancel=250)| {0.0:>18.1f} | {tot_r_p1:>21.1f}")
    print(f"Fill Rate (%)                        | {fr_naive:>17.3f}% | {fr_p1:>20.3f}%")
    print(f"Fill Rate Delta (%)                  | {'-':>18} | {(fr_p1 - fr_naive):>+20.3f}%")
    print(f"Total Markout ($)                    | ${tot_mo_naive:>17.2f} | ${tot_mo_p1:>20.2f}")
    print(f"  - Adverse Markout                  | ${tot_adv_naive:>17.2f} | ${tot_adv_p1:>20.2f}")
    print(f"  - Benign Markout                   | ${tot_ben_naive:>17.2f} | ${tot_ben_p1:>20.2f}")
    print(f"  - Race Fills Markout               | ${0.0:>17.2f} | ${tot_r_mo_p1:>20.2f}")
    print(f"Overall Unit Markout (¢/sh)          | {(tot_mo_naive/tot_filled_naive*100.0):>17.2f}¢ | {(tot_mo_p1/tot_filled_p1*100.0):>20.2f}¢")
    print("="*115)


if __name__ == "__main__":
    main()
