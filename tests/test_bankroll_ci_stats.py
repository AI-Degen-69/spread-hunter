"""Unit tests for statistical confidence interval engine (95%/98% CIs, Sharpe, Sortino)."""
import math
from strategy.stats import calc_confidence_intervals, get_student_t_critical

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
    assert res["ci_95"]["lower"] == 1.5
    assert res["ci_95"]["upper"] == 1.5

def test_calc_confidence_intervals_empty():
    res = calc_confidence_intervals([])
    assert res["count"] == 0
    assert res["mean"] == 0.0
    assert res["ci_95"]["lower"] == 0.0
    assert res["ci_95"]["upper"] == 0.0

def test_student_t_critical_oracles():
    assert get_student_t_critical(1, "95") == 12.706
    assert get_student_t_critical(9, "95") == 2.262
    assert get_student_t_critical(29, "95") == 2.045
    assert get_student_t_critical(1000, "95") == 1.962
    assert get_student_t_critical(2000, "95") == 1.960

    assert get_student_t_critical(1, "98") == 31.821
    assert get_student_t_critical(9, "98") == 2.821
    assert get_student_t_critical(29, "98") == 2.462

def test_all_positive_returns_sortino():
    returns = [1.0, 2.0, 3.0, 4.0, 5.0]
    res = calc_confidence_intervals(returns)
    assert res["count"] == 5
    assert res["mean"] == 3.0
    assert res["sortino"] >= 99.0

