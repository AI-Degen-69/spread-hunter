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
from typing import List, Optional, Tuple, Dict, Any

# ערכי z קריטיים עבור רמות סמך של 95%, 98% ו-99%
Z_CRITICAL: Dict[str, float] = {
    "95%": 1.95996,
    "98%": 2.32635,
    "99%": 2.57583,
}

# Close methods that mean the pair was merged back into $1.00 of USDC.
# Pinned copy of `core_brain/registry_state.py:41 MERGE_CLOSE_METHODS` (kept
# local, not imported: `python scripts/...` puts `scripts/` first on the
# import path, so a `core_brain` import may fail). The pin is enforced by
# `tests/test_statistical_trade_audit.py::test_merge_methods_pinned_to_registry`.
MERGE_CLOSE_METHODS = ("merge", "shadow_merge")

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
    """מחשב רווח סמך עבור משתנה רציף (רווח/הפסד, זמן החזקה)."""
    if n <= 1 or std_dev == 0.0:
        return mean, mean, 0.0
    margin = z * (std_dev / math.sqrt(n))
    return mean - margin, mean + margin, margin

def proportion_confidence_interval(successes: int, n: int, z: float) -> Optional[Tuple[float, float, float]]:
    """מחשב רווח סמך לפרופורציה (Wilson Score Interval).

    Returns None ("no estimate is possible") when the count is impossible:
    `n <= 0`, `successes < 0`, or `successes > n`. Callers print a named
    unavailable line instead of a fraction in that case.
    """
    if n <= 0 or successes < 0 or successes > n:
        return None
    p = successes / n
    denominator = 1.0 + (z ** 2) / n
    center = (p + (z ** 2) / (2.0 * n)) / denominator
    spread = (z / denominator) * math.sqrt((p * (1.0 - p) / n) + ((z ** 2) / (4.0 * (n ** 2))))
    lower = max(0.0, center - spread)
    upper = min(1.0, center + spread)
    return lower, upper, (upper - lower) / 2.0

def required_sample_size_for_mean(std_dev: float, target_margin: float, z: float) -> int:
    """מחשב גודל מדגם מינימלי נדרש לדיוק מבוקש."""
    if target_margin <= 0 or std_dev <= 0:
        return 0
    return math.ceil(((z * std_dev) / target_margin) ** 2)

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