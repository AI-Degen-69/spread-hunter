"""#840: live and shadow lanes load identical champion settings.

The champion is the `prudent` tournament preset plus five per-field env
overrides (see SPEC.md). Both lanes build config through `config.load()` --
`shadow_run.shadow_cfg()` is a straight alias -- so any divergence between
the lanes is a loader bug, and these tests pin the mapping shut.
"""
from __future__ import annotations

import dataclasses
import os
import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from core_brain import risk  # noqa: E402
from core_brain.config import load  # noqa: E402
from core_brain.shadow_run import shadow_cfg  # noqa: E402

CHAMPION_ENV = {
    "BOT_QUOTE_TOURNAMENT_PRESET": "prudent",
    "BOT_ORDER_REQUOTE_DEAD_BAND_CENTS": "0.06",
    "BOT_RISK_STOP_MODE": "ABSOLUTE_DROP",
    "BOT_RISK_STOP_ADVERSE_DROP_CENTS": "0.045",
    "BOT_ORDER_GATE_QUEUE_CLEAR_ON": "1",
    "BOT_ORDER_QUEUE_MAX_CLEAR_MIN": "3",
}

# Trial knobs that fail loud (raise) outside a rehearsal -- never part of
# the champion, cleared so the suite is hermetic on any operator shell.
_TRIAL_VARS = (
    "BOT_POS_PRICE_MAX_PAIR_COST_USD",
    "BOT_QUOTE_WIDE_BOOK_TRIAL",
    "BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID",
    "FILTER_COMMON_VOL_MIN_TOP3_DEPTH_USD_TRIAL",
    "FILTER_STD_VOL_MIN_24H_USD_TRIAL",
)


@pytest.fixture
def champion_env(monkeypatch):
    for k, v in CHAMPION_ENV.items():
        monkeypatch.setenv(k, v)
    for k in _TRIAL_VARS:
        monkeypatch.delenv(k, raising=False)
    yield
    for k in CHAMPION_ENV:
        monkeypatch.delenv(k, raising=False)


def test_live_and_shadow_load_identical_champion_settings(champion_env):
    """`config.load()` (live) and `shadow_run.shadow_cfg()` read the same map."""
    live = load()
    shadow = shadow_cfg()
    assert live == shadow


def test_champion_values_are_effective(champion_env):
    """The six champion knobs land with the values SPEC.md promises."""
    cfg = load()
    assert cfg.bot_quote_gate_dynamic_offset_on is True
    assert cfg.bot_quote_spread_offset_multiplier == pytest.approx(0.60)
    assert cfg.bot_quote_spread_offset_min_cents == 2
    assert cfg.bot_quote_spread_offset_max_cents == 4
    assert cfg.bot_order_requote_dead_band_cents == pytest.approx(0.06)
    assert cfg.bot_risk_stop_mode == "ABSOLUTE_DROP"
    assert cfg.bot_risk_stop_adverse_drop_cents == pytest.approx(0.045)
    assert cfg.bot_order_gate_queue_clear_on is True
    assert cfg.bot_order_queue_max_clear_min == pytest.approx(3.0)


def test_field_override_beats_preset(champion_env, monkeypatch):
    """A per-field env var wins over the `prudent` preset value."""
    monkeypatch.setenv("BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER", "0.99")
    assert load().bot_quote_spread_offset_multiplier == pytest.approx(0.99)


def test_shadow_bankroll_is_the_only_difference(champion_env):
    """The rehearsal bankroll override touches one field and nothing else."""
    cfg = load()
    pinned = dataclasses.replace(cfg, bot_quote_bank_baseline_usd=1000.0)
    rest_live = {f.name: getattr(cfg, f.name) for f in dataclasses.fields(cfg)
                 if f.name != "bot_quote_bank_baseline_usd"}
    rest_pinned = {f.name: getattr(pinned, f.name) for f in dataclasses.fields(pinned)
                   if f.name != "bot_quote_bank_baseline_usd"}
    assert rest_live == rest_pinned
    assert pinned.bot_quote_bank_baseline_usd == pytest.approx(1000.0)


def test_queue_gate_blocks_four_minutes_allows_two(champion_env):
    """With the champion 3-minute bar: a 4-minute queue blocks, 2 allows."""
    cfg = load()
    # 400 shares ahead against 3000 SELL shares per 30m = 4.0 queue-minutes.
    blocked, why_blocked = risk.queue_clear_block(cfg, "UP", 0.47, 400.0, 3000.0, 30.0)
    assert blocked is False
    assert "maker queue" in why_blocked
    # 200 shares ahead = 2.0 queue-minutes: clears inside the bar.
    allowed, why_allowed = risk.queue_clear_block(cfg, "UP", 0.47, 200.0, 3000.0, 30.0)
    assert allowed is True
    assert why_allowed == ""


def test_champion_env_survives_os_environ_snapshot(champion_env):
    """The six champion keys are exactly the documented contract surface."""
    assert set(CHAMPION_ENV) == {
        "BOT_QUOTE_TOURNAMENT_PRESET",
        "BOT_ORDER_REQUOTE_DEAD_BAND_CENTS",
        "BOT_RISK_STOP_MODE",
        "BOT_RISK_STOP_ADVERSE_DROP_CENTS",
        "BOT_ORDER_GATE_QUEUE_CLEAR_ON",
        "BOT_ORDER_QUEUE_MAX_CLEAR_MIN",
    }
    for key in CHAMPION_ENV:
        assert os.environ.get(key), f"{key} must be set by the fixture"
