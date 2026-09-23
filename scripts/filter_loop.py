"""Re-run the Market Filter on a fixed interval, forever.

Paths are relative to this repo: the log lands in runtime/rerank.log, the cycle ring
lives in runtime/, and the filter writes runtime/markets.json for the Trader to adopt.
The scoring rules it leans on live in scoring/.

The Trader adopts runtime/markets.json every `rerank_interval_sec`, but
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

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "runtime" / "rerank.log"

# Capture original OS environment override before any load_dotenv mutation.
_ORIGINAL_ENV_TOP = os.environ.get("SH_TOP_MARKETS")

# Load .env before any SH_* parsing at import time: values that live only in
# the file (not the process environment) would otherwise fall back to the
# hardcoded defaults, and a 600s test default would hide the mis-load.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except Exception:
    pass

# The live cycle-telemetry ring (core_brain/cycle_stream.py). This script
# APPENDS only, deliberately without importing core_brain.cycle_stream: it runs
# from the repo root and must stay decoupled from the execution package.
# Rotation of the ring is owned by the Query Polymarket process (Q3).
RING_PATH = ROOT / "runtime" / "cycle_events.jsonl"

# How often to regenerate runtime/markets.json. The fleet adopts the file within
# a second of its mtime changing, so this is the whole "how fast do new
# markets appear" budget. 3600 was the original: a universe that emptied at
# 09:58 left the fleet quoting nothing until the next hourly sweep found
# nothing new. 600 (10 min) keeps the venue scoring (a full pass over ~200
# candidates) from becoming a burden while cutting the worst-case wait from an
# hour to ten minutes.
def _get_top_markets() -> int:
    # Re-read each cycle so an operator can change .env without restarting the
    # loop. Parse the file directly with dotenv_values() so the current .env
    # is always observed, while an explicitly supplied process env var wins.
    try:
        from dotenv import dotenv_values
        file_vals = dotenv_values(ROOT / ".env")
        if _ORIGINAL_ENV_TOP is not None and str(_ORIGINAL_ENV_TOP).strip() != "":
            raw_top = str(_ORIGINAL_ENV_TOP).strip()
        else:
            raw_top = str(file_vals.get("SH_TOP_MARKETS", "2") or "2").strip()
        val = int(raw_top)
        return val if val > 0 else 2
    except Exception:
        # Fallback: honour process env, else default.
        try:
            if _ORIGINAL_ENV_TOP is not None and str(_ORIGINAL_ENV_TOP).strip() != "":
                raw_top = str(_ORIGINAL_ENV_TOP).strip()
            else:
                raw_top = "2"
            val = int(raw_top)
            return val if val > 0 else 2
        except Exception:
            return 2

raw_interval = os.environ.get("SH_FILTER_INTERVAL_SEC", "600").strip()
try:
    INTERVAL_SEC = int(raw_interval)
    if INTERVAL_SEC <= 0:
        raise ValueError(f"SH_FILTER_INTERVAL_SEC must be positive, got {INTERVAL_SEC}")
except Exception as e:
    raise ValueError(f"Invalid SH_FILTER_INTERVAL_SEC {raw_interval!r}: {e}") from e


def _emit_scan_event(record: dict) -> None:
    """Inline NDJSON append to the live cycle ring. Never raises.

    Same schema as core_brain.cycle_stream.emit, `service="filter"`. Appends go
    through one os.write on an O_APPEND fd, which lands at EOF as a single
    syscall -- a plain open("a") seek-then-write loses a line on Windows when
    two writers hit the same end offset. Query Polymarket owns rotation.
    """
    try:
        RING_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(record, default=str) + "\n").encode("utf-8")
        fd = os.open(RING_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
    except Exception:
        pass


def parse_args(argv=None) -> argparse.Namespace:
    """Loop-level flags. Both reroute one trial loop; the baseline loop runs
    bare and its command is byte-identical to before."""
    p = argparse.ArgumentParser(
        prog="python -m scripts.filter_loop",
        description="Re-run the market filter every SH_FILTER_INTERVAL_SEC.")
    p.add_argument("--out-dir", default=None, metavar="DIR",
                   help="refresh the ranker into DIR and write rerank.log "
                        "and cycle_events.jsonl there instead of runtime/ "
                        "(trial loop, e.g. runtime/trials/<run-id>/)")
    p.add_argument("--trial-depth", type=float, default=None, metavar="USD",
                   help="forward this top-3 bid-depth bar to the ranker; wins "
                        "over the configured HUNTER_DEPTH_TRIAL_USD")
    return p.parse_args(argv)


def _loop_paths(out_dir=None) -> tuple[Path, Path]:
    """Where this loop writes its log and ring: the shared runtime/ by
    default, the trial output directory when rerouted."""
    if out_dir is None:
        return LOG, RING_PATH
    base = Path(out_dir)
    return base / "rerank.log", base / "cycle_events.jsonl"


def _rank_cmd(top: int = 2, out_dir=None,
              trial_depth: float | None = None) -> list[str]:
    """The ranker invocation, with any staged gate trials from config appended.

    The depth trial (U32) and the volume trial (U36) stay opt-in: when
    `select_min_top3_depth_usd_trial` / `select_min_volume_24h_usd_trial` are
    set (env HUNTER_DEPTH_TRIAL_USD / HUNTER_VOLUME_TRIAL_USD, or a config
    default), the loop passes the bar through so adopted markets are tagged
    `trial_depth_usd` / `trial_volume_usd` and their markouts are the decision
    evidence. Config is read fresh at process start, so flipping a trial on is
    a restart of this one process away -- no fleet restart needed.
    """
    cmd = [sys.executable, "-m", "scripts.filter_markets", "--top", str(top)]
    if out_dir is not None:
        cmd += ["--out-dir", str(out_dir)]
    try:
        from scoring.config import load as _load_cfg
        cfg = _load_cfg()
        if trial_depth is not None:
            cmd += ["--trial-depth", str(trial_depth)]
        elif cfg.select_min_top3_depth_usd_trial:
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


def main(argv=None) -> None:
    global LOG, RING_PATH
    args = parse_args(argv)
    LOG, RING_PATH = _loop_paths(args.out_dir)
    LOG.parent.mkdir(parents=True, exist_ok=True)
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
            top_n = _get_top_markets()
            r = subprocess.run(
                _rank_cmd(top_n, out_dir=args.out_dir,
                          trial_depth=args.trial_depth),
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
                "service": "filter", "cycle": cycle, "phase": "scanning",
                "action": "rerank_error", "market_slug": "", "reason": err,
                "latency_ms": round((time.time() - t0) * 1000.0, 2),
                "pid": os.getpid(), "extra": {},
            })
        else:
            _emit_scan_event({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "service": "filter", "cycle": cycle, "phase": "scanning",
                "action": "rerank_done", "market_slug": "", "reason": "",
                "latency_ms": round((time.time() - t0) * 1000.0, 2),
                "pid": os.getpid(),
                "extra": {"exit_code": r.returncode},
            })
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    main()
