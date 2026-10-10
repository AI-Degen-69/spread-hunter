"""Tests for required_sample_size_for_mean, Cohen d, proportion, and sample_size_sufficiency payload (#443, #448)."""
from __future__ import annotations

import math
import pytest

from core_brain import kpi as kpi_mod
from core_brain.kpi import (
    KPI_PAYLOAD_VERSION,
    Z_95_SAMPLE_SUFFICIENCY,
    Z_98_SAMPLE_SUFFICIENCY,
    Z_99_SAMPLE_SUFFICIENCY,
    DEFAULT_COHEN_D,
    DEFAULT_PROPORTION_MARGIN,
    required_sample_size_for_mean,
    required_sample_size_cohen_d,
    required_sample_size_proportion,
    compute_trade_analytics,
)


def test_required_sample_size_for_mean_formula_values():
    # Formula: ceil(((z * std) / E) ** 2)
    std = 0.11547
    e = 0.02
    assert required_sample_size_for_mean(std, e, Z_95_SAMPLE_SUFFICIENCY) == 129
    assert required_sample_size_for_mean(std, e, Z_98_SAMPLE_SUFFICIENCY) == 181
    assert required_sample_size_for_mean(std, e, Z_99_SAMPLE_SUFFICIENCY) == 222


def test_required_sample_size_cohen_d_values():
    # Formula: ceil((z / d) ** 2)
    # 95% (z = 1.95996, d = 0.20): (1.95996 / 0.20) ** 2 = 96.036 => 97
    # 98% (z = 2.32635, d = 0.20): (2.32635 / 0.20) ** 2 = 135.30 => 136
    # 99% (z = 2.57583, d = 0.20): (2.57583 / 0.20) ** 2 = 165.87 => 166
    assert required_sample_size_cohen_d(0.20, Z_95_SAMPLE_SUFFICIENCY) == 97
    assert required_sample_size_cohen_d(0.20, Z_98_SAMPLE_SUFFICIENCY) == 136
    assert required_sample_size_cohen_d(0.20, Z_99_SAMPLE_SUFFICIENCY) == 166


def test_required_sample_size_proportion_values():
    # Formula: ceil((z**2 * p * (1-p)) / margin**2)
    # p = 0.50, margin = 0.05
    # 95% (z = 1.95996): (1.95996**2 * 0.25) / 0.0025 = 384.14 => 385
    # 98% (z = 2.32635): (2.32635**2 * 0.25) / 0.0025 = 541.19 => 542
    # 99% (z = 2.57583): (2.57583**2 * 0.25) / 0.0025 = 663.48 => 664
    assert required_sample_size_proportion(0.50, 0.05, Z_95_SAMPLE_SUFFICIENCY) == 385
    assert required_sample_size_proportion(0.50, 0.05, Z_98_SAMPLE_SUFFICIENCY) == 542
    assert required_sample_size_proportion(0.50, 0.05, Z_99_SAMPLE_SUFFICIENCY) == 664


@pytest.mark.parametrize("bad_d", [0.0, -0.05, None, float("nan"), float("inf")])
def test_required_sample_size_cohen_d_invalid(bad_d):
    assert required_sample_size_cohen_d(bad_d, 1.96) == 0


@pytest.mark.parametrize("bad_p", [0.0, 1.0, -0.1, 1.2, None, float("nan"), float("inf")])
def test_required_sample_size_proportion_invalid_p(bad_p):
    assert required_sample_size_proportion(bad_p, 0.05, 1.96) == 0


@pytest.mark.parametrize("bad_margin", [0.0, -0.01, None, float("nan"), float("inf")])
def test_required_sample_size_proportion_invalid_margin(bad_margin):
    assert required_sample_size_proportion(0.50, bad_margin, 1.96) == 0


def test_sample_size_sufficiency_payload_4_closes():
    closes = [
        {"realized_pnl": 0.10, "cost_basis": 1.0},
        {"realized_pnl": -0.10, "cost_basis": 1.0},
        {"realized_pnl": 0.10, "cost_basis": 1.0},
        {"realized_pnl": -0.10, "cost_basis": 1.0},
    ]
    res = compute_trade_analytics(
        closes=closes,
        starting_capital=100.0,
        equity_series=[],
        float_marks=[],
        orders_count=20,
    )
    suff = res["sample_size_sufficiency"]
    assert suff["current_n"] == 4
    assert suff["std_dev_usd"] == pytest.approx(0.11547, abs=1e-4)
    assert suff["effect_size_d"] == 0.20
    assert len(suff["levels"]) == 3

    lvl95, lvl98, lvl99 = suff["levels"]
    assert lvl95["confidence_pct"] == 95
    assert lvl95["z"] == Z_95_SAMPLE_SUFFICIENCY
    assert lvl95["required_n"] == 97
    assert lvl95["remaining_n"] == 93
    assert lvl95["progress_pct"] == 4  # min(100, round(4 / 97 * 100)) = 4

    assert lvl98["confidence_pct"] == 98
    assert lvl98["z"] == Z_98_SAMPLE_SUFFICIENCY
    assert lvl98["required_n"] == 136
    assert lvl98["remaining_n"] == 132
    assert lvl98["progress_pct"] == 3  # min(100, round(4 / 136 * 100)) = 3

    # Segmented statuses
    statuses = suff["statuses"]
    assert "pnl_expectancy" in statuses
    assert "stop_loss_rate" in statuses
    assert "merge_rate" in statuses
    assert "fill_rate" in statuses

    assert statuses["pnl_expectancy"]["base"] == "closes"
    assert statuses["pnl_expectancy"]["current_n"] == 4
    assert statuses["pnl_expectancy"]["levels"][0]["required_n"] == 97

    assert statuses["stop_loss_rate"]["base"] == "closes"
    assert statuses["stop_loss_rate"]["current_n"] == 4
    assert statuses["stop_loss_rate"]["levels"][0]["required_n"] == 385

    assert statuses["merge_rate"]["base"] == "closes"
    assert statuses["merge_rate"]["current_n"] == 4
    assert statuses["merge_rate"]["levels"][0]["required_n"] == 385

    assert statuses["fill_rate"]["base"] == "orders"
    assert statuses["fill_rate"]["current_n"] == 20
    assert statuses["fill_rate"]["levels"][0]["required_n"] == 385
    assert statuses["fill_rate"]["levels"][0]["remaining_n"] == 365


def test_sample_size_sufficiency_empty_closes():
    res_empty = compute_trade_analytics(
        closes=[],
        starting_capital=100.0,
        equity_series=[],
        float_marks=[],
        orders_count=0,
    )
    suff_empty = res_empty["sample_size_sufficiency"]
    assert suff_empty["current_n"] == 0
    assert suff_empty["std_dev_usd"] is None
    assert len(suff_empty["levels"]) == 3
    assert suff_empty["levels"][0]["required_n"] == 97
    assert suff_empty["levels"][0]["remaining_n"] == 97
    assert suff_empty["levels"][0]["progress_pct"] == 0

    assert suff_empty["statuses"]["fill_rate"]["current_n"] == 0
    assert suff_empty["statuses"]["fill_rate"]["levels"][0]["remaining_n"] == 385


def test_active_orders_preserved_in_by_market_when_days_to_resolve_negative(tmp_path, monkeypatch):
    import sqlite3, time
    from core_brain.kpi import report
    from core_brain.order_registry import SCHEMA, OrderRecord, OrderRegistry

    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        kpi_mod,
        "_resolve_market_meta",
        lambda cid, closes, quotes: {
            "condition_id": cid,
            "title": "Live Match",
            "slug": "live-match",
            "url": "https://polymarket.com/market/live-match",
            "category": "Soccer",
            "days_to_resolve": -0.05,
        },
    )

    db_file = tmp_path / "test.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.commit()
    con.close()

    reg = OrderRegistry(db_file)
    now = int(time.time())
    cid = "0xlivematch"
    reg.create_order(OrderRecord(
        id="ord-1", condition_id=cid, token_id="tok-1", side="BUY", price=0.45,
        original_size=5.0, status="open", posted_ts=now, last_polled_ts=now,
        order_id="venue-ord-1", pair_id="pair-1", run_id="run-1",
    ))

    rep = report(db_file, run_id="run-1")
    assert cid in rep["by_market"]
    assert rep["by_market"][cid]["resolved"] is False


def test_expired_market_with_a_booked_close_stays_in_by_market(tmp_path, monkeypatch):
    """A run's closed trade survives the expired-market drop: the market's
    days_to_resolve went negative, it holds nothing and has no open orders,
    but it booked a realized loss. Dropping it hides the run's own trade from
    the Closed Trades tab while the loss still counts in the headline."""
    import sqlite3, time
    from core_brain.kpi import report
    from core_brain.order_registry import SCHEMA, CloseRecord, OrderRegistry

    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        kpi_mod,
        "_resolve_market_meta",
        lambda cid, closes, quotes: {
            "condition_id": cid,
            "title": "Swansea Match",
            "slug": "swansea-match",
            "url": "https://polymarket.com/market/swansea-match",
            "category": "Soccer",
            "days_to_resolve": -0.07,
        },
    )

    db_file = tmp_path / "test_closed.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.commit()
    con.close()

    reg = OrderRegistry(db_file)
    cid = "0xswansea"
    reg.log_close(CloseRecord(
        ts=time.time(), condition_id=cid, method="single_buy_exit", shares=10.0,
        dn_price=0.15, cost_basis=1.70, proceeds=1.50, realized_pnl=-0.20,
        dn_cost_removed=1.70, run_id="run-1", reason="lifecycle_hard_stop",
    ))

    rep = report(db_file, run_id="run-1")
    assert cid in rep["by_market"], "expired market with a booked close was dropped"
    assert len(rep["by_market"][cid]["settlements"]) == 1
    assert rep["by_market"][cid]["realized_pnl"] == pytest.approx(-0.20)


@pytest.mark.parametrize("status", ["pending", "partial"])
def test_pending_partial_orders_preserved_in_by_market(tmp_path, monkeypatch, status):
    """pending and partial orders count as active — market must appear in by_market."""
    import sqlite3, time
    from core_brain.kpi import report
    from core_brain.order_registry import SCHEMA, OrderRecord, OrderRegistry

    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        kpi_mod,
        "_resolve_market_meta",
        lambda cid, closes, quotes: {
            "condition_id": cid,
            "title": "Live Match",
            "slug": "live-match",
            "url": "https://polymarket.com/market/live-match",
            "category": "Soccer",
            "days_to_resolve": -0.05,
        },
    )

    db_file = tmp_path / f"test_{status}.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.commit()
    con.close()

    reg = OrderRegistry(db_file)
    now = int(time.time())
    cid = "0xlivematch2"
    reg.create_order(OrderRecord(
        id=f"ord-{status}", condition_id=cid, token_id="tok-2", side="BUY", price=0.45,
        original_size=5.0, status=status, posted_ts=now, last_polled_ts=now,
        order_id=f"venue-{status}", pair_id="pair-2", run_id="run-1",
    ))

    rep = report(db_file, run_id="run-1")
    assert cid in rep["by_market"], f"CID missing from by_market for status={status!r}"
    assert rep["by_market"][cid]["resolved"] is False


def test_market_with_open_order_and_filled_quote_not_resolved(tmp_path, monkeypatch):
    """A market with an open resting order AND a partially-filled quote must not be
    marked resolved when days_to_resolve < 0.  This exercises both active_order_cids
    guard and the up_sh branch of the resolved flag."""
    import sqlite3, time
    from core_brain.kpi import report
    from core_brain.order_registry import SCHEMA, OrderRecord, OrderRegistry

    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        kpi_mod,
        "_resolve_market_meta",
        lambda cid, closes, quotes: {
            "condition_id": cid,
            "title": "Live Match",
            "slug": "live-match",
            "url": "https://polymarket.com/market/live-match",
            "category": "Soccer",
            "days_to_resolve": -0.05,
        },
    )

    db_file = tmp_path / "test_held.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    # Quote with filled > 0 creates inventory signal; the open order keeps it active
    con.execute(
        "INSERT INTO quotes (ts, condition_id, token_id, side, price, size, filled, run_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (1.0, "0xlivematch3", "tok-yes", "YES", 0.45, 5.0, 2.5, "run-1"),
    )
    con.commit()
    con.close()

    reg = OrderRegistry(db_file)
    now = int(time.time())
    cid = "0xlivematch3"
    reg.create_order(OrderRecord(
        id="ord-open", condition_id=cid, token_id="tok-yes", side="BUY", price=0.45,
        original_size=5.0, status="open", posted_ts=now, last_polled_ts=now,
        order_id="venue-open", pair_id="pair-4", run_id="run-1",
    ))

    rep = report(db_file, run_id="run-1")
    assert cid in rep["by_market"]
    assert rep["by_market"][cid]["resolved"] is False
