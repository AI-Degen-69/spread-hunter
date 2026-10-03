"""Performance and work-budget regression guards for dashboard poll endpoints (#347).

Asserted against work-counted operations (file reads / parse counts) rather than
wall-clock time, ensuring deterministic and non-flaky execution across CI environments.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

import core_brain.kpi as kpi_module
import core_brain.market_meta as mm
from core_brain.order_registry import SCHEMA
from dashboard import server as srv


@pytest.fixture(autouse=True)
def clean_snapshot_cache(monkeypatch):
    monkeypatch.setattr(srv, "_snapshots", {}, raising=False)
    monkeypatch.setattr(srv, "_snapshot_builders", {}, raising=False)


def test_system_status_and_scan_state_heartbeat_parse_budget(tmp_path, monkeypatch):
    """With 1000+ heartbeat files, status and scan-state endpoints must not parse them all repeatedly."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    db_file = tmp_path / "01_shadow.db"

    now = time.time()

    # Create 1000 old files (older than 6 hours window)
    for i in range(1000):
        hb_file = runtime_dir / f"shadow_run_old_{i:04d}.json"
        hb_file.write_text(
            json.dumps({"run_id": f"old_{i}", "started_at": now - 30000, "heartbeat_ts": now - 30000, "db_path": str(tmp_path / f"old_{i}.db")}),
            encoding="utf-8",
        )
        old_time = now - (8 * 3600)
        os.utime(hb_file, (old_time, old_time))

    # Create 5 recent files including the active run
    active_hb = runtime_dir / "shadow_run_active.json"
    active_hb.write_text(
        json.dumps({"run_id": "active-01", "started_at": now - 60, "heartbeat_ts": now - 1, "db_path": str(db_file), "running": True}),
        encoding="utf-8",
    )

    for i in range(4):
        recent_hb = runtime_dir / f"shadow_run_recent_{i}.json"
        recent_hb.write_text(
            json.dumps({"run_id": f"recent_{i}", "started_at": now - 100, "heartbeat_ts": now - 5, "db_path": str(tmp_path / f"other_{i}.db"), "running": False}),
            encoding="utf-8",
        )

    # Monkeypatch candidates to read from our tmp runtime dir
    monkeypatch.setattr(
        srv,
        "_shadow_heartbeat_candidates",
        lambda: sorted(runtime_dir.glob("shadow_run_*.json")),
    )
    monkeypatch.setattr(srv, "_ACTIVE_DB_OVERRIDE", db_file)

    # Track how many heartbeat files get parsed
    parse_count = 0
    real_read_hb = srv._read_shadow_heartbeat_file

    def counting_read_hb(*args, **kwargs):
        nonlocal parse_count
        parse_count += 1
        return real_read_hb(*args, **kwargs)

    monkeypatch.setattr(srv, "_read_shadow_heartbeat_file", counting_read_hb)

    # Cold calls to prime the snapshot cache for both endpoints
    status1 = srv.get_system_status()
    scan1 = srv.get_scan_state()
    assert status1["shadow_run"] is not None
    assert status1["shadow_run"]["run_id"] == "active-01"

    cold_parses = parse_count
    # Should only parse recent files (<= 20), skipping the 1000 stale ones
    assert cold_parses <= 20, f"Expected <= 20 parses on cold status/scan, got {cold_parses}"

    # Warm calls: 5 subsequent status calls and scan-state calls within TTL
    for _ in range(5):
        srv.get_system_status()
        srv.get_scan_state()

    # Zero additional parses should occur because results are served from snapshot cache
    assert parse_count == cold_parses, (
        f"Warm calls performed {parse_count - cold_parses} unexpected heartbeat parses"
    )


def test_kpi_feed_read_budget_across_many_condition_ids(tmp_path, monkeypatch):
    """KPI report generation must read feed files once, not once per condition id."""
    db_file = tmp_path / "kpi_test.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)

    now = time.time()
    now_ms = int(now * 1000)

    # Populate 30 closed pairs across 30 distinct condition ids
    for i in range(30):
        cid = f"0xcid_{i:04d}"
        con.execute(
            "INSERT INTO orders (id, order_id, condition_id, token_id, side, price, original_size, status, posted_ts, last_polled_ts, run_id) "
            "VALUES (?, ?, ?, 'tok1', 'BUY', 0.49, 10.0, 'filled', ?, ?, 'test_run')",
            (f"ord_up_{i}", f"venue_up_{i}", cid, now_ms, now_ms),
        )
        con.execute(
            "INSERT INTO orders (id, order_id, condition_id, token_id, side, price, original_size, status, posted_ts, last_polled_ts, run_id) "
            "VALUES (?, ?, ?, 'tok2', 'BUY', 0.49, 10.0, 'filled', ?, ?, 'test_run')",
            (f"ord_dn_{i}", f"venue_dn_{i}", cid, now_ms, now_ms),
        )
        con.execute(
            "INSERT INTO closes (ts, condition_id, market_slug, method, shares, up_price, dn_price, cost_basis, proceeds, fee, realized_pnl, forgone_vs_settlement, up_cost_removed, dn_cost_removed, run_id) "
            "VALUES (?, ?, ?, 'merge', 10.0, 0.49, 0.49, 9.8, 10.0, 0.0, 0.2, 0.0, 4.9, 4.9, 'test_run')",
            (now, cid, f"market-slug-{i}"),
        )
    con.commit()
    con.close()

    # Write runtime feed with corresponding rows
    feed_rows = [
        {
            "cid": f"0xcid_{i:04d}",
            "slug": f"market-slug-{i}",
            "title": f"Market {i}",
            "category": "Crypto",
            "series_title": "",
            "market_group": "",
            "tags": [],
            "volume_24h": 100.0,
        }
        for i in range(30)
    ]
    run_dir = tmp_path / "runtime"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "markets.json").write_text(json.dumps(feed_rows), encoding="utf-8")

    # Track Path.read_text calls within market_meta module
    real_read = mm.Path.read_text
    read_count = 0

    def counting_read(self, *args, **kwargs):
        nonlocal read_count
        read_count += 1
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(mm.Path, "read_text", counting_read)
    monkeypatch.setattr(kpi_module, "REPO_ROOT", tmp_path)

    # Run KPI report
    report = kpi_module.report(str(db_file), run_id="test_run")
    assert report is not None
    assert len(report.get("by_market", {})) == 30

    # Feed must be read once for markets.json, not 30+ times
    assert read_count == 1, f"Expected 1 feed read across 30 condition IDs, got {read_count}"
