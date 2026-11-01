from __future__ import annotations

import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from scripts.reconcile_tournament_stack import (
    sweep_stale_orders,
    is_port_listening,
    ensure_dashboard,
    reconcile_arms,
    reconcile,
    ROOT,
)


def test_sweep_stale_orders_purges_matching_orders(tmp_path):
    db_file = tmp_path / "test_arm.db"
    con = sqlite3.connect(str(db_file))
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            condition_id TEXT,
            status TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            size REAL
        )
    """)
    # Insert clean order
    cur.execute("INSERT INTO orders (order_id, condition_id, status) VALUES ('clean-1', 'active-market-1', 'FILLED')")
    cur.execute("INSERT INTO fills (order_id, size) VALUES ('clean-1', 10.0)")
    # Insert stale order
    cur.execute("INSERT INTO orders (order_id, condition_id, status) VALUES ('stale-1', 'bra-san-bah-2026-10-24-bah', 'FILLED')")
    cur.execute("INSERT INTO fills (order_id, size) VALUES ('stale-1', 10.0)")
    con.commit()
    con.close()

    removed = sweep_stale_orders(db_file, ["bra-san-bah-2026-10-24%"])
    assert removed == 1

    con2 = sqlite3.connect(str(db_file))
    orders = con2.execute("SELECT order_id FROM orders").fetchall()
    fills = con2.execute("SELECT order_id FROM fills").fetchall()
    con2.close()

    assert len(orders) == 1
    assert orders[0][0] == "clean-1"
    assert len(fills) == 1
    assert fills[0][0] == "clean-1"


def test_sweep_stale_orders_on_clean_db(tmp_path):
    db_file = tmp_path / "clean_arm.db"
    con = sqlite3.connect(str(db_file))
    cur = con.cursor()
    cur.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, order_id TEXT, condition_id TEXT)")
    cur.execute("INSERT INTO orders (order_id, condition_id) VALUES ('ord-1', 'valid-market')")
    con.commit()
    con.close()

    removed = sweep_stale_orders(db_file, ["bra-san-bah-2026-10-24%"])
    assert removed == 0


def test_sweep_without_a_named_slug_deletes_nothing(tmp_path):
    """The reconciler must never purge a market the operator did not name."""
    db_file = tmp_path / "arm.db"
    con = sqlite3.connect(str(db_file))
    con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, order_id TEXT, condition_id TEXT)")
    con.execute("INSERT INTO orders (order_id, condition_id) VALUES ('ord-1', 'bra-san-bah-2026-10-24-bah')")
    con.commit()
    con.close()

    assert sweep_stale_orders(db_file, []) == 0
    assert sweep_stale_orders(db_file, ["some-other-market%"]) == 0

    con = sqlite3.connect(str(db_file))
    assert con.execute("SELECT count(*) FROM orders").fetchone()[0] == 1
    con.close()


def test_ensure_dashboard_when_listening():
    """If dashboard is already listening, ensure_dashboard returns True immediately."""
    with patch("scripts.reconcile_tournament_stack.is_port_listening", return_value=True):
        with patch("subprocess.Popen") as mock_popen:
            assert ensure_dashboard(8799) is True
            mock_popen.assert_not_called()


def test_ensure_dashboard_starts_when_not_listening():
    """If dashboard is not listening, ensure_dashboard launches dashboard.server."""
    listening_sequence = [False, True]

    def mock_listening(port, **kwargs):
        return listening_sequence.pop(0) if listening_sequence else True

    with patch("scripts.reconcile_tournament_stack.is_port_listening", side_effect=mock_listening):
        with patch("subprocess.Popen") as mock_popen:
            assert ensure_dashboard(8799) is True
            mock_popen.assert_called_once()
            args = mock_popen.call_args[0][0]
            assert "dashboard.server" in args
            assert "--port" in args
            assert "8799" in args


def test_reconcile_arms_uncapped_omits_max_markets_flag(tmp_path):
    """When max_markets is None or not provided, shadow_run is invoked without --max-markets (uncapped)."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)
    db_path = tmp_path / "arm-01.db"
    arms = [{
        "index": 1,
        "name": "control",
        "db_path": str(db_path),
        "run_id": "test-arm-01",
        "dash_port": 8801,
        "env": {},
    }]
    with patch("subprocess.Popen") as mock_popen:
        with patch("scripts.reconcile_tournament_stack.clear_instance_lock"):
            reconcile_arms(arms=arms, logs_dir=logs_dir, max_markets=None, minutes=1440.0)
            assert mock_popen.called
            cmd = mock_popen.call_args[0][0]
            assert "--max-markets" not in cmd
            assert "--db" in cmd
            assert str(db_path) in cmd


def test_reconcile_arms_capped_passes_flag(tmp_path):
    """When max_markets is given as an int, shadow_run is invoked with --max-markets <N>."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)
    db_path = tmp_path / "arm-01.db"
    arms = [{
        "index": 1,
        "name": "control",
        "db_path": str(db_path),
        "run_id": "test-arm-01",
        "dash_port": 8801,
        "env": {},
    }]
    with patch("subprocess.Popen") as mock_popen:
        with patch("scripts.reconcile_tournament_stack.clear_instance_lock"):
            reconcile_arms(arms=arms, logs_dir=logs_dir, max_markets=5, minutes=1440.0)
            assert mock_popen.called
            cmd = mock_popen.call_args[0][0]
            assert "--max-markets" in cmd
            idx = cmd.index("--max-markets")
            assert cmd[idx + 1] == "5"


@pytest.mark.parametrize("tourney_id,expected_arm_count", [
    ("799", 10),
    ("843", 4),
    ("905", 5),
    ("i843", 4),
    ("#905", 5),
])
def test_reconcile_finds_configured_arms_and_initializes_plan(tmp_path, tourney_id, expected_arm_count):
    """Reconcile discovers the correct config file for each tournament ID and initializes the plan (uncapped by default)."""
    tourn_dir = tmp_path / "tournaments"
    tourn_dir.mkdir(parents=True)

    with patch("scripts.reconcile_tournament_stack.ROOT", ROOT):
        with patch("core_brain.shadow_run.TOURNAMENT_ROOT", tourn_dir):
            with patch("scripts.reconcile_tournament_stack.ensure_filter_loop", return_value=True):
                with patch("scripts.reconcile_tournament_stack.ensure_dashboard", return_value=True):
                    with patch("scripts.reconcile_tournament_stack.reconcile_arms") as mock_arms:
                        with patch("scripts.reconcile_tournament_stack.time.sleep"):
                            with patch("scripts.reconcile_tournament_stack.verify_and_report", return_value=True):
                                # First run creates the plan uncapped by default
                                exit_code = reconcile(tourney_id=tourney_id, minutes=1440.0, base_dir=tourn_dir)
                                assert exit_code == 0
                                assert mock_arms.called
                                arms_passed = mock_arms.call_args[0][0]
                                assert len(arms_passed) == expected_arm_count
                                # max_markets is None (uncapped)
                                assert mock_arms.call_args[1].get("max_markets") is None

                                # Second run finds the existing plan and resumes it
                                mock_arms.reset_mock()
                                exit_code_resume = reconcile(tourney_id=tourney_id, minutes=1440.0, base_dir=tourn_dir)
                                assert exit_code_resume == 0
                                assert mock_arms.called
                                resumed_arms = mock_arms.call_args[0][0]
                                assert len(resumed_arms) == expected_arm_count
