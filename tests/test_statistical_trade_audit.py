"""Focused tests for scripts/statistical_trade_audit.py (#542).

T1 — the proportion guard: impossible success counts are refused by name
(`None`), never a `math domain error`, and both proportion call sites print
the named unavailable line instead of an x/y fraction.
"""

import sqlite3

import pytest

from core_brain.registry_state import MERGE_CLOSE_METHODS as REGISTRY_MERGE_METHODS
from scripts import statistical_trade_audit as audit
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


# --- T2: merges are counted from the close record, never from leg rows ---


def _write_merge_fixture_db(path, closes_rows, n_legs, with_method=True):
    conn = sqlite3.connect(path)
    try:
        schema = "(realized_pnl REAL, exit_reason TEXT"
        schema += ", method TEXT" if with_method else ""
        schema += ", hold_duration_sec REAL)"
        conn.execute(f"CREATE TABLE closes {schema}")
        conn.executemany(
            f"INSERT INTO closes VALUES ({', '.join('?' * len(closes_rows[0]))})",
            closes_rows,
        )
        conn.execute("CREATE TABLE single_leg_lifecycle (id INTEGER)")
        conn.execute("CREATE TABLE shadow_merge_legs (id INTEGER)")
        conn.executemany(
            "INSERT INTO shadow_merge_legs VALUES (?)", [(i,) for i in range(n_legs)]
        )
        conn.commit()
    finally:
        conn.close()


def test_merge_rate_counts_close_methods_not_leg_rows(tmp_path, capsys):
    db = tmp_path / "t2_mixed.db"
    _write_merge_fixture_db(
        str(db),
        [
            (1.0, "merge", "shadow_merge", 10.0),
            (2.0, "lifecycle_hard_stop", "single_buy_exit", 20.0),
            (3.0, "sell", "sell", 30.0),
        ],
        n_legs=5,
    )
    run_analysis(str(db))
    out = capsys.readouterr().out
    assert "Merged Positions: 1/3 (33.33%)" in out
    assert "Stop Loss Exits: 1/3 (33.33%)" in out
    assert "PNL EXPECTANCY" in out
    assert "PROPORTIONS" in out
    assert "POSITION HOLDING TIME" in out
    assert "SAMPLE SIZE" in out


def test_merge_rate_zero_when_no_close_merged(tmp_path, capsys):
    db = tmp_path / "t2_nomerge.db"
    _write_merge_fixture_db(
        str(db),
        [
            (1.0, "sell", "sell", 10.0),
            (2.0, "sell", "sell", 20.0),
        ],
        n_legs=4,
    )
    run_analysis(str(db))
    out = capsys.readouterr().out
    assert "Merged Positions: 0/2 (0.00%)" in out


def test_merge_rate_unavailable_without_method_column(tmp_path, capsys):
    db = tmp_path / "t2_nomethod.db"
    _write_merge_fixture_db(
        str(db),
        [
            (1.0, "sell", 10.0),
            (2.0, "sell", 20.0),
        ],
        n_legs=3,
        with_method=False,
    )
    run_analysis(str(db))
    out = capsys.readouterr().out
    assert "merge rate unavailable from this database" in out
    assert "Merged Positions: 3/" not in out


def test_merge_methods_pinned_to_registry():
    assert audit.MERGE_CLOSE_METHODS == REGISTRY_MERGE_METHODS


# --- T3: a missing table is named with the database path, never a traceback ---


def test_missing_merge_legs_table_named_with_path(tmp_path, capsys):
    db = tmp_path / "t3_nolegs.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("CREATE TABLE closes (realized_pnl REAL, method TEXT)")
        conn.execute("CREATE TABLE single_leg_lifecycle (id INTEGER)")
        conn.commit()
    finally:
        conn.close()
    run_analysis(str(db))  # must not raise OperationalError
    out = capsys.readouterr().out
    assert "shadow_merge_legs" in out
    assert str(db) in out


def test_empty_database_names_closes_with_path(tmp_path, capsys):
    db = tmp_path / "t3_empty.db"
    sqlite3.connect(str(db)).close()
    run_analysis(str(db))  # must not raise OperationalError
    out = capsys.readouterr().out
    assert "closes" in out
    assert str(db) in out
