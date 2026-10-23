"""Fill quality: which fills become dead legs, and why (#761).

Per-fill attribution joined to the book state at fill time and the terminal
outcome of the pair, calibrated with honest intervals, gated behind a
default-off switch, and judged on inclusive PnL.

READ-ONLY, BY CONSTRUCTION. The input opens through `file:<abs>?mode=ro` with
`uri=True`; `OrderRegistry` is never constructed on an input path (its
constructor creates and migrates tables, which against a production registry
would rewrite it); no transaction is ever opened. A query that fails is
reported as UNREADABLE, never repaired.

The module imports nothing from `core_brain`, which is what makes that claim
auditable in one glance. The queue bucket boundaries are COPIED from `kpi.py`
rather than imported for the same reason.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

#: Load statuses. `NO_DATA` and `UNREADABLE` are the `run_scorer` vocabulary;
#: `INSUFFICIENT` is a bucket status, not a load status.
OK = "OK"
NO_DATA = "NO_DATA"
UNREADABLE = "UNREADABLE"
INSUFFICIENT = "insufficient"

SIGN_RULE = "death rate = dead legs / closed fills; lift = bucket / base"
DEFAULT_MIN_OBS = 30


@dataclass(frozen=True)
class StoreLoad:
    """One input file, read once. `status` is OK / NO_DATA / UNREADABLE."""

    label: str
    path: str
    run_id: str | None
    status: str
    detail: str


@dataclass(frozen=True)
class QualityReport:
    """Skeleton report. Fill rows and calibration land in Tasks 2-3."""

    store: StoreLoad
    min_obs: int

    def to_dict(self) -> dict:
        return {
            "store": {
                "label": self.store.label,
                "path": self.store.path,
                "run_id": self.store.run_id,
                "status": self.store.status,
                "detail": self.store.detail,
            },
            "min_obs": self.min_obs,
            "fills": [],
            "buckets": [],
        }


def load_store(
    path: Path | str,
    *,
    run_id: str | None = None,
    label: str = "shadow",
) -> StoreLoad:
    """Read one store's cohort. Never writes, never creates, never repairs."""
    resolved = Path(path)
    if not resolved.is_file():
        return StoreLoad(
            label=label, path=str(resolved), run_id=run_id,
            status=NO_DATA, detail="file not found; nothing to read",
        )

    # `as_uri()` percent-escapes reserved characters. A raw `file:<path>?mode=ro`
    # URI is cut at a `#`, so a store named `a#b.db` opened `a` instead -- and
    # created it, read-write, because the `?mode=ro` then belonged to the
    # truncated path. `?` cannot exist in a Windows filename; `#` can.
    uri = f"{resolved.resolve().as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        return StoreLoad(
            label=label, path=str(resolved), run_id=run_id,
            status=UNREADABLE, detail=str(exc) or exc.__class__.__name__,
        )
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name IN ('orders', 'fills')"
        ).fetchall()
    except sqlite3.Error as exc:
        return StoreLoad(
            label=label, path=str(resolved), run_id=run_id,
            status=UNREADABLE, detail=str(exc) or exc.__class__.__name__,
        )
    finally:
        conn.close()
    return StoreLoad(
        label=label, path=str(resolved), run_id=run_id,
        status=OK, detail="readable; fill rows land in Task 2",
    )


def build_report(store: StoreLoad, *, min_obs: int = DEFAULT_MIN_OBS) -> QualityReport:
    """Assemble the report from an already-loaded store. Pure: no I/O."""
    return QualityReport(store=store, min_obs=min_obs)


def render_text(report: QualityReport) -> str:
    lines = [
        "Fill quality report (#761): which fills become dead legs, and why.",
        f"  store: {report.store.label} {report.store.path}",
        f"  status: {report.store.status} ({report.store.detail})",
        f"  min_obs: {report.min_obs} (buckets below this are not-a-finding)",
        f"  {SIGN_RULE}",
        f"  {NO_DATA} / {UNREADABLE} inputs are reported, never repaired.",
    ]
    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m core_brain.fill_quality_report",
        description=(
            "Attribute every fill to its book state and terminal outcome."
            " Read-only: the database is opened with mode=ro."
        ),
    )
    parser.add_argument("--shadow-db", required=True, type=Path,
                        help="shadow order registry to read, e.g. data/NN_shadow_<stamp>.db")
    parser.add_argument("--run-id", default=None,
                        help="restrict rows to one run_id")
    parser.add_argument("--min-obs", type=int, default=DEFAULT_MIN_OBS,
                        help=f"observations required before a bucket is a finding"
                             f" (default {DEFAULT_MIN_OBS})")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 only when the input produced a cohort; else 1 (report prints)."""
    args = _build_parser().parse_args(argv)
    if args.min_obs < 1:
        print("--min-obs must be >= 1")
        return 1

    store = load_store(args.shadow_db, run_id=args.run_id)
    report = build_report(store, min_obs=args.min_obs)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, allow_nan=False))
    else:
        print(render_text(report))
    return 0 if store.status == OK else 1


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
