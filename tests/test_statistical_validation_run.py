"""Tests for the statistical validation harness (Issue #52).

Verifies safe read-only execution, hybrid stopping rules, SQLite closes counting,
environment snapshotting, and banner logging without touching the live venue.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
import pytest

from core_brain.config import load as load_cfg
from core_brain.order_registry import DEFAULT_DB_PATH, OrderRegistry, init_db
from core_brain.quotes import QuoteIntent
from core_brain.shadow_guard import ShadowSafetyViolation
from core_brain.shadow_run import _Deadline
from statistical_validation_run.run import (
    count_closes,
    main,
    make_hybrid_deadline_sleep,
    snapshot_stat_validation_env,
)


class FakeMarket:
    def __init__(self, cid="0xabc"):
        self.condition_id = cid
        self.up_token = "tok-up"
        self.down_token = "tok-dn"
        self.market_slug = "fake-market"
        self.tick_size = 0.01
        self.neg_risk = False


def _books(clob_host, token):
    return {
        "token_id": token,
        "best_bid": 0.47,
        "best_ask": 0.49,
        "bids": {0.47: 100},
        "asks": {0.49: 100},
    }


class TestSafety:
    """AGENTS.md: data/orders.db is production registry; refuse it outright."""

    def test_production_registry_is_refused(self):
        with pytest.raises(ShadowSafetyViolation, match="production registry"):
            main(["--db", str(DEFAULT_DB_PATH), "--max-hours", "0.0"])


class TestCountCloses:
    """Helper count_closes queries SQLite closes table safely."""

    def test_returns_zero_when_file_missing(self, tmp_path):
        assert count_closes(tmp_path / "nonexistent.db") == 0

    def test_returns_zero_when_table_missing(self, tmp_path):
        db = tmp_path / "empty.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE foo (id INT)")
        con.commit()
        con.close()
        assert count_closes(db) == 0

    def test_returns_count_scoped_by_run_id(self, tmp_path):
        db = tmp_path / "test.db"
        con = sqlite3.connect(str(db))
        con.execute(
            "CREATE TABLE closes (id TEXT PRIMARY KEY, run_id TEXT, realized_pnl REAL)"
        )
        con.execute("INSERT INTO closes VALUES ('1', 'shadow-aaa', 0.5)")
        con.execute("INSERT INTO closes VALUES ('2', 'shadow-aaa', -0.2)")
        con.execute("INSERT INTO closes VALUES ('3', 'shadow-bbb', 0.1)")
        con.commit()
        con.close()

        assert count_closes(db, run_id="shadow-aaa") == 2
        assert count_closes(db, run_id="shadow-bbb") == 1
        assert count_closes(db, run_id="shadow-ccc") == 0
        assert count_closes(db) == 3

    def test_returns_zero_when_run_id_column_missing_for_scoped_query(self, tmp_path):
        db = tmp_path / "test_no_col.db"
        con = sqlite3.connect(str(db))
        con.execute(
            "CREATE TABLE closes (id TEXT PRIMARY KEY, realized_pnl REAL)"
        )
        con.execute("INSERT INTO closes VALUES ('1', 0.5)")
        con.commit()
        con.close()

        assert count_closes(db, run_id="shadow-aaa") == 0
        assert count_closes(db) == 1


class TestHybridDeadlineSleep:
    """Stops when target closes reached or wall clock deadline expires."""

    def test_stops_when_target_closes_reached(self):
        closes_box = [0]
        slept = []

        sleep_fn = make_hybrid_deadline_sleep(
            deadline_ts=1000.0 + 100.0,
            count_closes_fn=lambda: closes_box[0],
            target_closes=5,
            clock=lambda: 1000.0,
            sleep=slept.append,
        )

        sleep_fn(5.0)
        assert slept == [5.0]

        closes_box[0] = 5
        with pytest.raises(_Deadline):
            sleep_fn(5.0)

    def test_stops_when_deadline_passes(self):
        slept = []
        sleep_fn = make_hybrid_deadline_sleep(
            deadline_ts=1010.0,
            count_closes_fn=lambda: 0,
            target_closes=10,
            clock=lambda: 1015.0,
            sleep=slept.append,
        )

        with pytest.raises(_Deadline):
            sleep_fn(5.0)

    def test_sleep_is_clamped_to_remaining_time(self):
        slept = []
        sleep_fn = make_hybrid_deadline_sleep(
            deadline_ts=1003.0,
            count_closes_fn=lambda: 0,
            target_closes=10,
            clock=lambda: 1000.0,
            sleep=slept.append,
        )

        sleep_fn(5.0)
        assert slept == [3.0]

    def test_hybrid_sleep_requires_all_criteria_to_stop_early(self):
        closes_box = [0]
        markouts_box = [0]
        now_box = [1000.0]
        slept = []

        sleep_fn = make_hybrid_deadline_sleep(
            deadline_ts=1000.0 + 3600.0,
            count_closes_fn=lambda: closes_box[0],
            target_closes=50,
            count_markouts_fn=lambda: markouts_box[0],
            min_markouts=25,
            start_ts=1000.0,
            min_seconds=300.0,
            clock=lambda: now_box[0],
            sleep=slept.append,
        )

        # 1. Neither met, min time not reached -> sleeps
        sleep_fn(5.0)
        assert slept == [5.0]

        # 2. Closes met (50), but markouts not met (10), min time reached (400s) -> sleeps
        closes_box[0] = 50
        markouts_box[0] = 10
        now_box[0] = 1400.0
        sleep_fn(5.0)
        assert slept == [5.0, 5.0]

        # 3. Markouts met (25), but closes not met (40), min time reached -> sleeps
        closes_box[0] = 40
        markouts_box[0] = 25
        sleep_fn(5.0)
        assert slept == [5.0, 5.0, 5.0]

        # 4. Both closes and markouts met, but min time NOT reached (100s < 300s) -> sleeps
        closes_box[0] = 50
        markouts_box[0] = 25
        now_box[0] = 1100.0
        sleep_fn(5.0)
        assert slept == [5.0, 5.0, 5.0, 5.0]

        # 5. Closes met (50), markouts met (25), min time reached (400s >= 300s) -> STOPS!
        now_box[0] = 1400.0
        with pytest.raises(_Deadline):
            sleep_fn(5.0)


class TestMaturityGate:
    """Immature markouts report insufficient_sample rather than 0.0 drift."""

    def test_immature_run_reports_insufficient_sample_not_zero_drift(self, tmp_path):
        from core_brain.markout import fleet_stats
        from statistical_validation_run.run import count_matured_markouts
        db = tmp_path / "fleet_test.db"
        init_db(db)
        reg = OrderRegistry(db_path=db)

        # Insert 24 markouts (under the 25 threshold)
        with reg._conn() as conn:
            for i in range(24):
                conn.execute(
                    """INSERT INTO markouts (ts, ref_mid, ref_mid_source, mid_h0, size, run_id)
                       VALUES (?, ?, 'clean', ?, 1.0, 'shadow-run')""",
                    (100.0 + i, 0.50, 0.52),
                )
            conn.commit()

        # Direct count check for 24 markouts
        assert count_matured_markouts(db, run_id="shadow-run") == 24

        stats = fleet_stats(reg, min_sample=25)
        assert stats["verdict"] == "insufficient_sample"
        assert stats["mean_per_share"] is None

    def test_mature_run_passes_sample_threshold(self, tmp_path):
        from core_brain.markout import fleet_stats
        from statistical_validation_run.run import count_matured_markouts
        db = tmp_path / "fleet_test_mature.db"
        init_db(db)
        reg = OrderRegistry(db_path=db)

        # Insert 25 matured markouts (exact boundary)
        with reg._conn() as conn:
            for i in range(25):
                conn.execute(
                    """INSERT INTO markouts (ts, ref_mid, ref_mid_source, mid_h0, size, run_id)
                       VALUES (?, ?, 'clean', ?, 1.0, 'shadow-run')""",
                    (100.0 + i, 0.50, 0.52),
                )
            conn.commit()

        # Direct count check for 25 markouts
        assert count_matured_markouts(db, run_id="shadow-run") == 25

        stats = fleet_stats(reg, min_sample=25)
        assert stats["verdict"] in ("earning", "losing")
        assert stats["mean_per_share"] is not None
        assert stats["mean_per_share"] == pytest.approx(0.02)


class TestCountMaturedMarkouts:
    """Helper count_matured_markouts queries SQLite markouts table safely."""

    def test_returns_zero_when_file_missing(self, tmp_path):
        from statistical_validation_run.run import count_matured_markouts
        assert count_matured_markouts(tmp_path / "nonexistent.db") == 0

    def test_returns_zero_when_table_missing(self, tmp_path):
        from statistical_validation_run.run import count_matured_markouts
        db = tmp_path / "empty.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE foo (id INT)")
        con.commit()
        con.close()
        assert count_matured_markouts(db) == 0

    def test_counts_only_clean_matured_markouts(self, tmp_path):
        from statistical_validation_run.run import count_matured_markouts
        db = tmp_path / "markouts_test.db"
        con = sqlite3.connect(str(db))
        con.execute(
            """CREATE TABLE markouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                ref_mid REAL,
                ref_mid_source TEXT DEFAULT 'contaminated',
                mid_h0 REAL,
                mid_h1 REAL,
                mid_h2 REAL,
                mid_h3 REAL,
                run_id TEXT NOT NULL
            )"""
        )
        # Row 1: clean, mid_h0 measured -> matured
        con.execute(
            "INSERT INTO markouts (ts, ref_mid, ref_mid_source, mid_h0, run_id) VALUES (1.0, 0.50, 'clean', 0.51, 'shadow-1')"
        )
        # Row 2: clean, mid_h1 measured -> matured
        con.execute(
            "INSERT INTO markouts (ts, ref_mid, ref_mid_source, mid_h1, run_id) VALUES (2.0, 0.50, 'clean', 0.49, 'shadow-1')"
        )
        # Row 3: contaminated -> NOT counted
        con.execute(
            "INSERT INTO markouts (ts, ref_mid, ref_mid_source, mid_h0, run_id) VALUES (3.0, 0.50, 'contaminated', 0.52, 'shadow-1')"
        )
        # Row 4: clean, but no mid_h0..h3 measured yet (pending) -> NOT counted
        con.execute(
            "INSERT INTO markouts (ts, ref_mid, ref_mid_source, run_id) VALUES (4.0, 0.50, 'clean', 'shadow-1')"
        )
        # Row 5: clean, mid_h3 measured for shadow-2
        con.execute(
            "INSERT INTO markouts (ts, ref_mid, ref_mid_source, mid_h3, run_id) VALUES (5.0, 0.50, 'clean', 0.50, 'shadow-2')"
        )
        con.commit()
        con.close()

        assert count_matured_markouts(db, run_id="shadow-1") == 2
        assert count_matured_markouts(db, run_id="shadow-2") == 1
        assert count_matured_markouts(db, run_id="shadow-3") == 0
        assert count_matured_markouts(db) == 3


class TestSnapshot:
    """Snapshots runtime environment and writes MakerConfig as JSON."""

    def test_snapshot_creates_directory_and_writes_config(self, tmp_path, caplog):
        art_dir = tmp_path / "shadow_stat_test"
        cfg = load_cfg()

        with caplog.at_level(logging.INFO, logger="statistical_validation_run"):
            snapshot_stat_validation_env(art_dir, cfg)

        assert art_dir.is_dir()
        # Issue #54: snapshots carry the `_snapshot` suffix so a run's frozen
        # inputs are never confused with the live files under runtime/.
        cfg_file = art_dir / "config_snapshot.json"
        assert cfg_file.is_file()

        data = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert "bankroll_usd" in data
        assert "stat_gate_threshold_pct" in data
        assert "STAT HARNESS config" in caplog.text


class TestEndToEndHarness:
    """Full execution test with injected client and market dependencies."""

    def test_e2e_run_records_banners_telemetry_and_orders(self, tmp_path, caplog, monkeypatch):
        db = tmp_path / "shadow_stat_e2e.db"
        intent = QuoteIntent(
            side="UP",
            token_id="tok-up",
            price=0.48,
            size=2,
            mid=0.49,
            edge_vs_mid=0.01,
        )

        now = [1000.0]

        def clock():
            return now[0]

        def sleep(seconds):
            now[0] += seconds

        with caplog.at_level(logging.INFO):
            rc = main(
                ["--target-closes", "2", "--max-hours", "0.001", "--db", str(db)],
                markets_fn=lambda max_markets=None: [FakeMarket("0xabc")],
                client_fn=lambda: object(),
                decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([intent], ""),
                fetch_books=_books,
                clock=clock,
                sleep=sleep,
            )

        assert rc == 0
        text = caplog.text.lower()
        assert "shadow run" in text
        assert "no signer loaded" in text
        assert str(db).lower() in text

        # Verify DB rows
        reg = OrderRegistry(db_path=db)
        orders = reg.get_all_orders()
        assert len(orders) >= 1
        assert orders[0]["run_id"].startswith("shadow-")

        # Verify ring telemetry file was created
        from core_brain.cycle_stream import LIVE_ROOT
        ring_file = LIVE_ROOT / "runtime" / f"{orders[0]['run_id']}.jsonl"
        assert ring_file.is_file()
        content = ring_file.read_text(encoding="utf-8")
        assert "quoting" in content

    def test_underpowered_run_exits_inconclusive_without_go_verdict(self, tmp_path, caplog):
        """Acceptance Criteria: run with target-closes 999 exits INCONCLUSIVE with underpowered reason."""
        db = tmp_path / "shadow_stat_underpowered.db"
        report_dir = tmp_path / "stat_inconclusive"
        now = [1000.0]

        def clock():
            return now[0]

        def sleep(seconds):
            now[0] += seconds

        with caplog.at_level(logging.INFO):
            rc = main(
                ["--target-closes", "999", "--max-hours", "0.001",
                 "--db", str(db), "--report", str(report_dir)],
                markets_fn=lambda max_markets=None: [FakeMarket("0xabc")],
                client_fn=lambda: object(),
                decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([], ""),
                fetch_books=_books,
                clock=clock,
                sleep=sleep,
            )

        assert rc == 0
        text = caplog.text.lower()
        assert "inconclusive" in text
        assert "underpowered: n < n_min or markouts < 25" in text
        assert "verdict: go" not in text
        assert "verdict: no-go" not in text

        # Issue #54: the full artifact bundle is emitted even for a zero-close
        # run, and the memo verdict agrees with the harness verdict.
        assert (report_dir / "report.json").is_file()
        assert (report_dir / "report.md").is_file()
        assert (report_dir / "closes.csv").is_file()
        assert (report_dir / "fills.csv").is_file()
        assert (report_dir / "quotes.csv").is_file()
        assert (report_dir / "config_snapshot.json").is_file()
        data = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
        assert "trade_analytics" in data
        assert data["stat_validation"]["verdict"] == "INCONCLUSIVE"
        assert "underpowered" in data["stat_validation"]["verdict_reason"]
        md = (report_dir / "report.md").read_text(encoding="utf-8")
        assert "**INCONCLUSIVE**" in md
        assert "Gate table" in md

    def test_zero_fill_minutes_only_run_is_inconclusive_not_go(self, tmp_path, caplog):
        """Acceptance: a minutes-only run with zero fills exits INCONCLUSIVE, not GO.

        No `--target-closes` is passed, so the run is bounded by `--max-hours`
        alone. Zero fills means zero matured markouts, which trips the default
        `min_markouts` floor -> underpowered -> INCONCLUSIVE. The memo must
        never read GO (or NO-GO) on a run that has nothing to judge.
        """
        db = tmp_path / "shadow_stat_minutes_only.db"
        report_dir = tmp_path / "stat_minutes_only"
        now = [1000.0]

        def clock():
            return now[0]

        def sleep(seconds):
            now[0] += seconds

        with caplog.at_level(logging.INFO):
            rc = main(
                ["--max-hours", "0.001", "--db", str(db),
                 "--report", str(report_dir)],
                markets_fn=lambda max_markets=None: [FakeMarket("0xabc")],
                client_fn=lambda: object(),
                decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([], ""),
                fetch_books=_books,
                clock=clock,
                sleep=sleep,
            )

        assert rc == 0
        text = caplog.text.lower()
        assert "inconclusive" in text
        assert "verdict: go" not in text
        assert "verdict: no-go" not in text

        data = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
        assert data["stat_validation"]["verdict"] == "INCONCLUSIVE"
        md = (report_dir / "report.md").read_text(encoding="utf-8")
        assert "**INCONCLUSIVE**" in md
        assert "**GO**" not in md
        assert "**NO-GO**" not in md

    def test_target_closes_defaults_to_config(self, tmp_path, caplog):
        """Without --target-closes the harness gates on the config target.

        shadow-01's ad-hoc report passed with n=38 against no target at all;
        the menu-launched harness must collect stat_gate_target_closes (60)
        closes before its stop rule can fire on sample, so a 'profitable but
        thin' run is reported underpowered instead of GO. Fails before the
        fix (a.target_closes stayed None -> gate reported, never failed).
        """
        from core_brain.config import load as load_cfg

        db = tmp_path / "shadow_stat_cfg_target.db"
        report_dir = tmp_path / "stat_cfg_target"
        target = load_cfg().stat_gate_target_closes
        now = [1000.0]

        def clock():
            return now[0]

        def sleep(seconds):
            now[0] += seconds

        with caplog.at_level(logging.WARNING):
            rc = main(
                ["--max-hours", "0.001", "--db", str(db),
                 "--report", str(report_dir)],
                markets_fn=lambda max_markets=None: [FakeMarket("0xabc")],
                client_fn=lambda: object(),
                decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([], ""),
                fetch_books=_books,
                clock=clock,
                sleep=sleep,
            )

        assert rc == 0
        # The banner records the effective target, and the underpowered
        # reason names the config number, proving the default was applied.
        assert f"target_closes={target}" in caplog.text
        assert f"closes 0 < {target}" in caplog.text
        data = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
        assert data["stat_validation"]["verdict"] == "INCONCLUSIVE"
        assert data["stat_validation"]["run_result"]["target_closes"] == target
