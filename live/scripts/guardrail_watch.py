"""Guardrail watcher: flag the two live-run failure signatures the moment they
happen, independent of the poll loop.

1. REPEAT-EXIT -- the same pair_id exits more than once inside the window.
   The RC-1 repeat-sell loop's signature: `exit_naked_leg` used to sell at the
   venue and write nothing, so the next cycle re-discovered the same naked pair
   and sold it again (pair-00845 exited 3x, pair-eea99 twice in 5s in
   production). With the close-recording fix a pair can only exit once -- so a
   second `pairs_exited` for the same pair_id is the bug coming back.

2. OVER-CAP PAIR -- a filled pair at/over `max_pair_cost`. The instrument pays
   exactly $1.00, so a pair assembled at >= the cap is a booked loss; the
   RC-2 cap now blocks the light-side quote at the source, and this watcher is
   the backstop that screams if a pair still forms.

Detection is pure and testable; the loop only calls it, dedupes, and alerts
(console, `run/guardrail_alerts.log`, and a `guardrail_alert` ring event so the
dashboard telemetry sees it).

Run:  cd live && python scripts/guardrail_watch.py [--interval 5] [--once]
"""
from __future__ import annotations

import argparse
import datetime
import sqlite3
import sys
import time
from pathlib import Path

LIVE_ROOT = Path(__file__).resolve().parent.parent
if str(LIVE_ROOT) not in sys.path:
    sys.path.insert(0, str(LIVE_ROOT))

DEFAULT_RING = LIVE_ROOT / "run" / "cycle_events.jsonl"
DEFAULT_DB = LIVE_ROOT / "run" / "live.db"
DEFAULT_ALERTS_LOG = LIVE_ROOT / "run" / "guardrail_alerts.log"


def _parse_iso(ts: str) -> float:
    """Ring timestamps are UTC ISO second-precision: 2026-08-21T08:23:24Z."""
    try:
        dt = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
        return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
    except (ValueError, TypeError):
        return 0.0


def detect_repeat_exits(events: list[dict], window_s: float = 900.0,
                        now: float | None = None) -> list[dict]:
    """Pair_ids with 2+ `pairs_exited` ring events inside the window.

    Returns [{pair_id, count, last_ts}] sorted newest-first. A single exit is
    the healthy one-shot; two within the window is the repeat-sell signature.
    """
    now = now if now is not None else time.time()
    per_pair: dict[str, list[float]] = {}
    for e in events:
        if e.get("action") != "pairs_exited":
            continue
        pid = (e.get("extra") or {}).get("pair_id")
        if not pid:
            continue
        ts = _parse_iso(e.get("ts", ""))
        if ts <= 0 or (now - ts) > window_s:
            continue
        per_pair.setdefault(str(pid), []).append(ts)

    out = []
    for pid, tss in per_pair.items():
        if len(tss) >= 2:
            out.append({"pair_id": pid, "count": len(tss), "last_ts": max(tss)})
    return sorted(out, key=lambda r: r["last_ts"], reverse=True)


def detect_over_cap_pairs(db_path: Path | str, cap: float = 0.995) -> list[dict]:
    """Conditions whose filled pair cost is at/over the cap.

    Returns [{condition_id, pair_cost}] sorted most-expensive first. The side
    map comes from the quotes ledger (the same source the dashboard uses); a
    condition without a resolvable UP/DOWN pair is skipped.
    """
    db = Path(db_path)
    if not db.is_file():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out: list[dict] = []
    try:
        conds = {r["condition_id"] for r in con.execute(
            "SELECT DISTINCT o.condition_id FROM fills f "
            "JOIN orders o ON f.order_uuid = o.id")}
        for cid in conds:
            sides = {r["side"]: r["token_id"] for r in con.execute(
                "SELECT side, token_id FROM quotes WHERE condition_id = ? "
                "AND side IN ('UP','DOWN') GROUP BY side", (cid,))}
            if len(sides) != 2:
                continue
            from engine.order_registry import inventory_from_registry
            inv = inventory_from_registry(cid, sides["UP"], sides["DOWN"],
                                          db_path=db)
            pc = inv.pair_cost()
            if pc >= cap:
                out.append({"condition_id": cid, "pair_cost": round(pc, 4)})
    finally:
        con.close()
    return sorted(out, key=lambda r: r["pair_cost"], reverse=True)


class GuardrailWatch:
    """Dedupes and reports violations. One alert per violation, re-armed when
    it clears (over-cap) or grows (repeat-exit count increasing)."""

    def __init__(self, alerts_log: Path | str = DEFAULT_ALERTS_LOG,
                 ring_path: Path | str = DEFAULT_RING,
                 db_path: Path | str = DEFAULT_DB,
                 cap: float = 0.995, window_s: float = 900.0):
        self.alerts_log = Path(alerts_log)
        self.ring_path = Path(ring_path)
        self.db_path = Path(db_path)
        self.cap = cap
        self.window_s = window_s
        self._exit_alerted: dict[str, int] = {}   # pair_id -> last alerted count
        self._cap_alerted: set[str] = set()       # condition_ids currently alerted
        self.cycle = 0

    def _alert(self, kind: str, subject: str, detail: str) -> None:
        now_iso = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        line = f"GUARDRAIL ALERT {now_iso} : {kind} {subject} -- {detail}"
        print(line, file=sys.stderr)
        try:
            with open(self.alerts_log, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as exc:
            print(f"guardrail: could not write {self.alerts_log}: {exc}",
                  file=sys.stderr)
        try:
            from engine.cycle_stream import emit
            emit(self.cycle, "settling", "guardrail_alert",
                 reason=kind, extra={"subject": subject, "detail": detail},
                 ring_path=self.ring_path)
        except Exception as exc:
            print(f"guardrail: ring emit failed: {exc}", file=sys.stderr)

    def check(self) -> None:
        self.cycle += 1
        now = time.time()

        from engine.cycle_stream import read_ring
        events = read_ring(self.ring_path, tail=0)
        for hit in detect_repeat_exits(events, self.window_s, now):
            pid = hit["pair_id"]
            if hit["count"] > self._exit_alerted.get(pid, 0):
                self._exit_alerted[pid] = hit["count"]
                self._alert(
                    "REPEAT-EXIT", f"pair {pid}",
                    f"exited {hit['count']}x within {int(self.window_s)}s -- "
                    f"the repeat-sell bug's signature; STOP and check the "
                    f"registry/venue")

        for hit in detect_over_cap_pairs(self.db_path, self.cap):
            cid = hit["condition_id"]
            if cid not in self._cap_alerted:
                self._cap_alerted.add(cid)
                self._alert(
                    "OVER-CAP-PAIR", cid[:12],
                    f"filled pair cost ${hit['pair_cost']:.4f} >= cap "
                    f"${self.cap:.3f} -- booked loss on an instrument that "
                    f"pays $1.00")

        # Re-arm over-cap alerts once the condition clears.
        current = {h["condition_id"] for h in
                   detect_over_cap_pairs(self.db_path, self.cap)}
        self._cap_alerted &= current


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval", type=float, default=5.0,
                    help="seconds between checks (default 5)")
    ap.add_argument("--window", type=float, default=900.0,
                    help="repeat-exit window in seconds (default 900)")
    ap.add_argument("--cap", type=float, default=0.995,
                    help="pair-cost alert threshold (default 0.995)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--ring", default=str(DEFAULT_RING))
    ap.add_argument("--alerts-log", default=str(DEFAULT_ALERTS_LOG))
    ap.add_argument("--once", action="store_true",
                    help="run a single check and exit (cron/tests)")
    args = ap.parse_args()

    w = GuardrailWatch(alerts_log=args.alerts_log, ring_path=args.ring,
                       db_path=args.db, cap=args.cap, window_s=args.window)
    while True:
        try:
            w.check()
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"guardrail: check failed: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("guardrail: stopped", file=sys.stderr)
