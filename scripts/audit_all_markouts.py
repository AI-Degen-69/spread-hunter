"""Detailed audit of all fills and markouts in eval_post_latency_sensitivity."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy.config import load as load_cfg
from strategy.fills import QueueFillEngine
from strategy.quotes import Inventory
from scripts.measure_fill_rate import load_windows, Window, traded_between, targets_for


def audit():
    db_path = ROOT / "archive" / "20260729" / "books.db"
    raw_windows = load_windows(db_path)
    windows = [w for w in raw_windows if w.coverage >= 0.5]
    cfg = load_cfg()

    for name, net_oneway, c_ack, p_acc in [
        ("Naive Baseline (tau=0)", 0.0, 0.0, 0.0),
        ("Isolated tau_post=150ms", 0.0, 0.0, 150.0),
        ("Combined Phase 1", 100.0, 150.0, 50.0),
    ]:
        print(f"\n==================== {name} ====================")
        tot_q_sh = 0.0
        tot_r_sh = 0.0
        tot_adv_mo = 0.0
        tot_ben_mo = 0.0
        
        for w_idx, w in enumerate(windows):
            eng = QueueFillEngine(net_oneway_ms=net_oneway, cancel_venue_ack_ms=c_ack, post_venue_accept_ms=p_acc)
            inv = Inventory()
            prev_ts = None
            open_orders = {}
            mid_hist = {"UP": [], "DOWN": []}
            q_fills = []
            r_fills = []
            
            for poll in w.polls:
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
                        if f.reason == "race":
                            r_fills.append(f)
                        else:
                            q_fills.append(f)

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

            for f in q_fills:
                lbl = "UP" if f.side == "UP" else "DOWN"
                last_mid = mid_hist[lbl][-1][1] if mid_hist[lbl] else f.price
                mo = (last_mid - f.price) * f.size
                tot_q_sh += f.size
                if mo < 0:
                    tot_adv_mo += mo
                else:
                    tot_ben_mo += mo
                print(f"  [Q-FILL] Win {w_idx:2d} | {f.side} p={f.price:.4f} sz={f.size:5.1f} | last_mid={last_mid:.4f} | MO={mo:+6.2f}")

            for f in r_fills:
                lbl = "UP" if f.side == "UP" else "DOWN"
                last_mid = mid_hist[lbl][-1][1] if mid_hist[lbl] else f.price
                mo = (last_mid - f.price) * f.size
                tot_r_sh += f.size
                print(f"  [R-FILL] Win {w_idx:2d} | {f.side} p={f.price:.4f} sz={f.size:5.1f} | last_mid={last_mid:.4f} | MO={mo:+6.2f}")

        print(f"TOTAL Q-Shares: {tot_q_sh:.1f}, R-Shares: {tot_r_sh:.1f}")
        print(f"Adverse Markout: ${tot_adv_mo:.2f}, Benign Markout: ${tot_ben_mo:.2f}, Net: ${tot_adv_mo + tot_ben_mo:.2f}")


if __name__ == "__main__":
    audit()
