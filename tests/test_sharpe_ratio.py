"""Tests for the shared time-series Sharpe ratio (`core_brain/sharpe.py`).

Issue #875 replaces the cockpit's per-trade ``mean/std * 10`` proxy with an
annualised time-series Sharpe. The known series below is hand-computed in the
comments so a regression cannot hide behind a self-referential oracle:

    values 100, 110, 99, 108.9 at t = 0, 300, 600, 900 s
    step returns: +0.10, -0.10, +0.10
    mean = 0.10 / 3
    sample variance = ((0.10-m)^2 + (-0.10-m)^2 + (0.10-m)^2) / 2 = 0.01333
    per-step ratio = m / sqrt(var) = sqrt(3) / 6 ~= 0.288675
    annualised = (sqrt(3) / 6) * sqrt(31_536_000 / 300) = (sqrt(3)/6)*sqrt(105120)
"""
from __future__ import annotations

import math

import pytest

from core_brain.sharpe import (
    SECONDS_PER_YEAR,
    SHARPE_FORMULA_TEXT,
    risk_free_rate_from_env,
    sharpe_ratio,
)

# Hand-computed closed form for the known series (zero risk-free rate).
KNOWN_SHARPE = (math.sqrt(3.0) / 6.0) * math.sqrt(SECONDS_PER_YEAR / 300)
KNOWN_POINTS = [(0.0, 100.0), (300.0, 110.0), (600.0, 99.0), (900.0, 108.9)]


def test_sharpe_known_series_matches_hand_calculation():
    assert sharpe_ratio(KNOWN_POINTS) == pytest.approx(KNOWN_SHARPE)


def test_sharpe_nonzero_risk_free_rate():
    # Per-step risk-free via compounding: (1.05)^(300/31536000) - 1.
    rf_step = (1.05) ** (300.0 / SECONDS_PER_YEAR) - 1.0
    returns = [0.10, -0.10, 0.10]
    excess = [r - rf_step for r in returns]
    mean = sum(excess) / 3.0
    var = sum((e - mean) ** 2 for e in excess) / 2.0
    expected = mean / math.sqrt(var) * math.sqrt(SECONDS_PER_YEAR / 300)
    assert sharpe_ratio(KNOWN_POINTS, risk_free_rate_annual=0.05) == pytest.approx(
        expected
    )
    # A positive risk-free rate must drag the ratio below the zero-rate value.
    assert sharpe_ratio(KNOWN_POINTS, risk_free_rate_annual=0.05) < KNOWN_SHARPE


def test_sharpe_irregular_gap_carries_forward():
    # Grid 0/300/600/900 carries 100 forward: returns 0, 0, +0.10.
    # mean = 0.10/3, sample var = 0.003333, ratio = 1/sqrt(3).
    expected = (1.0 / math.sqrt(3.0)) * math.sqrt(SECONDS_PER_YEAR / 300)
    assert sharpe_ratio([(0.0, 100.0), (900.0, 110.0)]) == pytest.approx(expected)


def test_sharpe_duplicate_and_unordered_points():
    # Order must not matter; a repeated identical record is idempotent.
    shuffled = [
        (900.0, 108.9),
        (300.0, 110.0),
        (0.0, 100.0),
        (300.0, 110.0),
        (600.0, 99.0),
    ]
    assert sharpe_ratio(shuffled) == pytest.approx(KNOWN_SHARPE)


def test_sharpe_invalid_points_are_dropped():
    polluted = KNOWN_POINTS + [
        (1200.0, float("nan")),
        (1500.0, float("inf")),
        (1800.0, 0.0),
        (2100.0, -50.0),
        (float("nan"), 100.0),
    ]
    assert sharpe_ratio(polluted) == pytest.approx(KNOWN_SHARPE)
    # Nothing measurable left after cleaning.
    assert sharpe_ratio([]) is None
    assert (
        sharpe_ratio([(0.0, float("nan")), (300.0, -5.0), (600.0, 0.0)])
        is None
    )


def test_sharpe_constant_equity_is_none():
    assert sharpe_ratio([(0.0, 100.0), (300.0, 100.0), (600.0, 100.0)]) is None


def test_sharpe_single_step_is_none():
    assert sharpe_ratio([(0.0, 100.0), (300.0, 110.0)]) is None


def test_sharpe_formula_text_names_the_statistic():
    assert "Sharpe Ratio" in SHARPE_FORMULA_TEXT


def test_sharpe_risk_free_rate_setting(monkeypatch):
    monkeypatch.delenv("BOT_SHARPE_RISK_FREE_RATE", raising=False)
    assert risk_free_rate_from_env() == 0.0
    monkeypatch.setenv("BOT_SHARPE_RISK_FREE_RATE", "   ")
    assert risk_free_rate_from_env() == 0.0
    monkeypatch.setenv("BOT_SHARPE_RISK_FREE_RATE", "0.05")
    assert risk_free_rate_from_env() == pytest.approx(0.05)


@pytest.mark.parametrize("bad", ["-0.01", "1.5", "abc", "nan", "inf"])
def test_sharpe_risk_free_rate_setting_rejects_bad_values(monkeypatch, bad):
    monkeypatch.setenv("BOT_SHARPE_RISK_FREE_RATE", bad)
    with pytest.raises(ValueError):
        risk_free_rate_from_env()
