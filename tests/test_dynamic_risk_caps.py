"""Tests for dynamic portfolio-scaled risk caps and max_pair_cost = $0.99."""
import pytest
from unittest.mock import MagicMock

from core_brain.config import MakerConfig, derive_dynamic_caps, load as load_config
from core_brain.quotes import Inventory
from core_brain import risk
from core_brain.trader_loop import _fleet_state


def test_default_config_risk_parameters():
    """Verify default risk percentages and max_pair_cost."""
    cfg = MakerConfig()
    assert cfg.bot_pos_price_max_pair_cost_usd == 0.99
    assert cfg.bot_risk_exposure_max_naked_pct == 0.06
    assert cfg.bot_risk_exposure_max_order_pct == 0.25
    assert cfg.bot_risk_exposure_bankroll_ceiling_pct == 0.90
    assert cfg.bot_risk_exposure_max_naked_usd == 6.0
    assert cfg.bot_risk_exposure_max_order_usd == 25.0
    assert cfg.bot_risk_exposure_max_total_usd == 90.0


def test_derive_dynamic_caps_100_usd():
    """At $100 portfolio value, caps match expected baseline."""
    cfg = MakerConfig(bot_quote_bank_baseline_usd=100.0)
    caps = derive_dynamic_caps(cfg, 100.0)
    assert caps["bot_quote_bank_baseline_usd"] == 100.0
    assert caps["bot_risk_exposure_max_naked_usd"] == 6.00   # 6% of $100
    assert caps["bot_risk_exposure_max_order_usd"] == 25.00  # 25% of $100
    assert caps["bot_risk_exposure_max_total_usd"] == 90.00  # 90% of $100


def test_derive_dynamic_caps_90_usd():
    """At $90 portfolio value, caps scale down proportionally."""
    cfg = MakerConfig()
    caps = derive_dynamic_caps(cfg, 90.0)
    assert caps["bot_quote_bank_baseline_usd"] == 90.0
    assert caps["bot_risk_exposure_max_naked_usd"] == 5.40   # 6% of $90
    assert caps["bot_risk_exposure_max_order_usd"] == 22.50  # 25% of $90
    assert caps["bot_risk_exposure_max_total_usd"] == 81.00  # 90% of $90


def test_derive_dynamic_caps_50_usd():
    """At $50 portfolio value, caps scale down to 50% baseline."""
    cfg = MakerConfig()
    caps = derive_dynamic_caps(cfg, 50.0)
    assert caps["bot_quote_bank_baseline_usd"] == 50.0
    assert caps["bot_risk_exposure_max_naked_usd"] == 3.00   # 6% of $50
    assert caps["bot_risk_exposure_max_order_usd"] == 12.50  # 25% of $50
    assert caps["bot_risk_exposure_max_total_usd"] == 45.00  # 90% of $50


def test_derive_dynamic_caps_fallback():
    """When portfolio value is None or non-positive, fallback to cfg.bankroll_usd."""
    cfg = MakerConfig(bot_quote_bank_baseline_usd=100.0)
    caps_none = derive_dynamic_caps(cfg, None)
    assert caps_none["bot_quote_bank_baseline_usd"] == 100.0
    assert caps_none["bot_risk_exposure_max_naked_usd"] == 6.00

    caps_zero = derive_dynamic_caps(cfg, 0.0)
    assert caps_zero["bot_quote_bank_baseline_usd"] == 100.0
    assert caps_zero["bot_risk_exposure_max_naked_usd"] == 6.00


def test_fleet_state_adopts_dynamic_caps_from_account_mark():
    """_fleet_state queries registry and derives dynamic caps for the cycle."""
    cfg = MakerConfig()
    mock_reg = MagicMock()
    mock_reg.get_latest_account_mark.return_value = {
        "account_value_usd": 90.0,
        "collateral_usd": 70.0,
        "positions_value_usd": 20.0,
    }

    state = _fleet_state(mock_reg, cfg)
    assert state["bot_risk_exposure_max_naked_usd"] == 5.40
    assert state["bot_risk_exposure_max_order_usd"] == 22.50
    assert state["bot_risk_exposure_max_total_usd"] == 81.00


def test_derive_dynamic_caps_edge_cases():
    """Verify derive_dynamic_caps rejects nan, inf, and negative inputs safely."""
    cfg = MakerConfig(bot_quote_bank_baseline_usd=100.0)
    
    # NaN and Infs should fall back safely to cfg.bankroll_usd
    caps_nan = derive_dynamic_caps(cfg, float("nan"))
    assert caps_nan["bot_quote_bank_baseline_usd"] == 100.0
    assert caps_nan["bot_risk_exposure_max_naked_usd"] == 6.00

    caps_inf = derive_dynamic_caps(cfg, float("inf"))
    assert caps_inf["bot_quote_bank_baseline_usd"] == 100.0

    caps_ninf = derive_dynamic_caps(cfg, float("-inf"))
    assert caps_ninf["bot_quote_bank_baseline_usd"] == 100.0

    caps_neg = derive_dynamic_caps(cfg, -50.0)
    assert caps_neg["bot_quote_bank_baseline_usd"] == 100.0


def test_derive_dynamic_caps_invalid_percentages():
    """Verify derive_dynamic_caps falls back to defaults for invalid percentage params."""
    # Percentage > 1.0 or <= 0 or NaN should fall back to defaults (0.06, 0.25, 0.90)
    cfg_invalid = MakerConfig(bot_risk_exposure_max_naked_pct=1.5, bot_risk_exposure_max_order_pct=-0.1, bot_risk_exposure_bankroll_ceiling_pct=float("nan"))
    caps = derive_dynamic_caps(cfg_invalid, 100.0)
    assert caps["bot_risk_exposure_max_naked_usd"] == 6.00   # default 6%
    assert caps["bot_risk_exposure_max_order_usd"] == 25.00  # default 25%
    assert caps["bot_risk_exposure_max_total_usd"] == 90.00  # default 90%


def test_registry_get_latest_account_mark_run_scoping(tmp_path):
    """get_latest_account_mark strictly scopes to the active run without cross-run bleed."""
    from core_brain.order_registry import OrderRegistry, set_run_id, get_run_id

    orig_rid = get_run_id()
    try:
        db_path = tmp_path / "orders.db"
        reg = OrderRegistry(db_path=str(db_path))

        # Record mark for run-1
        reg.log_account_mark(
            {"collateral_usd": 50.0, "positions_value_usd": 50.0, "account_value_usd": 100.0, "source": "polymarket"},
            ts=100.0,
            run_id="run-1",
        )

        # Record mark for run-2 at a later timestamp
        reg.log_account_mark(
            {"collateral_usd": 40.0, "positions_value_usd": 40.0, "account_value_usd": 80.0, "source": "polymarket"},
            ts=200.0,
            run_id="run-2",
        )

        # Active run is run-2 -> should retrieve run-2 mark ($80)
        set_run_id("run-2")
        mark_run2 = reg.get_latest_account_mark()
        assert mark_run2 is not None
        assert mark_run2["run_id"] == "run-2"
        assert mark_run2["account_value_usd"] == 80.0

        # Explicitly query run-1 -> should retrieve run-1 mark ($100)
        mark_run1 = reg.get_latest_account_mark(run_id="run-1")
        assert mark_run1 is not None
        assert mark_run1["run_id"] == "run-1"
        assert mark_run1["account_value_usd"] == 100.0

        # Query a run with no marks -> returns None (no cross-run bleed)
        set_run_id("run-3")
        mark_run3 = reg.get_latest_account_mark()
        assert mark_run3 is None
    finally:
        set_run_id(orig_rid)


def test_parameters_endpoint_basis_label():
    """get_parameters displays 'account value' when live mark exists, 'bankroll' otherwise."""
    import json
    from dashboard.server import get_parameters

    # 1. Fallback / no mark -> 'bankroll' basis
    res_fallback = get_parameters()
    data_fallback = json.loads(res_fallback.body.decode())
    naked_fallback = next(p for p in data_fallback["parameters"] if p["name"] == "bot_risk_exposure_max_naked_usd")
    assert "bankroll" in naked_fallback["value"]

    # 2. Live mark provided -> 'account value' basis
    mock_reg = MagicMock()
    mock_reg.get_latest_account_mark.return_value = {"account_value_usd": 90.0}
    res_live = get_parameters(registry=mock_reg)
    data_live = json.loads(res_live.body.decode())
    naked_live = next(p for p in data_live["parameters"] if p["name"] == "bot_risk_exposure_max_naked_usd")
    assert "account value" in naked_live["value"]
    assert "$5.40" in naked_live["value"]


def test_fleet_state_float_marks_fallback():
    """_fleet_state uses float_marks unrealized PnL when account_marks is absent."""
    cfg = MakerConfig(bot_quote_bank_baseline_usd=100.0)
    mock_reg = MagicMock()
    mock_reg.get_latest_account_mark.return_value = None
    mock_reg.get_all_float_marks.return_value = [
        {"ts": 100.0, "unrealized_usd": -10.0, "committed_open_usd": 0.0, "naked_usd": 0.0}
    ]

    # $100 bankroll - $10 unrealized = $90 portfolio value
    state = _fleet_state(mock_reg, cfg)
    assert state["bot_quote_bank_baseline_usd"] == 90.0
    assert state["bot_risk_exposure_max_naked_usd"] == 5.40
    assert state["bot_risk_exposure_max_order_usd"] == 22.50
    assert state["bot_risk_exposure_max_total_usd"] == 81.00


def test_max_pair_cost_enforcement_at_99_cents():
    """risk.hard_block enforces the $0.99 ceiling on pair cost."""
    cfg = MakerConfig(bot_pos_price_max_pair_cost_usd=0.99)
    # Inventory holding 10 UP shares at 0.50 avg cost
    inv = Inventory(up_shares=10, up_cost=5.0)
    book = {"best_bid": 0.48, "best_ask": 0.52}

    # DOWN bid at 0.495 -> combined pair cost = 0.50 + 0.495 = 0.995 >= 0.99 -> BLOCKED
    blocked_reason = risk.hard_block(cfg, inv, "DOWN", 0.495, book, book)
    assert blocked_reason is not None
    assert "bot_pos_price_max_pair_cost_usd" in blocked_reason or "pair" in blocked_reason or "0.99" in blocked_reason

    # DOWN bid at 0.485 -> combined pair cost = 0.50 + 0.485 = 0.985 < 0.99 -> ALLOWED
    allowed_reason = risk.hard_block(cfg, inv, "DOWN", 0.485, book, book)
    assert allowed_reason is None


def test_dynamic_max_naked_usd_risk_utilization():
    """Risk utilization and sizing ladder adapt to dynamically lowered max_naked_usd."""
    cfg_90 = MakerConfig(bot_risk_exposure_max_naked_usd=5.40)
    inv = Inventory(up_shares=10, up_cost=5.40)  # exactly $5.40 naked on UP
    book = {"best_bid": 0.48, "best_ask": 0.52}

    util = risk.risk_utilization(cfg_90, inv, "UP")
    assert util == pytest.approx(1.0)

    # Hard block fires when naked reaches max_naked_usd
    block = risk.hard_block(cfg_90, inv, "UP", 0.54, book, book)
    assert block is not None
    assert "naked" in block


def test_derive_dynamic_caps_small_portfolio_enforces_hard_block():
    """A small positive portfolio ($0.01) retains a positive unrounded cap and enforces hard_block."""
    cfg_base = MakerConfig()
    caps = derive_dynamic_caps(cfg_base, 0.01)
    assert caps["bot_risk_exposure_max_naked_usd"] > 0.0
    assert caps["bot_risk_exposure_max_naked_usd"] == pytest.approx(0.0006)

    # Apply derived cap to config (frozen dataclass)
    cfg = MakerConfig(bot_risk_exposure_max_naked_usd=caps["bot_risk_exposure_max_naked_usd"])

    # Holding 1 share with $0.01 naked exposure
    inv = Inventory(up_shares=1, up_cost=0.01)
    book = {"best_bid": 0.48, "best_ask": 0.52}

    # Verify hard_block stops further naked exposure ($0.01 >= $0.0006)
    block = risk.hard_block(cfg, inv, "UP", 0.50, book, book)
    assert block is not None
    assert "naked" in block


