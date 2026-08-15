"""Analyze recorded WebSocket snapshots DB for staleness, jitter, NTP trajectory, and rollovers."""
from __future__ import annotations

import argparse
import math
import sqlite3
import statistics
from pathlib import Path

def percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    sorted_d = sorted(data)
    k = (len(sorted_d) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_d[int(k)]
    return sorted_d[int(f)] * (c - k) + sorted_d[int(c)] * (k - f)

def print_dist(name: str, data: list[float]):
    if not data:
        print(f"  {name}: N=0")
        return
    print(f"  {name} (N={len(data)}):")
    print(f"    Min:    {min(data):.2f} ms")
    print(f"    P25:    {percentile(data, 25):.2f} ms")
    print(f"    Median: {statistics.median(data):.2f} ms")
    print(f"    Mean:   {statistics.mean(data):.2f} ms")
    print(f"    P75:    {percentile(data, 75):.2f} ms")
    print(f"    P95:    {percentile(data, 95):.2f} ms")
    print(f"    Max:    {max(data):.2f} ms")
    print(f"    IQR:    {percentile(data, 75) - percentile(data, 25):.2f} ms")
    print(f"    StdDev: {statistics.stdev(data) if len(data)>1 else 0:.2f} ms")

def analyze(db_path: Path):
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    
    # Check NTP samples
    ntp_cur = conn.cursor()
    try:
        ntp_cur.execute("SELECT ts, server, offset_ms, rtt_ms FROM ntp_samples ORDER BY ts ASC")
        ntp_rows = ntp_cur.fetchall()
    except Exception:
        ntp_rows = []

    print(f"=== NTP TRAJECTORY AUDIT (N={len(ntp_rows)}) ===")
    if ntp_rows:
        offsets = [r[2] for r in ntp_rows]
        rtts = [r[3] for r in ntp_rows]
        print(f"  Offset: min={min(offsets):.2f}ms, P25={percentile(offsets, 25):.2f}ms, median={statistics.median(offsets):.2f}ms, P75={percentile(offsets, 75):.2f}ms, max={max(offsets):.2f}ms, IQR={percentile(offsets, 75)-percentile(offsets, 25):.2f}ms")
        print(f"  RTT:    min={min(rtts):.2f}ms, P25={percentile(rtts, 25):.2f}ms, median={statistics.median(rtts):.2f}ms, P75={percentile(rtts, 75):.2f}ms, max={max(rtts):.2f}ms")
        print(f"  Start Offset: {offsets[0]:.2f}ms -> End Offset: {offsets[-1]:.2f}ms (Drift: {offsets[-1]-offsets[0]:.2f}ms over {ntp_rows[-1][0]-ntp_rows[0][0]:.1f}s)")
    else:
        print("  No NTP samples in DB; using standard baseline offset (-541.31 ms)")

    # Read snapshots
    cur = conn.cursor()
    cur.execute("""
        SELECT ts, ts_venue, is_rollover 
        FROM snapshots 
        WHERE side = 'UP' AND ts_venue IS NOT NULL 
        ORDER BY ts ASC
    """)
    rows = cur.fetchall()
    print(f"\n=== SNAPSHOT STALENESS AUDIT (N={len(rows)}) ===")
    if not rows:
        print("No valid snapshot rows found.")
        return

    # Build piecewise time-matched NTP offset function
    def get_ntp_offset(t):
        if not ntp_rows:
            return -541.31
        closest = min(ntp_rows, key=lambda r: abs(r[0] - t))
        return closest[2]

    raw_all = []
    corr_all = []
    corr_steady = []
    corr_rollover = []
    intervals = []

    prev_ts = None
    for ts, ts_venue, is_rollover in rows:
        if prev_ts is not None:
            intervals.append((ts - prev_ts) * 1000.0)
        prev_ts = ts

        raw_staleness = (ts - ts_venue) * 1000.0
        offset_ms = get_ntp_offset(ts)
        corr_staleness = raw_staleness + offset_ms

        raw_all.append(raw_staleness)
        corr_all.append(corr_staleness)
        if is_rollover:
            corr_rollover.append(corr_staleness)
        else:
            corr_steady.append(corr_staleness)

    print("\n--- RAW STALENESS (ts - ts_venue) ---")
    print_dist("All Events", raw_all)

    print("\n--- OFFSET-CORRECTED STALENESS ((ts + NTP_offset) - ts_venue) ---")
    print_dist("All Events", corr_all)
    print_dist("Steady-State Events", corr_steady)
    if corr_rollover:
        print_dist("Rollover Events", corr_rollover)

    print("\n--- INTER-EVENT SAMPLING CADENCE ---")
    print_dist("Intervals", intervals)

    # Windows summary
    cur.execute("SELECT COUNT(*), MIN(polls), MAX(polls) FROM windows")
    w_count, min_p, max_p = cur.fetchone()
    print(f"\n=== WINDOWS AUDIT ===")
    print(f"  Windows captured: {w_count} (polls/window: min={min_p}, max={max_p})")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("db", nargs="?", default="books_fast.db")
    args = p.parse_args()
    analyze(Path(args.db))
