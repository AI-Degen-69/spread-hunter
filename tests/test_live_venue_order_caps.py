"""#574: venue order caps follow live settings, not the import-time snapshot."""
import os

import pytest

from core_brain import venue


@pytest.fixture
def clean_bankroll_env(monkeypatch):
    monkeypatch.delenv("BOT_QUOTE_BANK_BASELINE_USD", raising=False)
    monkeypatch.delenv("BOT_QUOTE_BANK_BASELINE_USD", raising=False)
    yield
    monkeypatch.delenv("BOT_QUOTE_BANK_BASELINE_USD", raising=False)
    monkeypatch.delenv("BOT_QUOTE_BANK_BASELINE_USD", raising=False)


def test_order_caps_follows_settings_changed_after_import(clean_bankroll_env, monkeypatch):
    """Settings changed after `venue` was imported move the enforced caps."""
    baseline = venue.order_caps()
    assert baseline == pytest.approx((25.0, 90.0))

    # A per-run override applied after import (tournament preset, starting
    # cash, test adjustment): bankroll $400 -> 25% / 90% dynamic caps.
    monkeypatch.setenv("BOT_QUOTE_BANK_BASELINE_USD", "400")
    assert venue.order_caps() == pytest.approx((100.0, 360.0))

    # The import-time snapshot is untouched -- that staleness is the bug.
    assert (venue.MAX_ORDER_USD, venue.MAX_TOTAL_USD) == pytest.approx(baseline)


def test_order_caps_accepts_live_portfolio_mark(clean_bankroll_env):
    """A live account mark scales the caps the same way the dashboard shows."""
    assert venue.order_caps(200.0) == pytest.approx((50.0, 180.0))
    # No mark falls back to the configured bankroll ($100 default).
    assert venue.order_caps(None) == pytest.approx((25.0, 90.0))
    assert venue.order_caps(os.environ.get("NOPE")) == pytest.approx((25.0, 90.0))
