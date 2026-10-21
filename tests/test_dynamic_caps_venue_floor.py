"""Dynamic caps never fall below the venue's own floor (#3).

Caps scale with the account: 25% of portfolio value per order, 90% total. On a
small account the percentage cap can land under the cost of one share, and the
venue refuses anything below `single_buy_saver.MIN_ORDER_SHARES` — so the bot
would rest nothing while believing it was correctly sized. `max_order_usd` is
floored at the venue minimum notional, bounded by the total cap so the
portfolio ceiling still wins on an account too small to fund one share.
"""
from __future__ import annotations

import pytest

from core_brain.config import VENUE_MIN_NOTIONAL_USD, MakerConfig, derive_dynamic_caps


@pytest.fixture
def cfg() -> MakerConfig:
    return MakerConfig()


def test_caps_scale_with_the_account_at_normal_size(cfg):
    # Arrange / Act
    caps = derive_dynamic_caps(cfg, 200.0)

    # Assert — the floor is inert well above it.
    assert caps["bot_risk_exposure_max_order_usd"] == pytest.approx(50.0)
    assert caps["bot_risk_exposure_max_total_usd"] == pytest.approx(180.0)


def test_the_order_cap_is_floored_at_the_venue_minimum(cfg):
    # Arrange — 25% of $3.00 is $0.75, under one share's cost.
    caps = derive_dynamic_caps(cfg, 3.0)

    # Act / Assert
    assert caps["bot_risk_exposure_max_order_usd"] == pytest.approx(VENUE_MIN_NOTIONAL_USD)


def test_the_floor_never_outruns_the_total_cap(cfg):
    # Arrange — an account too small to fund one share at all.
    caps = derive_dynamic_caps(cfg, 0.5)

    # Act / Assert — the ceiling wins; the order is refused, not sized past it.
    assert caps["bot_risk_exposure_max_order_usd"] <= caps["bot_risk_exposure_max_total_usd"]
    assert caps["bot_risk_exposure_max_order_usd"] == pytest.approx(caps["bot_risk_exposure_max_total_usd"])


def test_the_fallback_bankroll_gets_the_same_floor(cfg):
    # Arrange — no portfolio read at all (first boot, failed balance read).
    tiny = MakerConfig(bot_quote_bank_baseline_usd=3.0)

    # Act
    caps = derive_dynamic_caps(tiny, None)

    # Assert
    assert caps["bot_risk_exposure_max_order_usd"] == pytest.approx(VENUE_MIN_NOTIONAL_USD)
