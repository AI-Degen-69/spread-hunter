"""Focused tests for scripts/statistical_trade_audit.py (#542).

T1 — the proportion guard: impossible success counts are refused by name
(`None`), never a `math domain error`, and both proportion call sites print
the named unavailable line instead of an x/y fraction.
"""

import sqlite3

import pytest

from scripts.statistical_trade_audit import (
    _print_proportion_block,
    proportion_confidence_interval,
    run_analysis,
)

Z95 = 1.96


def test_proportion_guard_refuses_impossible_counts():
    assert proportion_confidence_interval(178, 170, Z95) is None
    assert proportion_confidence_interval(-1, 10, Z95) is None
    assert proportion_confidence_interval(0, 0, Z95) is None


def test_proportion_valid_wilson_math_unchanged():
    result = proportion_confidence_interval(53, 170, Z95)
    assert result is not None
    low, high, margin = result
    assert all(isinstance(v, float) for v in result)
    assert 0.0 <= low < 53 / 170 < high <= 1.0
    assert margin == pytest.approx((high - low) / 2.0)


def test_print_block_impossible_count_names_no_fraction(capsys):
    _print_proportion_block("Merged Positions", 5, 3)
    out = capsys.readouterr().out
    assert "Merged Positions: rate unavailable (success count is outside 0..N)" in out
    assert "5/3" not in out


def test_print_block_valid_count_keeps_fraction_and_ci(capsys):
    _print_proportion_block("Stop Loss Exits", 1, 3)
    out = capsys.readouterr().out
    assert "Stop Loss Exits: 1/3 (33.33%)" in out
    assert "[95% CL]:" in out


def _write_fixture_db(path):
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE closes (realized_pnl REAL, exit_reason TEXT, method TEXT)"
        )
        conn.executemany(
            "INSERT INTO closes VALUES (?, ?, ?)",
            [
                (1.0, "stop-loss", "sell"),
                (2.0, "sell", "sell"),
                (3.0, "sell", "sell"),
            ],
        )
        conn.execute("CREATE TABLE single_leg_lifecycle (id INTEGER)")
        conn.execute("CREATE TABLE shadow_merge_legs (id INTEGER)")
        conn.commit()
    finally:
        conn.close()


def test_run_analysis_valid_stop_loss_line_unchanged(tmp_path, capsys):
    db = tmp_path / "t1_valid.db"
    _write_fixture_db(str(db))
    run_analysis(str(db))
    out = capsys.readouterr().out
    assert "Stop Loss Exits: 1/3 (33.33%)" in out
