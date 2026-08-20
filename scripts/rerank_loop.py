"""Re-run the ranker on a fixed interval, forever.

`strategy.fleet` adopts run/markets.json every `rerank_interval_sec`, but
nothing regenerates that file -- and the U6 universe is short-dated by
construction, so every market in it resolves within a day. Left alone, the
fleet re-reads the same file until its whole universe has settled and then
quotes nothing while looking perfectly healthy.

A separate process rather than a thread inside the fleet: re-ranking scores
hundreds of books over the network, and a stall there must not stall the
trading loop. That is the same argument `fleet.py` already makes for keeping
the scoring out of the sweep.

Failures are logged and slept through, never raised. A ranker that fails at
03:00 because the venue returned a 502 must not leave the fleet with no
refresh for the rest of the night.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "rerank.log"

# The live cycle-telemetry ring (live/engine/cycle_stream.py). This script
# APPENDS only, deliberately without importing engine.cycle_stream: it runs
# from the repo root and must stay decoupled from live/'s package (AGENTS.md
# isolation). Rotation of the ring is owned by the engine process (Q3).
RING_PATH = ROOT / "live" / "run" / "cycle_events.jsonl"

# How often to regenerate run/markets.json. The fleet adopts the file within
# a second of its mtime changing, so this is the whole "how fast do new
# markets appear" budget. 3600 was the original: a universe that emptied at
# 09:58 left the fleet quoting nothing until the next hourly sweep found
# nothing new. 600 (10 min) keeps the venue scoring (a full pass over ~200
# candidates) from becoming a burden while cutting the worst-case wait from an
# hour to ten minutes.
INTERVAL_SEC = 600.0
TOP = 20


def _emit_scan_event(record: dict) -> None:
    """Inline NDJSON append to the live cycle ring. Never raises.

    Three lines, same schema as engine.cycle_stream.emit, `service="screener"`.
    Append-only: the engine owns rotation, so a screener append can never race
    a rewrite of the file (Q3).
    """
    try:
        RING_PATH.parent.mkdir(parents=True, exist_ok=True)
        with RING_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


def _rank_cmd(top: int = TOP) -> list[str]:
    """The ranker invocation, with any staged gate trials from config appended.

    The depth trial (U32) and the volume trial (U36) stay opt-in: when
    `select_min_top3_depth_usd_trial` / `select_min_volume_24h_usd_trial` are
    set (env HUNTER_DEPTH_TRIAL_USD / HUNTER_VOLUME_TRIAL_USD, or a config
    default), the loop passes the bar through so adopted markets are tagged
    `trial_depth_usd` / `trial_volume_usd` and their markouts are the decision
    evidence. Config is read fresh at process start, so flipping a trial on is
    a restart of this one process away -- no fleet restart needed.
    """
    cmd = [sys.executable, "-m", "scripts.rank_markets", "--top", str(top)]
    try:
        from strategy.config import load as _load_cfg
        cfg = _load_cfg()
        if cfg.select_min_top3_depth_usd_trial:
            cmd += ["--trial-depth", str(cfg.select_min_top3_depth_usd_trial)]
        if cfg.select_min_volume_24h_usd_trial:
            cmd += ["--trial-volume", str(cfg.select_min_volume_24h_usd_trial)]
    except Exception as e:
        # A config read failure must not stop the loop -- but a silently
        # disabled trial would be an unmonitored gate change, so say so.
        try:
            with LOG.open("a", encoding="utf-8", errors="replace") as fh:
                fh.write(time.strftime("%Y-%m-%d %H:%M:%S")
                         + f" WARN: gate trials disabled by config-read "
                           f"failure: {type(e).__name__}: {e}\n")
        except Exception:
            pass
    return cmd


def main() -> None:
    LOG.parent.mkdir(exist_ok=True)
    cycle = 0
    while True:
        # Rank FIRST, then sleep. Sleeping first left a newly started fleet
        # quoting whatever markets.json happened to be on disk for a full
        # hour -- and fleet-start.ps1 starts the supervisor before this process,
        # so that stale universe is exactly what it picks up.
        cycle += 1
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        t0 = time.time()
        try:
            r = subprocess.run(
                _rank_cmd(TOP),
                cwd=str(ROOT), capture_output=True, text=True, timeout=600)
            out = r.stdout or ""
            err = "" if r.returncode == 0 else f"\nEXIT {r.returncode}\n{r.stderr}"
        except Exception as e:                      # network, timeout, anything
            out, err = "", f"\nFAILED: {type(e).__name__}: {e}"
        with LOG.open("a", encoding="utf-8", errors="replace") as f:
            f.write(f"\n===== {stamp} =====\n{out}{err}")
        if err:
            _emit_scan_event({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "service": "screener", "cycle": cycle, "phase": "scanning",
                "action": "rerank_error", "market_slug": "", "reason": err,
                "latency_ms": round((time.time() - t0) * 1000.0, 2),
                "pid": os.getpid(), "extra": {},
            })
        else:
            _emit_scan_event({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "service": "screener", "cycle": cycle, "phase": "scanning",
                "action": "rerank_done", "market_slug": "", "reason": "",
                "latency_ms": round((time.time() - t0) * 1000.0, 2),
                "pid": os.getpid(),
                "extra": {"exit_code": r.returncode},
            })
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    main()
