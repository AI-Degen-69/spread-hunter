"""Active engine profile naming (#856).

`describe_active_profile` builds the badge name from the selected tournament
preset and the queue-gate settings of the config in use: `balanced`,
`queue-clear-5m`, `balanced+queue-clear-5m`, or `default`. `None` means "no
named preset selected" -- it is never rendered as `baseline`, which is a real
preset.
"""
from __future__ import annotations

from core_brain.config import describe_active_profile, load, resolve_tournament_preset_name

PRESET_VAR = "BOT_QUOTE_TOURNAMENT_PRESET"
GATE_VAR = "BOT_ORDER_GATE_QUEUE_CLEAR_ON"
MIN_VAR = "BOT_ORDER_QUEUE_MAX_CLEAR_MIN"


def _load_with(monkeypatch, preset=None, gate_on=False, minutes=None):
    if preset is None:
        monkeypatch.delenv(PRESET_VAR, raising=False)
    else:
        monkeypatch.setenv(PRESET_VAR, preset)
    if gate_on:
        monkeypatch.setenv(GATE_VAR, "1")
    else:
        monkeypatch.delenv(GATE_VAR, raising=False)
    if minutes is None:
        monkeypatch.delenv(MIN_VAR, raising=False)
    else:
        monkeypatch.setenv(MIN_VAR, str(minutes))
    cfg = load()
    return cfg, resolve_tournament_preset_name()


def test_preset_alone_names_the_preset(monkeypatch):
    cfg, preset = _load_with(monkeypatch, preset="balanced")
    profile = describe_active_profile(cfg, preset)
    assert profile["name"] == "balanced"
    assert profile["preset"] == "balanced"
    assert profile["queue_clear_on"] is False


def test_explicit_baseline_is_not_the_fallback(monkeypatch):
    cfg, preset = _load_with(monkeypatch, preset="baseline")
    profile = describe_active_profile(cfg, preset)
    assert profile["name"] == "baseline"
    assert profile["preset"] == "baseline"


def test_queue_gate_alone_names_the_minutes(monkeypatch):
    cfg, preset = _load_with(monkeypatch, gate_on=True, minutes=5)
    profile = describe_active_profile(cfg, preset)
    assert profile["name"] == "queue-clear-5m"
    assert profile["preset"] is None
    assert profile["queue_clear_on"] is True
    assert profile["queue_max_clear_min"] == 5.0


def test_preset_and_queue_gate_join_with_plus(monkeypatch):
    cfg, preset = _load_with(monkeypatch, preset="balanced", gate_on=True, minutes=5)
    profile = describe_active_profile(cfg, preset)
    assert profile["name"] == "balanced+queue-clear-5m"
    assert profile["preset"] == "balanced"


def test_fractional_minutes_keep_the_fraction(monkeypatch):
    cfg, preset = _load_with(monkeypatch, gate_on=True, minutes=2.5)
    profile = describe_active_profile(cfg, preset)
    assert profile["name"] == "queue-clear-2.5m"
    assert profile["queue_max_clear_min"] == 2.5


def test_no_preset_and_no_gate_is_default_not_baseline(monkeypatch):
    cfg, preset = _load_with(monkeypatch)
    profile = describe_active_profile(cfg, preset)
    assert profile["name"] == "default"
    assert profile["name"] != "baseline"
    assert profile["preset"] is None
    assert profile["queue_clear_on"] is False
