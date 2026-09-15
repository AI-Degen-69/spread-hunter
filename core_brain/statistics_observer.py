from __future__ import annotations

import argparse
import datetime
import time
from pathlib import Path
from types import SimpleNamespace

from core_brain.kpi import report as kpi_report
from core_brain.order_registry import OrderRegistry
from core_brain.statistics_report import (DEFAULT_REPORT_DIR,
                                          write_statistics_report)
from core_brain.statistics_store import StatisticsStore
from core_brain.shadow_guard import assert_not_production_registry


def observe(
    watch: Path | str,
    run_id: str,
    mode: str,
    data_dir: Path | str,
    *,
    ticks: int | None = None,
    interval: float = 5.0,
    max_hours: float | None = None,
    stop_file: Path | str | None = None,
):
    if mode not in {"shadow", "live"}:
        raise ValueError("mode must be shadow or live")
    watch_path = Path(watch)
    if mode == "shadow":
        assert_not_production_registry(watch_path)
    store = StatisticsStore.create(data_dir, datetime.datetime.now().strftime("%d-%m_%H-%M"), run_id)
    started = time.monotonic()
    count = 0
    try:
        while ticks is None or count < ticks:
            if stop_file is not None and Path(stop_file).exists():
                break
            kpi = kpi_report(watch_path, run_id=run_id)
            registry = OrderRegistry(watch_path)
            closes = [c for c in registry.get_all_closes() if c.get("run_id") == run_id]
            store.append_snapshot(mode, run_id, "OBSERVING", kpi, [])
            count += 1
            if max_hours is not None and time.monotonic() - started >= max_hours * 3600:
                break
            if interval:
                time.sleep(interval)
    finally:
        write_statistics_report(watch_path, run_id, mode, DEFAULT_REPORT_DIR)
    return SimpleNamespace(count=count, stats_path=store.path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("shadow", "live"), required=True)
    parser.add_argument("--watch", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--max-hours", type=float, default=24.0)
    parser.add_argument("--stop-file", type=str, default=None)
    parser.add_argument("--data-dir", type=str, default="data")
    args = parser.parse_args(argv)
    observe(args.watch, args.run_id, args.mode, args.data_dir, interval=args.interval, max_hours=args.max_hours, stop_file=args.stop_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
