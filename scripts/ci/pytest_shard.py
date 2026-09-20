"""Deterministic duration-balanced pytest sharding for CI.

Splits every ``tests/**/test_*.py`` file into N shards so parallel CI jobs
finish at roughly the same time. Assignment is greedy largest-first over the
checked-in per-file durations (``shard_durations.json``, regenerated with
``python -m pytest -q --junitxml=...`` whenever the suite shape changes).

Self-maintaining by construction: the file list is globbed at runtime, so a
new test file is always assigned somewhere. Files missing from the durations
table get the median weight and land in the currently smallest shard; their
names go to stderr so the log shows the table needs a refresh.

Usage (CI runs under ``shell: bash`` on every OS)::

    python -m pytest -q $(python scripts/ci/pytest_shard.py --shard 1 --of 4)

``--shard all`` prints nothing, so the same workflow step runs the full suite.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TESTS_ROOT = REPO_ROOT / "tests"
DURATIONS_FILE = Path(__file__).resolve().parent / "shard_durations.json"


def discover_files() -> list[str]:
    return sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for p in TESTS_ROOT.rglob("test_*.py")
        if p.is_file()
    )


def load_durations() -> dict[str, float]:
    try:
        return {str(k): float(v) for k, v in json.loads(DURATIONS_FILE.read_text()).items()}
    except (OSError, ValueError):
        return {}


def partition(files: list[str], durations: dict[str, float], shards: int) -> list[list[str]]:
    median = statistics.median(durations.values()) if durations else 1.0
    weights = {f: durations.get(f, median) for f in files}
    unknown = sorted(f for f in files if f not in durations)
    if unknown:
        print(f"pytest_shard: {len(unknown)} file(s) missing from durations table: "
              f"{', '.join(unknown)}", file=sys.stderr)
    buckets: list[list[str]] = [[] for _ in range(shards)]
    totals = [0.0] * shards
    for f in sorted(files, key=lambda f: (-weights[f], f)):
        i = totals.index(min(totals))
        buckets[i].append(f)
        totals[i] += weights[f]
    return [sorted(b) for b in buckets]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", required=True,
                        help="1-based shard index, or 'all' for the full suite")
    parser.add_argument("--of", type=int, default=4, help="total shard count")
    args = parser.parse_args(argv)

    files = discover_files()
    if args.shard == "all":
        return 0
    index = int(args.shard) - 1
    if not 0 <= index < args.of:
        parser.error(f"--shard must be 1..{args.of} or 'all'")
    buckets = partition(files, load_durations(), args.of)
    print(" ".join(buckets[index]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
