"""Continuous log streamer for tournament supervision.

Tails the active tournament background task log into data/tournament_supervision.log
so operator commands like 'Get-Content -Tail 20 data/tournament_supervision.log'
always see live execution activity.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent.parent
TARGET_LOG = ROOT / "data" / "tournament_supervision.log"


def find_source_log() -> Path | None:
    # 1. Search task logs under .system_generated/tasks/
    task_logs = list(Path(os.environ.get("USERPROFILE", "")).glob(
        ".gemini/antigravity/brain/*/.system_generated/tasks/task-1503.log"
    ))
    if task_logs:
        return task_logs[0]
    
    # Fallback to any recent shadow_tournament task log
    candidates = list(Path(os.environ.get("USERPROFILE", "")).glob(
        ".gemini/antigravity/brain/*/.system_generated/tasks/task-*.log"
    ))
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for c in candidates[:10]:
        try:
            with open(c, "r", encoding="utf-8", errors="ignore") as f:
                head = f.read(500)
                if "shadow_tournament" in head or "cycle=" in head:
                    return c
        except Exception:
            continue
    return None


def stream_log() -> None:
    TARGET_LOG.parent.mkdir(parents=True, exist_ok=True)
    src = find_source_log()
    if not src or not src.exists():
        print(f"No source tournament log found yet.")
        return

    print(f"Streaming from {src} -> {TARGET_LOG}")
    with open(src, "r", encoding="utf-8", errors="ignore") as f_in, \
         open(TARGET_LOG, "a", encoding="utf-8", buffering=1) as f_out:
        # Seek near end if target is already partially populated
        f_in.seek(0, os.SEEK_END)
        cur_pos = max(0, f_in.tell() - 65536)  # Grab last 64KB first
        f_in.seek(cur_pos)
        initial_chunk = f_in.read()
        if initial_chunk:
            f_out.write(initial_chunk)
            f_out.flush()

        while True:
            line = f_in.readline()
            if line:
                f_out.write(line)
                f_out.flush()
            else:
                time.sleep(0.5)


if __name__ == "__main__":
    stream_log()
