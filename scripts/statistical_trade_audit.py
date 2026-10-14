"""
statistical_trade_audit.py
סקריפט לניתוח סטטיסטי מותאם למבנה מסד הנתונים של הבוט.
שואב נתונים מהטבלאות: closes, single_leg_lifecycle, shadow_merge_legs.
שיעור המיזוג נספר משדה method של רשומת הסגירה (closes.method); שורות
shadow_merge_legs הן מטמון פר-רגל לעלות, לא סגירות, ואינן נספרות כשיעור.
"""

import sqlite3
import math
import sys
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

# Ensure repo root is importable when run as ``python scripts/statistical_trade_audit.py``.
_LIVE_ROOT = Path(__file__).resolve().parent.parent
if str(_LIVE_ROOT) not in sys.path:
    sys.path.insert(0, str(_LIVE_ROOT))

from core_brain.close_methods import MERGE_METHODS as MERGE_CLOSE_METHODS
from core_brain.kpi import (
    SAMPLE_SUFFICIENCY_LEVELS,
    required_sample_size_for_mean,
    wilson_ci,
)
from core_brain.statistical_analytics import ci_critical_value, normal_mean_interval

# Level labels the audit has always printed ("95%"...); the multipliers behind
# them live in `core_brain.kpi` now -- this dict is labels, not a second table.
Z_CRITICAL: Dict[str, float] = {f"{level}%": z for level, z in SAMPLE_SUFFICIENCY_LEVELS}
# Reverse map so the adapters below keep their `(…, z)` signatures while still
# reaching the level-keyed Student-t table.
_LEVEL_BY_Z: Dict[float, int] = {z: level for level, z in SAMPLE_SUFFICIENCY_LEVELS}

# Close methods that mean the pair was merged back into $1.00 of USDC --
# MERGE_METHODS from the canonical leaf `core_brain/close_methods.py` (imported
# above as MERGE_CLOSE_METHODS). The core_brain import works here (repo root is
# on sys.path); the old "may fail" rationale is gone.

def calculate_mean_and_std(values: List[float]) -> Tuple[float, float]:
    """מחשב ממוצע וסטיית תקן מדגמית."""
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mean = sum(values) / n
    if n == 1:
        return mean, 0.0
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    return mean, math.sqrt(variance)

def mean_confidence_interval(mean: float, std_dev: float, n: int, z: float) -> Tuple[float, float, float]:
    """מחשב רווח סמך עבור משתנה רציף (רווח/הפסד, זמן החזקה).

    Thin adapter over `core_brain.statistical_analytics.normal_mean_interval`.
    The multiplier is Student-t while the sample is small, normal once it is
    not (#541: one convention); `(mean, mean, 0.0)` when n <= 1 or zero
    deviation, exactly as before.
    """
    if n <= 1 or std_dev == 0.0:
        return mean, mean, 0.0
    level = _LEVEL_BY_Z.get(z)
    mult = ci_critical_value(level, n, z) if level is not None else z
    return normal_mean_interval(mean, std_dev, n, mult)

def proportion_confidence_interval(successes: int, n: int, z: float) -> Optional[Tuple[float, float, float]]:
    """מחשב רווח סמך לפרופורציה (Wilson Score Interval).

    Thin adapter over `core_brain.kpi.wilson_ci`: same (lower, upper, margin)
    tuple, same `None` for impossible counts.
    """
    if n <= 0 or successes < 0 or successes > n:
        return None
    interval = wilson_ci(successes, n, z)
    if interval is None:
        return None
    low, high = interval["lower"], interval["upper"]
    return low, high, (high - low) / 2.0

def fetch_table_rows(cursor: sqlite3.Cursor, table_name: str) -> List[Dict[str, Any]]:
    """שולף את כל הרשומות מטבלה ומחזיר אותן כמילונים לפי שמות העמודות."""
    cursor.execute(f"PRAGMA table_info({table_name});")
    columns = [col[1] for col in cursor.fetchall()]
    cursor.execute(f"SELECT * FROM {table_name};")
    return [dict(zip(columns, row)) for row in cursor.fetchall()]

def _print_proportion_block(label: str, successes: int, n: int) -> None:
    """Print one proportion section: fraction plus Wilson CI per level.

    When the count is impossible the interval is None and a single named
    unavailable line is printed instead — never an x/y fraction, never a
    traceback. The valid path prints byte-identical lines to the old inline
    blocks.
    """
    if proportion_confidence_interval(successes, n, Z_CRITICAL["95%"]) is None:
        print(f"{label}: rate unavailable (success count is outside 0..N)")
        return
    print(f"{label}: {successes}/{n} ({(successes/n)*100:.2f}%)")
    for cl, z in Z_CRITICAL.items():
        interval = proportion_confidence_interval(successes, n, z)
        if interval is None:  # guarded above; kept so a future edit cannot re-crash here
            print(f"{label}: rate unavailable (success count is outside 0..N)")
            return
        low, high, margin = interval
        print(f"  [{cl} CL]: [{low*100:.2f}%, {high*100:.2f}%] | Margin: +/- {margin*100:.2f}%")


def run_analysis(db_path: str):
    print(f"Opening database: {db_path}...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return

    try:
        # All three tables are required; a missing one gets a naming sentence, not a traceback.
        existing = {row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = ("closes", "single_leg_lifecycle", "shadow_merge_legs")
        missing = [t for t in required if t not in existing]
        if missing:
            names = ", ".join(f"'{t}'" for t in missing)
            noun = "Table" if len(missing) == 1 else "Tables"
            print(f"{noun} {names} not found in {db_path}. "
                  f"This audit needs closes, single_leg_lifecycle, and shadow_merge_legs.")
            return

        # שליפת רשומות מהטבלאות הרלוונטיות
        closes = fetch_table_rows(cursor, "closes")
        single_legs = fetch_table_rows(cursor, "single_leg_lifecycle")
        merge_legs = fetch_table_rows(cursor, "shadow_merge_legs")
    finally:
        conn.close()

    total_closed = len(closes)
    print(f"Found {total_closed} records in 'closes' table.\n")

    if total_closed == 0:
        print("No closed trade records found to analyze.")
        return

    # איסוף נתונים לחישוב
    pnl_values: List[float] = []
    holding_times: List[float] = []
    stop_loss_count = 0

    for r in closes:
        # 1. חילוץ PnL
        pnl = r.get("realized_pnl")
        if pnl is None:
            pnl = r.get("pnl", 0.0)
        pnl_values.append(float(pnl))

        # 2. חילוץ סיבת יציאה ובדיקת Stop Loss
        reason = str(r.get("exit_reason", "") or r.get("close_reason", "") or r.get("reason", "")).lower()
        if "stop" in reason:
            stop_loss_count += 1

        # 3. חילוץ זמן החזקה
        hold = r.get("hold_duration_sec") or r.get("duration_sec") or r.get("duration")
        if hold is not None:
            holding_times.append(float(hold))
        elif "close_time" in r and "open_time" in r and r["close_time"] and r["open_time"]:
            try:
                holding_times.append(float(r["close_time"]) - float(r["open_time"]))
            except (ValueError, TypeError):
                pass

    # שיעור המיזוג נספר מרשומת הסגירה עצמה (שדה method), שורה אחת = סגירה
    # אחת לכל היותר. בלי עמודת method אין מקור — שורות shadow_merge_legs
    # הן מטמון פר-רגל, לא סגירות, ואינן מחליפות את הספירה.
    merge_count: Optional[int] = None
    if closes and "method" in closes[0]:
        merge_count = sum(1 for r in closes if r.get("method") in MERGE_CLOSE_METHODS)

    # חישוב מדדי PnL
    mean_pnl, std_pnl = calculate_mean_and_std(pnl_values)

    print("=" * 75)
    print(" 1. PNL EXPECTANCY ($)")
    print("=" * 75)
    print(f"Total Closed Trades (N):       {total_closed}")
    print(f"Sample Mean PnL:              ${mean_pnl:.4f}")
    print(f"Standard Deviation:           ${std_pnl:.4f}\n")

    print("Confidence Intervals for Expected PnL:")
    for cl, z in Z_CRITICAL.items():
        low, high, margin = mean_confidence_interval(mean_pnl, std_pnl, total_closed, z)
        print(f"  [{cl} CL]: [${low:.4f}, ${high:.4f}] | Margin of Error: +/- ${margin:.4f}")

    print("\n" + "=" * 75)
    print(" 2. PROPORTIONS: STOP LOSS RATE & MERGE RATE")
    print("=" * 75)
    _print_proportion_block("Stop Loss Exits", stop_loss_count, total_closed)
    print()
    if merge_count is None:
        print("Merged Positions: merge rate unavailable from this database (closes has no method column)")
    else:
        _print_proportion_block("Merged Positions", merge_count, total_closed)

    if holding_times:
        mean_hold, std_hold = calculate_mean_and_std(holding_times)
        print("\n" + "=" * 75)
        print(" 3. POSITION HOLDING TIME")
        print("=" * 75)
        print(f"Mean Duration: {mean_hold:.1f}s ({mean_hold/60.0:.2f} min) | Std Dev: {std_hold:.1f}s\n")
        print("Confidence Intervals for Holding Time:")
        for cl, z in Z_CRITICAL.items():
            low, high, margin = mean_confidence_interval(mean_hold, std_hold, len(holding_times), z)
            print(f"  [{cl} CL]: [{low:.1f}s, {high:.1f}s] | Margin: +/- {margin:.1f}s")

    print("\n" + "=" * 75)
    print(" 4. SAMPLE SIZE PLANNING FOR PNL EXPECTANCY")
    print("=" * 75)
    for target in [0.05, 0.02, 0.01]:
        print(f"\nTarget Margin of Error: +/- ${target:.2f}")
        for cl, z in Z_CRITICAL.items():
            req_n = required_sample_size_for_mean(std_pnl, target, z)
            delta = max(0, req_n - total_closed)
            print(f"  [{cl} CL]: Required Total N = {req_n} (Need {delta} more trades)")
    print("=" * 75)

if __name__ == "__main__":
    db_file = r".\data\03_shadow_conservative_09-10_16-41.db"
    if len(sys.argv) > 1:
        db_file = sys.argv[1]
    run_analysis(db_file)