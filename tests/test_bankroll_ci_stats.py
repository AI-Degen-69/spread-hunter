"""Unit tests for statistical confidence interval engine (95%/98% CIs, Sharpe, Sortino)."""
from strategy.stats import calc_confidence_intervals

def test_calc_confidence_intervals_basic():
    returns = [1.0, 2.0, 3.0, 1.5, 2.5, -0.5, 3.5, 2.0, 1.8, 2.2]
    res = calc_confidence_intervals(returns)
    assert res["count"] == 10
    assert abs(res["mean"] - 1.9) < 1e-4
    assert "sharpe" in res
    assert "sortino" in res
    assert res["ci_95"]["lower"] < res["mean"] < res["ci_95"]["upper"]
    assert res["ci_98"]["lower"] < res["ci_95"]["lower"]
    assert res["ci_98"]["upper"] > res["ci_95"]["upper"]

def test_calc_confidence_intervals_small_sample():
    returns = [1.5]
    res = calc_confidence_intervals(returns)
    assert res["count"] == 1
    assert res["mean"] == 1.5
    assert res["std"] == 0.0
    assert res["se"] == 0.0
