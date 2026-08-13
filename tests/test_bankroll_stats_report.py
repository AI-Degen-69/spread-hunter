"""Unit tests for bankroll analysis report generator."""
import os
import json
import sqlite3
from pathlib import Path
from scripts.bankroll_stats_report import evaluate_bankroll_tier, generate_summary_report

def test_evaluate_bankroll_tier(tmp_path):
    tier_dir = tmp_path / "bankroll_100"
    tier_dir.mkdir(parents=True, exist_ok=True)
    db_path = tier_dir / "fleet.db"
    
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE settled (pnl_usd REAL)")
    # Insert 35 positive trades to meet sample size and pass 95% CI gate
    for _ in range(35):
        conn.execute("INSERT INTO settled VALUES (2.5)")
    conn.commit()
    conn.close()

    res = evaluate_bankroll_tier(bankroll=100, workdir=tier_dir)
    assert res["bankroll"] == 100
    assert res["sample_count"] == 35
    assert res["is_invalid"] is False
    assert res["ci_95"]["lower"] > 0.0

def test_evaluate_bankroll_invalidation(tmp_path):
    tier_dir = tmp_path / "bankroll_200"
    tier_dir.mkdir(parents=True, exist_ok=True)
    db_path = tier_dir / "fleet.db"
    
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE settled (pnl_usd REAL)")
    # Insert 35 negative trades to force 95% CI upper bound < 0
    for _ in range(35):
        conn.execute("INSERT INTO settled VALUES (-5.0)")
    conn.commit()
    conn.close()

    res = evaluate_bankroll_tier(bankroll=200, workdir=tier_dir)
    assert res["bankroll"] == 200
    assert res["is_invalid"] is True
    assert "95% CI upper bound < 0%" in res["invalidation_reasons"]

def test_evaluate_bankroll_db_error(tmp_path):
    tier_dir = tmp_path / "bankroll_300"
    tier_dir.mkdir(parents=True, exist_ok=True)
    db_path = tier_dir / "fleet.db"
    # Write invalid non-SQLite content to trigger SQLite error
    db_path.write_text("corrupted sqlite file content")

    res = evaluate_bankroll_tier(bankroll=300, workdir=tier_dir)
    assert res["bankroll"] == 300
    assert res["is_invalid"] is True
    assert any("Database error" in reason for reason in res["invalidation_reasons"])

def test_generate_summary_report(tmp_path):
    for amt in range(100, 1001, 100):
        tdir = tmp_path / f"bankroll_{amt}"
        tdir.mkdir(parents=True, exist_ok=True)
    reports = generate_summary_report(run_dir=tmp_path)
    assert len(reports) == 10
    assert reports[0]["bankroll"] == 100
    assert reports[-1]["bankroll"] == 1000

