"""Ladder config (issue #325): T2 RED first.

Additive `MakerConfig` fields, off by default; invalid shapes and budgets
are rejected. No existing default changes.
"""
from __future__ import annotations

import pytest

from core_brain.config import MakerConfig


def test_ladder_defaults_are_off_and_safe():
    cfg = MakerConfig()
    assert cfg.ladder_mode is False
    assert cfg.ladder_rungs == 2
    assert cfg.ladder_exit_sec == pytest.approx(60.0)
    assert cfg.ladder_budget_usd == pytest.approx(0.0)
    assert cfg.ladder_open_window_sec == pytest.approx(30.0)
    cfg.validate_ladder()  # off with defaults still validates


def test_ladder_accepts_a_funded_shape():
    cfg = MakerConfig(ladder_mode=True, ladder_rungs=2,
                      ladder_exit_sec=60.0, ladder_budget_usd=25.0)
    cfg.validate_ladder()


@pytest.mark.parametrize("kw", [
    {"ladder_rungs": 1},
    {"ladder_rungs": 0},
    {"ladder_exit_sec": 0.0},
    {"ladder_exit_sec": -5.0},
    {"ladder_budget_usd": -1.0},
    {"ladder_open_window_sec": 0.0},
])
def test_ladder_rejects_bad_shapes(kw):
    cfg = MakerConfig(ladder_mode=True, **kw)
    with pytest.raises(ValueError):
        cfg.validate_ladder()
