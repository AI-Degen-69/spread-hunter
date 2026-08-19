"""The dashboard smoke-test fixture must exercise every Level 1 widget.

`live/scripts/seed_preview_fixture.py` is what the owner points `--db` at to
eyeball the live monitor. This test pins what that fixture must produce: a
run whose report has real closes (win rate/expectancy/distribution/risk) AND
real markouts (the adverse-selection bell curve), not n=0.
"""
from __future__ import annotations

import sqlite3

import pytest

from engine.kpi import report
from engine.order_registry import SCHEMA, OrderRegistry
from scripts.seed_preview_fixture import RUN_ID, seed


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "live.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return db_file


def test_fixture_exercises_the_adverse_selection_bell_curve(temp_db):
    """4 matured markouts over 4 fills produce a non-NULL size-weighted drift."""
    reg = OrderRegistry(temp_db)
    seed(reg)

    data = report(db_path=temp_db, run_id=RUN_ID)

    assert data["markout_samples"] == 4
    # Three of four fills drift against us; one drifts favourably.
    # Drifts: -0.02, -0.01, -0.02, +0.01 at 5 shares each over 20 filled sh.
    assert data["adverse_selection"] is not None
    assert data["adverse_selection"] == pytest.approx(-0.01)
    assert data["filled_shares"] == pytest.approx(20.0)


def test_fixture_exercises_the_closes_based_tiles(temp_db):
    """The same fixture feeds win rate, distribution, and risk factors."""
    reg = OrderRegistry(temp_db)
    seed(reg)

    ta = report(db_path=temp_db, run_id=RUN_ID)["trade_analytics"]

    assert ta["n_closes"] == 8
    assert ta["wins"] == 5
    assert ta["losses"] == 3
    assert ta["win_rate"] == pytest.approx(5 / 8)
    # Dollar expectancy is positive, mean return % negative (the -100% trade).
    assert ta["expectancy_usd"] == pytest.approx(0.20 / 8)
    assert ta["mean_return_pct"] < 0
    assert ta["sharpe_ratio"] is not None
    assert ta["max_drawdown_usd"] is not None
    assert ta["max_naked_exposure_usd"] == pytest.approx(3.20)
