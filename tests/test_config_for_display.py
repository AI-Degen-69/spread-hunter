"""`config.load(for_display=True)` for read-only consumers.

The two rehearsal-gated trial knobs -- `BOT_POS_PRICE_MAX_PAIR_COST_USD` and
`BOT_QUOTE_WIDE_BOOK_TRIAL` -- RAISE outside a declared rehearsal so a live trader
cannot loosen a loss-prevention cap by accident. The dashboard is not that
trader: its KPI and parameter endpoints import `core_brain.config` only to
display numbers, and they inherit the operator's shell environment because
`Start-Process` copies it. Before `for_display`, a knob exported to steer a
rehearsal also 500-ed every dashboard panel that imports `core_brain.kpi`.

`for_display=True` drops the inherited knob with a logged warning and keeps the
shipped ceiling. The money path never passes the flag, so its refusal is
unchanged -- pinned here too.
"""
from __future__ import annotations

import logging

import pytest

from core_brain import config as core_config
from core_brain import rehearsal
from core_brain.config import MakerConfig


@pytest.fixture(autouse=True)
def _clean_rehearsal_flag():
    rehearsal.reset_for_test()
    yield
    rehearsal.reset_for_test()


# --- BOT_POS_PRICE_MAX_PAIR_COST_USD -----------------------------------------------------


def test_pair_cost_cap_still_raises_on_the_money_path(monkeypatch):
    monkeypatch.setenv("BOT_POS_PRICE_MAX_PAIR_COST_USD", "0.995")

    with pytest.raises(ValueError) as e:
        core_config.load()

    assert "unable to place an order" in str(e.value)


def test_pair_cost_cap_falls_back_to_shipped_for_display(monkeypatch, caplog):
    monkeypatch.setenv("BOT_POS_PRICE_MAX_PAIR_COST_USD", "0.995")

    with caplog.at_level(logging.WARNING):
        cfg = core_config.load(for_display=True)

    assert cfg.max_pair_cost == MakerConfig.max_pair_cost
    assert cfg.max_pair_cost == 0.99
    assert "BOT_POS_PRICE_MAX_PAIR_COST_USD ignored" in caplog.text


def test_pair_cost_cap_still_applies_for_display_inside_a_rehearsal(monkeypatch):
    # for_display does not disable the knob; it only stops the refusal from
    # crashing a reader. A real rehearsal still gets the trial value.
    rehearsal.declare_rehearsal()
    monkeypatch.setenv("BOT_POS_PRICE_MAX_PAIR_COST_USD", "0.995")

    cfg = core_config.load(for_display=True)

    assert cfg.max_pair_cost == 0.995


# --- BOT_QUOTE_WIDE_BOOK_TRIAL ---------------------------------------------------


def test_wide_book_trial_still_raises_on_the_money_path(monkeypatch):
    monkeypatch.setenv("BOT_QUOTE_WIDE_BOOK_TRIAL", "0.05")

    with pytest.raises(ValueError):
        core_config.load()


def test_wide_book_trial_falls_back_to_shipped_for_display(monkeypatch, caplog):
    monkeypatch.setenv("BOT_QUOTE_WIDE_BOOK_TRIAL", "0.05")

    with caplog.at_level(logging.WARNING):
        cfg = core_config.load(for_display=True)

    assert cfg.wide_book_trial is None
    assert cfg.max_book_spread == MakerConfig.max_book_spread
    # The picking bar degrades with the gate (#572): the page shows the shipped
    # contract, never a trial value the process it describes cannot act on.
    assert cfg.select_max_book_spread == MakerConfig().select_max_book_spread == 0.0205
    assert "BOT_QUOTE_WIDE_BOOK_TRIAL ignored" in caplog.text


def test_clean_environment_is_unaffected_by_the_flag():
    # No knob set: for_display changes nothing.
    plain = core_config.load()
    display = core_config.load(for_display=True)

    assert plain.max_pair_cost == display.max_pair_cost
    assert plain.max_book_spread == display.max_book_spread


def test_kpi_module_imports_with_an_inherited_trial_knob(monkeypatch):
    # The regression: `core_brain.kpi` runs `load_cfg()` at import, and every
    # dashboard endpoint that imports it 500-ed when the knob was in the env.
    monkeypatch.setenv("BOT_POS_PRICE_MAX_PAIR_COST_USD", "0.995")
    import importlib

    from core_brain import kpi

    importlib.reload(kpi)  # must not raise

    assert kpi._CFG.max_pair_cost == MakerConfig.max_pair_cost


# --- UNREAD SETTINGS PURGE (#576) ---------------------------------------------


def test_unread_settings_purged_from_maker_configs():
    import dataclasses
    from scoring import config as scoring_config

    purged = {
        "float_mark_retention_days",
        "scarcity_close_threshold",
        "est_reward_pool_usd",
        "stat_gate_alpha",
        "stat_gate_beta",
        "experiment_census_markets",
        "experiment_verdict_markets",
        "hedge_fillable_min_rate",
        "balance_hedge_sec",
        "sim_only",
        # #686: the 15s remaining-time floor is gone from both configs.
        "bot_order_min_t_remaining_sec",
        "min_t_remaining_sec",
    }

    core_fields = {f.name for f in dataclasses.fields(core_config.MakerConfig)}
    scoring_fields = {f.name for f in dataclasses.fields(scoring_config.MakerConfig)}

    core_lingering = core_fields.intersection(purged)
    scoring_lingering = scoring_fields.intersection(purged)

    assert not core_lingering, f"Unread fields lingering in core_brain.config.MakerConfig: {core_lingering}"
    assert not scoring_lingering, f"Unread fields lingering in scoring.config.MakerConfig: {scoring_lingering}"


def test_retained_neighbors_still_declared():
    import dataclasses
    from scoring import config as scoring_config

    core_fields = {f.name for f in dataclasses.fields(core_config.MakerConfig)}
    scoring_fields = {f.name for f in dataclasses.fields(scoring_config.MakerConfig)}

    expected_core = {
        "stat_gate_deltas",
        "stat_gate_threshold_pct",
        "stat_gate_bankroll_pct",
        "stat_gate_target_closes",
        "stranded_max_wait_sec",
        "pairs_exit_window_sec",
    }
    expected_scoring = {
        "select_min_top3_depth_usd",
        # `select_max_book_spread` is derived (a property) since #572, so the
        # declared scoring knob to guard is the share it derives from.
        "select_spread_pct",
    }

    missing_core = expected_core - core_fields
    missing_scoring = expected_scoring - scoring_fields

    assert not missing_core, f"Retained core fields missing: {missing_core}"
    assert not missing_scoring, f"Retained scoring fields missing: {missing_scoring}"

