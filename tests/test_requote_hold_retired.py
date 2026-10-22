"""The re-quote holds stay retired (#753).

#387 deleted the behaviour; the names then survived in the planner signature, in
`MakerConfig`, in every tournament preset, on the dashboard parameter cards, in
the strategy explainer, in the cancel report and in the tournament cockpit, so
an operator or an arm could still turn a knob that changed nothing. #753 deleted
them. This module is the guard: putting any of it back fails here first.

It asserts absence, not behaviour. Nothing below would have passed before #753.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from core_brain import cancel_report, config, trader_loop
from core_brain.config import TOURNAMENT_PRESETS, MakerConfig, apply_tournament_preset, load

ROOT = Path(__file__).resolve().parent.parent

#: The six `plan_orders` parameters #387 stopped reading; #753 removed them.
RETIRED_PARAMETERS = (
    "hold_queue_shares",
    "hold_below_target",
    "hedge_asks",
    "hedge_held",
    "queue_ahead",
    "visit_outcome",
)
RETIRED_FIELDS = (
    "bot_order_requote_hold_queue_shares",
    "bot_order_requote_hold_below_target_cents",
)
RETIRED_ENV = (
    "BOT_ORDER_REQUOTE_HOLD_QUEUE_SHARES",
    "BOT_ORDER_REQUOTE_HOLD_BELOW_TARGET_CENTS",
)


# --- the planner ------------------------------------------------------------

def test_plan_orders_accepts_no_dead_parameter():
    parameters = inspect.signature(trader_loop.plan_orders).parameters
    for name in RETIRED_PARAMETERS:
        assert name not in parameters, name
    # The two the body actually reads stay.
    assert "dead_band" in parameters
    assert "cfg" in parameters


def test_the_body_still_reads_the_two_live_settings():
    """The guard above must not be satisfied by deleting what plan_orders uses."""
    source = inspect.getsource(trader_loop.plan_orders)
    assert "dead_band" in source
    assert "bot_quote_size_min_shares" in source


# --- the config surface -----------------------------------------------------

def test_maker_config_declares_neither_field():
    cfg = MakerConfig()
    for name in RETIRED_FIELDS:
        assert not hasattr(cfg, name), name


@pytest.mark.parametrize("value", ["500", "0", "2000000", "nan", "-1", "abc"])
@pytest.mark.parametrize("name", RETIRED_ENV)
def test_setting_a_retired_variable_neither_changes_nor_breaks_load(name, value):
    """A stale `.env` line is ignored: no field appears, and no bound fires."""
    with mock.patch.dict(os.environ, {name: value}):
        cfg = load()
    for field in RETIRED_FIELDS:
        assert not hasattr(cfg, field), field


def test_no_preset_carries_a_hold_setting_or_advertises_one():
    assert set(TOURNAMENT_PRESETS) == {
        "conservative", "balanced", "aggressive", "prudent", "hybrid", "baseline"}
    for name, preset in TOURNAMENT_PRESETS.items():
        for key in RETIRED_FIELDS:
            assert key not in preset, f"{name} carries {key}"
        description = str(preset.get("description", "")).lower()
        assert "hold" not in description, f"{name}: {description!r}"


def test_every_preset_still_builds_a_valid_config():
    for name in TOURNAMENT_PRESETS:
        assert isinstance(apply_tournament_preset(MakerConfig(), name), MakerConfig)


# --- the operator surfaces --------------------------------------------------

def test_api_parameters_has_no_hold_card():
    from dashboard.server import app

    cards = TestClient(app).get("/api/parameters").json()["parameters"]
    for card in cards:
        for field in RETIRED_FIELDS:
            assert field not in str(card.get("key", "")), card.get("key")
            assert field not in str(card.get("name", "")), card.get("name")
    env_vars = {card.get("env_var") for card in cards}
    for name in RETIRED_ENV:
        assert name not in env_vars, name


def test_the_strategy_explainer_lists_neither_setting():
    html = (ROOT / "dashboard" / "static" / "strategy_explainer.html").read_text(
        encoding="utf-8")
    for name in RETIRED_FIELDS + RETIRED_ENV:
        assert name not in html, name


def test_the_cancel_report_offers_no_hold_advice():
    assert not hasattr(cancel_report, "_hold_state")
    text = cancel_report.format_report({
        "db_path": "x.db", "readable": True, "cancelled": 10, "unattributed": 0,
        "reasons": {"price_moved": {"count": 10, "median_queue_ahead": 5.0,
                                    "min_queue_ahead": 1.0}},
    })
    assert "requote_hold" not in text
    assert "holds can keep" not in text
    # The counts and the percentage are unchanged behaviour.
    assert "10" in text and "100% of cancels were price moves" in text


# --- the launcher -----------------------------------------------------------

@pytest.mark.parametrize("name", RETIRED_ENV)
def test_the_launcher_refuses_a_retired_environment_name(tmp_path, name):
    from scripts.shadow_tournament import build_tournament_plan

    with pytest.raises(ValueError, match=r"#387"):
        build_tournament_plan(
            issue=753,
            arms=[{"name": "arm-one", "env": {name: "500"}}],
            base_dir=tmp_path,
            run_number=1,
            check_ports=False,
        )


@pytest.mark.parametrize("field", RETIRED_FIELDS)
def test_the_launcher_refuses_a_retired_preset_key(field):
    from scripts.shadow_tournament import _preset_to_env

    with pytest.raises(ValueError, match=r"#387"):
        _preset_to_env({"name": "arm-one", field: 500.0})
