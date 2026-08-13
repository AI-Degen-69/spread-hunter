"""Launch 10 simultaneous paper-trading bots with isolated workdirs ($100 to $1,000 in $100 steps)."""
import os
import sys
import json
import time
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def build_bankroll_configs(start: int = 100, end: int = 1000, step: int = 100) -> list[dict]:
    """Generate directory structures and bankroll allocations for experiment tiers."""
    configs = []
    for amount in range(start, end + 1, step):
        workdir = ROOT / "run" / f"bankroll_{amount}"
        configs.append({
            "bankroll": amount,
            "workdir": workdir,
            "db_path": workdir / "fleet.db",
            "status_path": workdir / "status.json",
        })
    return configs

def setup_experiment_dirs(configs: list[dict]):
    """Initialize directories and status tracking files for each bankroll tier."""
    for cfg in configs:
        cfg["workdir"].mkdir(parents=True, exist_ok=True)
        status_path = cfg.get("status_path", cfg["workdir"] / "status.json")
        status_data = {
            "bankroll": cfg["bankroll"],
            "status": "INITIALIZED",
            "created_at": time.time(),
            "target_samples": 100,
            "min_samples": 30,
        }
        status_path.write_text(json.dumps(status_data, indent=2))

def launch_tier_process(cfg: dict, dry_run: bool = False) -> subprocess.Popen | None:
    """Launch strategy process for a single bankroll tier in an isolated workdir."""
    env = os.environ.copy()
    env["SPREAD_HUNTER_DB"] = str(cfg["db_path"])
    env["SPREAD_HUNTER_BANKROLL"] = str(cfg["bankroll"])

    cmd = [sys.executable, "-m", "strategy.fleet"]
    if dry_run:
        print(f"[DRY RUN] Would execute: {' '.join(cmd)} (Workdir: {cfg['workdir']})")
        return None

    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc

def main():
    dry_run = "--dry-run" in sys.argv
    configs = build_bankroll_configs()
    setup_experiment_dirs(configs)
    print(f"Initialized {len(configs)} bankroll experiment directories.")
    for cfg in configs:
        print(f" - Tier ${cfg['bankroll']}: {cfg['workdir']}")

    if dry_run:
        print("Dry run complete. No processes launched.")
        return

    procs = []
    for cfg in configs:
        proc = launch_tier_process(cfg)
        if proc:
            procs.append((cfg["bankroll"], proc))
            print(f"Launched PID {proc.pid} for Bankroll ${cfg['bankroll']}")

    print(f"Successfully launched {len(procs)} concurrent bankroll experiment instances.")

if __name__ == "__main__":
    main()
