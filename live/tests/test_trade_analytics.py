"""Tests for per-trade analytics and risk factors on the live dashboard.

The Level 1 section used to answer only "did we make money in aggregate".
That hides the question a maker actually needs: *what is the distribution of
per-position outcomes, and is the expectancy positive with confidence?*

Journeys under test:
1. A closed position records its net gain/loss and return % in the trade
   distribution ($5 commit returning $6.50 is a +$1.50 / +30% row).
2. Win rate carries a Wilson 95% interval; expectancy carries a 90% lower
   bound -- the same "is it positive" gate the simulation uses.
3. Sharpe, Sortino, risk:reward, and profit factor are per-trade and NULL when
   unmeasurable, never a fabricated zero.
4. Max drawdown reads the run-level equity curve; inventory risk reads the
   largest naked float mark.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from engine import kpi as kpi_mod
from engine.kpi import compute_trade_analytics, report
from engine.order_registry import SCHEMA, CloseRecord, OrderRegistry

RUN = "run-trade-analytics"


@pytest.fixture
def temp_db(tmp_path):
    """A temporary registry database on the real schema."""
    db_file = tmp_path / "live.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return db_file


def _close(**kw) -> CloseRecord:
    base = dict(
        ts=time.time() - 600,
        condition_id="0xmarket_a",
        market_slug="market-a",
        method="merge",
        shares=5.0,
        cost_basis=5.00,
        proceeds=6.50,
        realized_pnl=1.50,
        run_id=RUN,
    )
    base.update(kw)
    return CloseRecord(**base)


# --------------------------------------------------------------------------
# Journey 1: the trade distribution records net gain/loss and return %
# --------------------------------------------------------------------------

def test_trade_distribution_records_dollar_gain_and_return_pct():
    """$5 committed returning $6.50 is a +$1.50 row at +30%."""
    closes = [
        dict(realized_pnl=1.50, cost_basis=5.00, ts=100.0, market_slug="a"),
        dict(realized_pnl=-1.00, cost_basis=4.00, ts=200.0, market_slug="b"),
    ]
    ta = compute_trade_analytics(closes, starting_capital=100.0,
                                 equity_series=[], float_marks=[])

    dist = ta["pnl_distribution"]
    assert len(dist) == 2
    assert dist[0]["realized_pnl"] == pytest.approx(1.50)
    assert dist[0]["cost_basis"] == pytest.approx(5.00)
    assert dist[0]["return_pct"] == pytest.approx(30.0)
    assert dist[1]["realized_pnl"] == pytest.approx(-1.00)
    assert dist[1]["return_pct"] == pytest.approx(-25.0)


def test_pnl_distribution_is_ordered_by_timestamp():
    """Rows sort oldest first regardless of insertion order."""
    closes = [
        dict(realized_pnl=2.0, cost_basis=5.0, ts=300.0, market_slug="late"),
        dict(realized_pnl=1.0, cost_basis=5.0, ts=100.0, market_slug="early"),
    ]
    ta = compute_trade_analytics(closes, starting_capital=100.0,
                                 equity_series=[], float_marks=[])
    assert [d["market_slug"] for d in ta["pnl_distribution"]] == ["early", "late"]


def test_close_without_cost_basis_still_counts_dollars_but_not_return_pct():
    """A close with no cost basis has an unmeasurable return, not a fake 0%."""
    closes = [dict(realized_pnl=1.50, cost_basis=None, ts=100.0)]
    ta = compute_trade_analytics(closes, starting_capital=100.0,
                                 equity_series=[], float_marks=[])
    assert ta["expectancy_usd"] == pytest.approx(1.50)
    assert ta["pnl_distribution"][0]["return_pct"] is None
    assert ta["mean_return_pct"] is None


# --------------------------------------------------------------------------
# Journey 2: win rate CI and expectancy CI
# --------------------------------------------------------------------------

def test_win_rate_with_wilson_interval():
    """2 wins in 3 closes -> 66.7%, with a Wilson interval inside [0, 1]."""
    closes = [
        dict(realized_pnl=1.0, cost_basis=5.0, ts=100.0),
        dict(realized_pnl=1.0, cost_basis=5.0, ts=200.0),
        dict(realized_pnl=-1.0, cost_basis=5.0, ts=300.0),
    ]
    ta = compute_trade_analytics(closes, starting_capital=100.0,
                                 equity_series=[], float_marks=[])
    assert ta["wins"] == 2
    assert ta["losses"] == 1
    assert ta["win_rate"] == pytest.approx(2 / 3)
    ci = ta["win_rate_ci95"]
    assert 0.0 <= ci["lower"] <= ci["upper"] <= 1.0
    # The point estimate sits inside its own interval.
    assert ci["lower"] <= ta["win_rate"] <= ci["upper"]


def test_win_rate_ci_is_null_when_there_are_no_closes():
    ta = compute_trade_analytics([], starting_capital=100.0,
                                 equity_series=[], float_marks=[])
    assert ta["win_rate"] is None
    assert ta["win_rate_ci95"] is None
    assert ta["expectancy_usd"] is None


def test_expectancy_ci_uses_90_percent_one_sided_lower_bound():
    """The lower bound is mean - 1.645*se, the simulation's positivity gate."""
    closes = [
        dict(realized_pnl=1.5, cost_basis=5.0, ts=100.0),   # +30%
        dict(realized_pnl=0.5, cost_basis=5.0, ts=200.0),   # +10%
        dict(realized_pnl=-0.5, cost_basis=5.0, ts=300.0),  # -10%
    ]
    ta = compute_trade_analytics(closes, starting_capital=100.0,
                                 equity_series=[], float_marks=[])
    mean = ta["mean_return_pct"]
    stdev = ta["stdev_return_pct"]
    se = stdev / (3 ** 0.5)
    assert ta["ci90_lower_pct"] == pytest.approx(mean - 1.645 * se)
    assert ta["ci95_return_pct"]["lower"] == pytest.approx(mean - 1.96 * se)
    assert ta["ci95_return_pct"]["upper"] == pytest.approx(mean + 1.96 * se)


def test_single_close_has_no_ci_or_sharpe():
    """One trade measures a mean but no dispersion -- CI and Sharpe stay NULL."""
    closes = [dict(realized_pnl=1.5, cost_basis=5.0, ts=100.0)]
    ta = compute_trade_analytics(closes, starting_capital=100.0,
                                 equity_series=[], float_marks=[])
    assert ta["mean_return_pct"] == pytest.approx(30.0)
    assert ta["stdev_return_pct"] is None
    assert ta["ci90_lower_pct"] is None
    assert ta["sharpe_ratio"] is None
    assert ta["sortino_ratio"] is None


# --------------------------------------------------------------------------
# Journey 3: risk-adjusted factors
# --------------------------------------------------------------------------

def test_sharpe_and_sortino_are_per_trade_mean_over_dispersion():
    closes = [
        dict(realized_pnl=1.5, cost_basis=5.0, ts=100.0),   # +30%
        dict(realized_pnl=0.5, cost_basis=5.0, ts=200.0),   # +10%
        dict(realized_pnl=-0.5, cost_basis=5.0, ts=300.0),  # -10%
    ]
    ta = compute_trade_analytics(closes, starting_capital=100.0,
                                 equity_series=[], float_marks=[])
    assert ta["sharpe_ratio"] == pytest.approx(
        ta["mean_return_pct"] / ta["stdev_return_pct"])
    # Sortino divides by downside deviation only.
    assert ta["sortino_ratio"] > ta["sharpe_ratio"]


def test_risk_reward_and_profit_factor():
    """R:R = avg win / |avg loss|; profit factor = gross wins / |gross losses|."""
    closes = [
        dict(realized_pnl=3.0, cost_basis=5.0, ts=100.0),
        dict(realized_pnl=1.0, cost_basis=5.0, ts=200.0),   # avg win = 2.0
        dict(realized_pnl=-4.0, cost_basis=5.0, ts=300.0),  # one loss of 4.0
    ]
    ta = compute_trade_analytics(closes, starting_capital=100.0,
                                 equity_series=[], float_marks=[])
    assert ta["avg_win_usd"] == pytest.approx(2.0)
    assert ta["avg_loss_usd"] == pytest.approx(-4.0)
    assert ta["risk_reward_ratio"] == pytest.approx(0.5)
    assert ta["profit_factor"] == pytest.approx(4.0 / 4.0)


def test_all_win_run_has_no_risk_reward_denominator():
    """No loss to measure against -> R:R and profit factor are NULL (UI: infinity)."""
    closes = [
        dict(realized_pnl=1.0, cost_basis=5.0, ts=100.0),
        dict(realized_pnl=2.0, cost_basis=5.0, ts=200.0),
    ]
    ta = compute_trade_analytics(closes, starting_capital=100.0,
                                 equity_series=[], float_marks=[])
    assert ta["risk_reward_ratio"] is None
    assert ta["profit_factor"] is None
    assert ta["avg_loss_usd"] is None


# --------------------------------------------------------------------------
# Journey 4: max drawdown and inventory risk
# --------------------------------------------------------------------------

def test_max_drawdown_reads_the_equity_curve():
    """A dip then recovery produces the correct peak-to-trough drawdown."""
    equity_series = [
        {"ts": 100.0, "v": 110.0},   # peak 110
        {"ts": 200.0, "v": 90.0},    # -20 drawdown
        {"ts": 300.0, "v": 120.0},   # new peak
    ]
    ta = compute_trade_analytics([], starting_capital=100.0,
                                 equity_series=equity_series, float_marks=[])
    assert ta["max_drawdown_usd"] == pytest.approx(20.0)
    assert ta["max_drawdown_pct"] == pytest.approx(100.0 * 20.0 / 110.0)


def test_no_equity_curve_means_no_drawdown():
    ta = compute_trade_analytics([], starting_capital=100.0,
                                 equity_series=[], float_marks=[])
    assert ta["max_drawdown_usd"] is None
    assert ta["max_drawdown_pct"] is None


def test_max_naked_exposure_reads_float_marks():
    float_marks = [
        {"ts": 100.0, "naked_usd": 1.0},
        {"ts": 200.0, "naked_usd": 4.5},
        {"ts": 300.0, "naked_usd": 2.0},
    ]
    ta = compute_trade_analytics([], starting_capital=100.0,
                                 equity_series=[], float_marks=float_marks)
    assert ta["max_naked_exposure_usd"] == pytest.approx(4.5)


# --------------------------------------------------------------------------
# Journey 5: wired into the live report
# --------------------------------------------------------------------------

def test_report_exposes_trade_analytics(temp_db, tmp_path, monkeypatch):
    """The dashboard's /api/kpi payload carries the new block."""
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    reg = OrderRegistry(temp_db)
    t0 = time.time() - 600
    reg.log_close(_close(ts=t0 + 60, realized_pnl=1.50, cost_basis=5.00))
    reg.log_close(_close(ts=t0 + 120, condition_id="0xmarket_b",
                         market_slug="market-b", realized_pnl=-1.00,
                         cost_basis=4.00))

    data = report(db_path=temp_db, run_id=RUN)
    ta = data["trade_analytics"]

    assert ta["n_closes"] == 2
    assert ta["wins"] == 1
    assert ta["losses"] == 1
    assert ta["win_rate"] == pytest.approx(0.5)
    assert ta["expectancy_usd"] == pytest.approx(0.25)
    assert [d["market_slug"] for d in ta["pnl_distribution"]] == \
        ["market-a", "market-b"]
    # The legacy outcome keys stay intact for anything already reading them.
    assert data["win_rate"] == pytest.approx(0.5)
    assert data["avg_win"] == pytest.approx(1.50)
    assert data["avg_loss"] == pytest.approx(-1.00)
