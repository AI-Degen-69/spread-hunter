"""#619: the entry-relative stop's config surface.

One mode per run, one formula per mode, and a universal emergency floor that
every mode shares. The three knobs this replaces are retired outright rather
than aliased: the old `usd`/`pct` pair counted in different units, so silently
mapping a stale name onto a new field would change what the operator asked for.
"""
import logging

import pytest

from core_brain.config import TOURNAMENT_PRESETS, MakerConfig, load


def test_defaults_are_none_mode_and_the_spec_numbers():
    cfg = MakerConfig()

    assert cfg.stop_loss_mode == "NONE"
    assert cfg.drop_cents == 0.05
    assert cfg.drawdown_pct == 10.0
    assert cfg.bot_risk_circuit_max_drawdown_pct == 75.0


def test_legacy_emergency_read_still_works():
    import warnings
    cfg = MakerConfig()
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        val = cfg.emergency_drawdown_pct
    assert val == 75.0
    legacy_warns = [
        w for w in recorded
        if issubclass(w.category, DeprecationWarning)
        and "emergency_drawdown_pct" in str(w.message)
    ]
    assert len(legacy_warns) == 1


@pytest.mark.parametrize(
    "attr",
    ["single_buy_grace_sec", "stop_loss_trigger_pct",
     "stop_loss_trigger_usd_cents"],
)
def test_the_retired_knobs_are_gone_from_the_config(attr):
    assert not hasattr(MakerConfig(), attr)


def test_mode_env_override_is_trimmed_and_uppercased(monkeypatch):
    monkeypatch.setenv("HUNTER_STOP_LOSS_MODE", "  percentage_drop ")

    assert load().stop_loss_mode == "PERCENTAGE_DROP"


@pytest.mark.parametrize("raw", ["ABSOLUTE", "PERCENTAGE", "usd", "pct", "bogus"])
def test_mode_env_refuses_non_canonical_spellings(monkeypatch, raw):
    monkeypatch.setenv("HUNTER_STOP_LOSS_MODE", raw)

    with pytest.raises(ValueError, match="HUNTER_STOP_LOSS_MODE"):
        load()


def test_drop_cents_env_is_parsed_and_bounded(monkeypatch):
    monkeypatch.setenv("HUNTER_DROP_CENTS", "0.05")
    assert load().drop_cents == 0.05

    for raw in ("0.0", "0.009", "0.91", "nan", "inf"):
        monkeypatch.setenv("HUNTER_DROP_CENTS", raw)
        with pytest.raises(ValueError, match="HUNTER_DROP_CENTS"):
            load()


@pytest.mark.parametrize(
    ("name", "field"),
    [("HUNTER_DRAWDOWN_PCT", "drawdown_pct"),
     ("HUNTER_EMERGENCY_DRAWDOWN_PCT", "bot_risk_circuit_max_drawdown_pct")],
)
def test_drawdown_pct_env_is_an_open_lower_interval(monkeypatch, name, field):
    monkeypatch.setenv(name, "33.5")
    assert getattr(load(), field) == 33.5

    monkeypatch.setenv(name, "100")
    assert getattr(load(), field) == 100.0

    for raw in ("0", "0.0", "100.1", "nan", "inf"):
        monkeypatch.setenv(name, raw)
        with pytest.raises(ValueError, match=name):
            load()


@pytest.mark.parametrize(
    "old",
    ["HUNTER_SINGLE_BUY_GRACE_SEC", "HUNTER_STOP_LOSS_TRIGGER_PCT",
     "HUNTER_STOP_LOSS_TRIGGER_USD_CENTS"],
)
def test_retired_env_vars_warn_and_are_ignored(monkeypatch, caplog, old):
    monkeypatch.setenv(old, "45")

    with caplog.at_level(logging.WARNING):
        load()

    warnings = [r.getMessage() for r in caplog.records]
    assert any(old in message for message in warnings), warnings


def test_a_retired_env_var_does_not_move_its_replacement(monkeypatch):
    """No value conversion: the retired units are not the new ones."""
    monkeypatch.setenv("HUNTER_STOP_LOSS_TRIGGER_USD_CENTS", "4.5")

    cfg = load()

    assert cfg.drop_cents == 0.05
    assert cfg.stop_loss_mode == "NONE"


def test_no_preset_carries_a_stop_knob():
    """#619: the stop is a run setting, not a per-arm one."""
    retired = ("stop_loss_trigger_usd_cents", "stop_loss_trigger_pct",
               "single_buy_grace_sec", "stop_loss_mode")

    for name, preset in TOURNAMENT_PRESETS.items():
        for key in retired:
            assert key not in preset, (name, key)
