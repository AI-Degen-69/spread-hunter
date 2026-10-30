"""#872: the `_CENTS` settings must state the unit they actually hold.

Three settings ended in `_CENTS` and did not all hold cents:
`BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS=6` was six cents, while
`BOT_ORDER_REQUOTE_DEAD_BAND_CENTS=0.10` was ten cents. The bounds enforced the
difference correctly; only the name lied, so these tests pin the name-to-unit
contract instead of the arithmetic that already worked.

This file is the selection the issue's own test command names:
`python -m pytest -q tests/test_config.py -k "cents or dead_band or adverse_drop or spread_offset"`.
"""
from __future__ import annotations

import logging
import os
import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from core_brain import config as core_config  # noqa: E402

OFFSET_MIN = "BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS"
OFFSET_MAX = "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Every test starts from shipped defaults, with no warning memory.

    The operator shell is not hermetic: `BOT_QUOTE_TOURNAMENT_PRESET` or a
    stale dead band in the environment would silently change what `load()`
    returns. The deprecation set is process-wide by design (one warning per
    key per process), so it has to be cleared between tests or the second
    test in the file would see no warning and pass for the wrong reason.
    """
    for name in list(os.environ):
        if name.startswith("BOT_") or name.startswith("FILTER_"):
            monkeypatch.delenv(name, raising=False)
    warned = getattr(core_config, "_DEPRECATION_WARNED", None)
    if warned is not None:
        warned.clear()
    yield


# --- spread offset: already integer cents, now strict about it -------------

def test_spread_offset_cents_accept_whole_cents(monkeypatch):
    monkeypatch.setenv(OFFSET_MIN, "2")
    monkeypatch.setenv(OFFSET_MAX, "6")
    cfg = core_config.load()
    assert cfg.bot_quote_spread_offset_min_cents == 2
    assert cfg.bot_quote_spread_offset_max_cents == 6
    assert isinstance(cfg.bot_quote_spread_offset_max_cents, int)
    assert isinstance(cfg.bot_quote_spread_offset_min_cents, int)


def test_spread_offset_cents_tolerates_surrounding_spaces(monkeypatch):
    monkeypatch.setenv(OFFSET_MAX, "  7  ")
    assert core_config.load().bot_quote_spread_offset_max_cents == 7


@pytest.mark.parametrize(
    "var,raw,lo,hi",
    [
        (OFFSET_MIN, "1", 1, 10),
        (OFFSET_MIN, "10", 1, 10),
        (OFFSET_MAX, "1", 1, 20),
        (OFFSET_MAX, "20", 1, 20),
    ],
)
def test_spread_offset_cents_bounds_are_inclusive(monkeypatch, var, raw, lo, hi):
    monkeypatch.setenv(var, raw)
    cfg = core_config.load()
    field = (
        "bot_quote_spread_offset_min_cents" if var == OFFSET_MIN
        else "bot_quote_spread_offset_max_cents"
    )
    assert getattr(cfg, field) == int(raw)


@pytest.mark.parametrize(
    "var,raw",
    [
        (OFFSET_MIN, "0"),
        (OFFSET_MIN, "11"),
        (OFFSET_MAX, "0"),
        (OFFSET_MAX, "21"),
        (OFFSET_MAX, "-3"),
    ],
)
def test_spread_offset_cents_out_of_range_is_refused(monkeypatch, var, raw):
    """A bounded knob is refused, never clamped: a clamped offset reads as armed."""
    monkeypatch.setenv(var, raw)
    with pytest.raises(ValueError, match="whole cents"):
        core_config.load()


@pytest.mark.parametrize(
    "var,raw",
    [
        (OFFSET_MIN, "2.5"),
        (OFFSET_MAX, "6.5"),
        (OFFSET_MAX, "19.9"),
        (OFFSET_MAX, "6.0"),
    ],
)
def test_fractional_spread_offset_cents_is_rejected(monkeypatch, var, raw):
    """`int()` used to run 19 for 19.9 and leave no trace; #872 refuses it."""
    monkeypatch.setenv(var, raw)
    with pytest.raises(ValueError) as exc:
        core_config.load()
    assert var in str(exc.value)
    assert "whole cents" in str(exc.value)


@pytest.mark.parametrize(
    "var,raw",
    [
        (OFFSET_MIN, "abc"),
        (OFFSET_MIN, "nan"),
        (OFFSET_MIN, "inf"),
        (OFFSET_MIN, "6c"),
        (OFFSET_MIN, "1e2"),
        (OFFSET_MAX, "abc"),
        (OFFSET_MAX, "nan"),
        (OFFSET_MAX, "inf"),
        (OFFSET_MAX, "6c"),
        (OFFSET_MAX, "1e2"),
        (OFFSET_MAX, ""),
    ],
)
def test_bad_text_on_spread_offset_cents_is_rejected(monkeypatch, var, raw):
    monkeypatch.setenv(var, raw)
    if not raw.strip():
        # A blank value is "unset", exactly as `_env` reads it everywhere else.
        cfg = core_config.load()
        assert cfg.bot_quote_spread_offset_max_cents == 20
        return
    with pytest.raises(ValueError, match="whole cents"):
        core_config.load()


def test_blank_spread_offset_cents_keeps_the_shipped_defaults(monkeypatch):
    monkeypatch.setenv(OFFSET_MIN, "   ")
    monkeypatch.setenv(OFFSET_MAX, "")
    cfg = core_config.load()
    assert cfg.bot_quote_spread_offset_min_cents == 1
    assert cfg.bot_quote_spread_offset_max_cents == 20


def test_spread_offset_cents_whole_values_log_no_deprecation(monkeypatch, caplog):
    monkeypatch.setenv(OFFSET_MIN, "2")
    monkeypatch.setenv(OFFSET_MAX, "6")
    with caplog.at_level(logging.WARNING):
        core_config.load()
    assert "DEPRECATED" not in caplog.text
