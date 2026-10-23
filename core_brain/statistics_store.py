from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from core_brain.shadow_guard import assert_not_production_registry

# Snapshot retention: the observer writes one row per tick; without a cap a
# long rehearsal accumulates thousands of rows. The trend value of a snapshot
# decays fast — recent ticks matter, old ticks are redundant. Keep the most
# recent MAX_SNAPSHOT_ROWS rows and compact (delete + VACUUM) periodically so
# the stats DB stays a few MB regardless of run length.
MAX_SNAPSHOT_ROWS = 2000
COMPACT_EVERY = 500  # compact after this many new rows since last compaction

# KPI keys that are full drilldowns (per-market/per-event detail). They dominate
# the payload (~700KB of a ~725KB snapshot once a run has activity) and add no
# trend value when repeated every 5s — the final statistics report regenerates
# them from the registry anyway. Dropped from stored snapshots; the final
# report still embeds the complete report.
_SLIMMED_KPI_KEYS = frozenset({
    "by_market",
    "settlements",
    "float_marks",
    "account_series",
    "equity_series",
})


class StatisticsStore:
    def __init__(self, path: Path):
        self.path = path
        # Rows pruned since the last VACUUM. Once the store is at capacity every
        # append prunes, so gating on this (not on total row count) keeps a
        # long overnight observer from VACUUMing on every single tick.
        self._pruned_since_compact = 0

    @classmethod
    def create(cls, data_dir: Path | str, timestamp: str, run_id: str) -> "StatisticsStore":
        data = Path(data_dir)
        if data.name == "orders.db":
            raise ValueError("production registry cannot be used as a statistics store")
        # `<kind>_<identity>_<stamp>.db`, the state grammar in docs/agents/naming.md.
        # The `stats_` prefix is load-bearing: data_retention.py classifies the
        # rehearsal family by it.
        path = data / f"stats_{_sanitize(run_id)}_{timestamp}.db"
        assert_not_production_registry(path)
        data.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    ts REAL NOT NULL,
                    mode TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    kpi_json TEXT NOT NULL,
                    gate_rows_json TEXT NOT NULL
                )
            """)
        return cls(path)

    def append_snapshot(
        self,
        mode: str,
        run_id: str,
        verdict: str,
        kpi: dict[str, Any],
        gate_rows: list[dict[str, Any]],
    ) -> None:
        slimmed = _slim_kpi(kpi)
        with sqlite3.connect(self.path) as con:
            con.execute(
                "INSERT INTO snapshots VALUES (strftime('%s','now'), ?, ?, ?, ?, ?)",
                (mode, run_id, verdict, json.dumps(slimmed, default=str), json.dumps(gate_rows, default=str)),
            )
            self._retain(con)

    def _retain(self, con: sqlite3.Connection) -> None:
        """Cap snapshot count and periodically reclaim disk space.

        The trend value of a snapshot decays fast — recent ticks matter, old
        ticks are redundant. Without a cap a long rehearsal accumulates
        thousands of half-megabyte rows and the stats DB grows unbounded.
        """
        excess = con.execute(
            "SELECT COUNT(*) FROM snapshots WHERE rowid NOT IN "
            "(SELECT rowid FROM snapshots ORDER BY ts DESC, rowid DESC LIMIT ?)",
            (MAX_SNAPSHOT_ROWS,),
        ).fetchone()[0]
        if excess > 0:
            con.execute(
                "DELETE FROM snapshots WHERE rowid NOT IN "
                "(SELECT rowid FROM snapshots ORDER BY ts DESC, rowid DESC LIMIT ?)",
                (MAX_SNAPSHOT_ROWS,),
            )
            self._pruned_since_compact += excess
            if self._pruned_since_compact >= COMPACT_EVERY:
                # VACUUM cannot run inside a transaction: flush the pending
                # insert first (the outer `with` would otherwise commit it
                # after VACUUM).
                con.commit()
                con.execute("VACUUM")
                self._pruned_since_compact = 0


def _slim_kpi(kpi: dict[str, Any]) -> dict[str, Any]:
    """Drop full drilldowns from a KPI report before storing a snapshot.

    The observer's trend value lives in the scalar metrics; the per-market
    drilldown (`by_market`) and per-item series dominate the payload (~700KB
    of a ~725KB snapshot once a run has activity) and add no trend value when
    repeated every 5s. The final statistics report regenerates them from the
    registry, so dropping them from stored snapshots loses nothing.
    """
    return {k: v for k, v in kpi.items() if k not in _SLIMMED_KPI_KEYS}


def _sanitize(run_id: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(run_id)).strip("._")
    return token or "run"
