"""#619: the entry-relative stop's config surface.

One mode per run, one formula per mode, and a universal emergency floor that
every mode shares. The three knobs this replaces are retired outright rather
than aliased: the old `usd`/`pct` pair counted in different units, so silently
mapping a stale name onto a new field would change what the operator asked for.
"""
import pytest

from core_brain.config import TOURNAMENT_PRESETS, MakerConfig, load


def test_defaults_are_none_mode_and_the_spec_numbers():
    cfg = MakerConfig()

    assert cfg.bot_risk_stop_mode == "NONE"
    assert cfg.bot_risk_stop_adverse_drop_cents == 0.05
    assert cfg.bot_risk_stop_max_drawdown_pct == 10.0
    assert cfg.bot_risk_circuit_max_drawdown_pct == 75.0


def test_reading_the_emergency_floor_is_not_a_deprecated_act():
    """The field is canonical; reading it warns about nothing."""
    import warnings
    cfg = MakerConfig()
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        val = cfg.bot_risk_circuit_max_drawdown_pct
    assert val == 75.0
    legacy_warns = [
        w for w in recorded
        if issubclass(w.category, DeprecationWarning)
    ]
    assert legacy_warns == []


@pytest.mark.parametrize(
    "attr",
    ["single_buy_grace_sec", "stop_loss_trigger_pct",
     "stop_loss_trigger_usd_cents"],
)
def test_the_retired_knobs_are_gone_from_the_config(attr):
    assert not hasattr(MakerConfig(), attr)


def test_mode_env_override_is_trimmed_and_uppercased(monkeypatch):
    monkeypatch.setenv("BOT_RISK_STOP_MODE", "  percentage_drop ")

    assert load().bot_risk_stop_mode == "PERCENTAGE_DROP"


@pytest.mark.parametrize("raw", ["ABSOLUTE", "PERCENTAGE", "usd", "pct", "bogus"])
def test_mode_env_refuses_non_canonical_spellings(monkeypatch, raw):
    monkeypatch.setenv("BOT_RISK_STOP_MODE", raw)

    with pytest.raises(ValueError, match="BOT_RISK_STOP_MODE"):
        load()


def test_drop_cents_env_is_parsed_and_bounded(monkeypatch):
    monkeypatch.setenv("BOT_RISK_STOP_ADVERSE_DROP_CENTS", "0.05")
    assert load().bot_risk_stop_adverse_drop_cents == 0.05

    for raw in ("0.0", "0.009", "0.91", "nan", "inf"):
        monkeypatch.setenv("BOT_RISK_STOP_ADVERSE_DROP_CENTS", raw)
        with pytest.raises(ValueError, match="BOT_RISK_STOP_ADVERSE_DROP_CENTS"):
            load()


@pytest.mark.parametrize(
    ("name", "field"),
    [("BOT_RISK_STOP_MAX_DRAWDOWN_PCT", "bot_risk_stop_max_drawdown_pct"),
     ("BOT_RISK_CIRCUIT_MAX_DRAWDOWN_PCT", "bot_risk_circuit_max_drawdown_pct")],
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
    ("retired", "field", "shipped"),
    [("HUNTER_DRAWDOWN_PCT", "bot_risk_stop_max_drawdown_pct", 10.0),
     ("HUNTER_EMERGENCY_DRAWDOWN_PCT", "bot_risk_circuit_max_drawdown_pct", 75.0)],
)
def test_a_retired_drawdown_variable_is_ignored(monkeypatch, retired, field, shipped):
    """The retired name is inert: the shipped bar stands and nothing is raised."""
    monkeypatch.setenv(retired, "33.5")
    assert getattr(load(), field) == shipped


def test_a_retired_env_var_does_not_move_its_replacement(monkeypatch):
    """No value conversion: the retired units are not the new ones."""
    monkeypatch.setenv("HUNTER_STOP_LOSS_TRIGGER_USD_CENTS", "4.5")

    cfg = load()

    assert cfg.bot_risk_stop_adverse_drop_cents == 0.05
    assert cfg.bot_risk_stop_mode == "NONE"


def test_no_preset_carries_a_stop_knob():
    """#619: the stop is a run setting, not a per-arm one."""
    retired = ("stop_loss_trigger_usd_cents", "stop_loss_trigger_pct",
               "single_buy_grace_sec", "bot_risk_stop_mode")

    for name, preset in TOURNAMENT_PRESETS.items():
        for key in retired:
            assert key not in preset, (name, key)
