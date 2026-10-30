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
DEAD_BAND = "BOT_ORDER_REQUOTE_DEAD_BAND_USD"
DEAD_BAND_RETIRED = "BOT_ORDER_REQUOTE_DEAD_BAND_CENTS"
DROPS = "BOT_RISK_STOP_ADVERSE_DROP_USD"
DROPS_RETIRED = "BOT_RISK_STOP_ADVERSE_DROP_CENTS"


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


# --- the two dollar-valued keys: renamed to `_usd`, retired names still read --

def test_defaults_are_usd_for_dead_band_and_adverse_drop():
    """The shipped numbers did not move when the names did."""
    cfg = core_config.load()
    assert cfg.bot_order_requote_dead_band_usd == 0.03
    assert cfg.bot_risk_stop_adverse_drop_usd == 0.05
    assert isinstance(cfg.bot_order_requote_dead_band_usd, float)


def test_canonical_usd_names_load(monkeypatch, caplog):
    monkeypatch.setenv(DEAD_BAND, "0.10")
    monkeypatch.setenv(DROPS, "0.08")
    with caplog.at_level(logging.WARNING):
        cfg = core_config.load()
    assert cfg.bot_order_requote_dead_band_usd == 0.10
    assert cfg.bot_risk_stop_adverse_drop_usd == 0.08
    assert "DEPRECATED" not in caplog.text


@pytest.mark.parametrize(
    "retired_name,canonical_name,field,raw",
    [
        (DEAD_BAND_RETIRED, DEAD_BAND, "bot_order_requote_dead_band_usd", "0.10"),
        (DROPS_RETIRED, DROPS, "bot_risk_stop_adverse_drop_usd", "0.045"),
    ],
)
def test_retired_cents_name_still_loads_with_one_warning(
        monkeypatch, caplog, retired_name, canonical_name, field, raw):
    """The alias is a rename, not a conversion: same value, one loud warning.

    A silently ignored retired name is the failure this guards: the operator
    would read 0.10 in their own `.env` while the loader ran the shipped 0.03.
    """
    monkeypatch.setenv(retired_name, raw)
    with caplog.at_level(logging.WARNING):
        cfg = core_config.load()
    assert getattr(cfg, field) == float(raw)

    deprecated = [r for r in caplog.records
                  if r.levelno == logging.WARNING and "DEPRECATED" in r.getMessage()]
    assert len(deprecated) == 1
    message = deprecated[0].getMessage()
    assert retired_name in message
    assert raw in message
    assert f"{canonical_name}={raw}" in message

    # The dashboard calls `from_env()` per request; one warning per key per
    # process is what makes the line readable.
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert getattr(core_config.load(), field) == float(raw)
    assert "DEPRECATED" not in caplog.text


def test_canonical_usd_name_beats_its_retired_spelling(monkeypatch, caplog):
    monkeypatch.setenv(DEAD_BAND_RETIRED, "0.10")
    monkeypatch.setenv(DEAD_BAND, "0.04")
    with caplog.at_level(logging.WARNING):
        cfg = core_config.load()
    assert cfg.bot_order_requote_dead_band_usd == 0.04
    assert "DEPRECATED" not in caplog.text


def test_blank_canonical_usd_name_falls_through_to_the_retired_one(monkeypatch, caplog):
    """`_env` reads blank as unset, and the fallback keeps that rule."""
    monkeypatch.setenv(DROPS, "   ")
    monkeypatch.setenv(DROPS_RETIRED, "0.08")
    with caplog.at_level(logging.WARNING):
        cfg = core_config.load()
    assert cfg.bot_risk_stop_adverse_drop_usd == 0.08
    assert "DEPRECATED" in caplog.text


@pytest.mark.parametrize("name", [DEAD_BAND, DEAD_BAND_RETIRED])
@pytest.mark.parametrize("raw", ["1.5", "-0.1", "abc", "nan", "inf"])
def test_dead_band_rejections_name_the_spelling_the_operator_used(
        monkeypatch, name, raw):
    monkeypatch.setenv(name, raw)
    with pytest.raises(ValueError) as exc:
        core_config.load()
    assert name in str(exc.value)


@pytest.mark.parametrize("name", [DROPS, DROPS_RETIRED])
@pytest.mark.parametrize("raw", ["0.0", "0.91", "abc"])
def test_adverse_drop_rejections_name_the_spelling_the_operator_used(
        monkeypatch, name, raw):
    monkeypatch.setenv(name, raw)
    with pytest.raises(ValueError) as exc:
        core_config.load()
    assert name in str(exc.value)


def test_hybrid_preset_sets_the_renamed_dead_band(monkeypatch):
    """The preset table carries the field name, so the rename had to reach it."""
    monkeypatch.setenv("BOT_QUOTE_TOURNAMENT_PRESET", "hybrid")
    assert core_config.load().bot_order_requote_dead_band_usd == 0.060


RETIRED_MONEY_NAMES = frozenset({DEAD_BAND_RETIRED, DROPS_RETIRED})


def test_arms_files_use_whole_cents_and_no_retired_money_names(monkeypatch, caplog):
    """Every shipped arm must match the unit its key advertises.

    This is the gate for the issue itself: a `_CENTS` key holding `"0.10"` is
    the 100x footgun, and a retired spelling still shipping in a checked-in
    arms file is how the alias window becomes permanent.
    """
    import json
    import pathlib

    arms_dir = pathlib.Path(__file__).resolve().parent.parent / "configs" / "tournaments"
    arms_files = sorted(arms_dir.glob("*.json"))
    assert arms_files, f"no arms files under {arms_dir}"

    checked = 0
    for path in arms_files:
        arms = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(arms, list), path.name
        for arm in arms:
            env = arm.get("env") or {}
            for key, value in env.items():
                assert not (RETIRED_MONEY_NAMES & {key}), (
                    f"{path.name} arm {arm['name']} still sets the retired {key}"
                )
                if key.endswith("_CENTS"):
                    assert value.strip().isdigit(), (
                        f"{path.name} arm {arm['name']} sets {key}={value!r}, "
                        "which is not whole cents"
                    )
            for key in env:
                monkeypatch.setenv(key, env[key])
            with caplog.at_level(logging.WARNING):
                core_config.load()
            assert "DEPRECATED" not in caplog.text, (
                f"{path.name} arm {arm['name']} loads through a retired name"
            )
            for key in env:
                monkeypatch.delenv(key, raising=False)
            caplog.clear()
            checked += 1

    assert checked >= 4, "the arms sweep did not actually walk the shipped arms"


@pytest.mark.parametrize("name", [DROPS, DROPS_RETIRED])
def test_the_45c_champion_stop_survives_the_rename(monkeypatch, name):
    """The live champion's 4.5c stop is the reason this is a rename, not a
    conversion: 4.5c has no integer-cent form, and the trigger it produces at a
    sub-cent entry must be identical through either spelling."""
    from core_brain.single_leg_lifecycle import stop_trigger

    monkeypatch.setenv(name, "0.045")
    loaded = core_config.load().bot_risk_stop_adverse_drop_usd
    assert loaded == 0.045
    for entry in (0.50, 0.512, 0.53, 0.618, 0.33):
        assert stop_trigger(entry, "ABSOLUTE_DROP", loaded, 10.0, 75.0) == \
            stop_trigger(entry, "ABSOLUTE_DROP", 0.045, 10.0, 75.0)
