"""Tests for required_sample_size_for_mean and sample_size_sufficiency payload (#443)."""
from __future__ import annotations

import math
import pytest

from core_brain import kpi as kpi_mod
from core_brain.kpi import (
    KPI_PAYLOAD_VERSION,
    Z_95_SAMPLE_SUFFICIENCY,
    Z_98_SAMPLE_SUFFICIENCY,
    Z_99_SAMPLE_SUFFICIENCY,
    required_sample_size_for_mean,
    compute_trade_analytics,
)


def test_required_sample_size_for_mean_formula_values():
    # Formula: ceil(((z * std) / E) ** 2)
    # std = 0.11547, E = 0.02
    # 95% (z = 1.95996): ((1.95996 * 0.11547) / 0.02) ** 2 = 128.05 => 129
    # 98% (z = 2.32635): ((2.32635 * 0.11547) / 0.02) ** 2 = 180.40 => 181
    # 99% (z = 2.57583): ((2.57583 * 0.11547) / 0.02) ** 2 = 221.18 => 222
    std = 0.11547
    e = 0.02
    assert required_sample_size_for_mean(std, e, Z_95_SAMPLE_SUFFICIENCY) == 129
    assert required_sample_size_for_mean(std, e, Z_98_SAMPLE_SUFFICIENCY) == 181
    assert required_sample_size_for_mean(std, e, Z_99_SAMPLE_SUFFICIENCY) == 222


def test_required_sample_size_for_mean_margin_override():
    std = 0.11547
    e = 0.05
    # With higher margin E = 0.05, required sample size is smaller
    n95 = required_sample_size_for_mean(std, e, Z_95_SAMPLE_SUFFICIENCY)
    assert n95 == math.ceil(((Z_95_SAMPLE_SUFFICIENCY * std) / e) ** 2)
    assert n95 < 129


@pytest.mark.parametrize("bad_std", [0.0, -0.05, None, float("nan"), float("inf")])
def test_required_sample_size_for_mean_invalid_std(bad_std):
    assert required_sample_size_for_mean(bad_std, 0.02, 1.96) == 0


@pytest.mark.parametrize("bad_margin", [0.0, -0.01, None, float("nan"), float("inf")])
def test_required_sample_size_for_mean_invalid_margin(bad_margin):
    assert required_sample_size_for_mean(0.10, bad_margin, 1.96) == 0


@pytest.mark.parametrize("bad_z", [0.0, -1.96, float("nan"), float("inf")])
def test_required_sample_size_for_mean_invalid_z(bad_z):
    assert required_sample_size_for_mean(0.10, 0.02, bad_z) == 0


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
    )
    suff = res["sample_size_sufficiency"]
    assert suff["current_n"] == 4
    assert suff["std_dev_usd"] == pytest.approx(0.11547, abs=1e-4)
    assert suff["target_margin_usd"] == 0.02
    assert len(suff["levels"]) == 3

    lvl95, lvl98, lvl99 = suff["levels"]
    assert lvl95["confidence_pct"] == 95
    assert lvl95["z"] == Z_95_SAMPLE_SUFFICIENCY
    assert lvl95["required_n"] == 129
    assert lvl95["remaining_n"] == 125
    assert lvl95["progress_pct"] == 3  # min(100, round(4 / 129 * 100)) = 3

    assert lvl98["confidence_pct"] == 98
    assert lvl98["z"] == Z_98_SAMPLE_SUFFICIENCY
    assert lvl98["required_n"] == 181
    assert lvl98["remaining_n"] == 177
    assert lvl98["progress_pct"] == 2  # min(100, round(4 / 181 * 100)) = 2

    assert lvl99["confidence_pct"] == 99
    assert lvl99["z"] == Z_99_SAMPLE_SUFFICIENCY
    assert lvl99["required_n"] == 222
    assert lvl99["remaining_n"] == 218
    assert lvl99["progress_pct"] == 2  # min(100, round(4 / 222 * 100)) = 2


def test_sample_size_sufficiency_empty_and_single_close():
    # Empty
    res_empty = compute_trade_analytics(
        closes=[],
        starting_capital=100.0,
        equity_series=[],
        float_marks=[],
    )
    suff_empty = res_empty["sample_size_sufficiency"]
    assert suff_empty["current_n"] == 0
    assert suff_empty["std_dev_usd"] is None
    assert suff_empty["target_margin_usd"] == 0.02
    assert len(suff_empty["levels"]) == 3
    for lvl in suff_empty["levels"]:
        assert lvl["required_n"] is None
        assert lvl["remaining_n"] is None
        assert lvl["progress_pct"] is None

    # Single close
    res_single = compute_trade_analytics(
        closes=[{"realized_pnl": 0.05, "cost_basis": 1.0}],
        starting_capital=100.0,
        equity_series=[],
        float_marks=[],
    )
    suff_single = res_single["sample_size_sufficiency"]
    assert suff_single["current_n"] == 1
    assert suff_single["std_dev_usd"] is None
    for lvl in suff_single["levels"]:
        assert lvl["required_n"] is None
        assert lvl["remaining_n"] is None
        assert lvl["progress_pct"] is None


def test_sample_size_sufficiency_zero_spread():
    # 2 identical closes -> sample stdev is 0.0
    closes = [
        {"realized_pnl": 0.05, "cost_basis": 1.0},
        {"realized_pnl": 0.05, "cost_basis": 1.0},
    ]
    res = compute_trade_analytics(
        closes=closes,
        starting_capital=100.0,
        equity_series=[],
        float_marks=[],
    )
    suff = res["sample_size_sufficiency"]
    assert suff["current_n"] == 2
    assert suff["std_dev_usd"] == 0.0
    for lvl in suff["levels"]:
        assert lvl["required_n"] is None
        assert lvl["remaining_n"] is None
        assert lvl["progress_pct"] is None


def test_sample_size_sufficiency_custom_target_margin():
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
        target_margin_usd=0.05,
    )
    suff = res["sample_size_sufficiency"]
    assert suff["target_margin_usd"] == 0.05
    assert suff["levels"][0]["required_n"] == required_sample_size_for_mean(
        suff["std_dev_usd"], 0.05, Z_95_SAMPLE_SUFFICIENCY
    )
