"""`price_risk_widen` must be settable for one rehearsal, default untouched.

This is a RISK CONTROL, not a tuning knob: `risk.band_risk_factor` widens the
quote by `price_risk_widen * (w + price)` because the downside of one long
share is the price paid for it, so 0.70 carries more than twice the risk of
0.30. Zeroing it quotes nearer mid everywhere.

It exists as an override because it is also the term that decides whether a
maker can reach the touch at all -- about 15 ticks anywhere near 0.50, against
a 10-tick book spread -- and that question can only be answered by resting
there. The shipped default therefore stays 0.010 and this override is scoped to
one shadow rehearsal, where nothing can sign and nothing spends.
"""
from __future__ import annotations

import os
from unittest import mock

import pytest

from core_brain.config import MakerConfig, load


class TestPriceRiskWidenOverride:
    def test_the_shipped_default_is_unchanged(self):
        assert MakerConfig().price_risk_widen == 0.010
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HUNTER_PRICE_RISK_WIDEN", None)
            assert load().price_risk_widen == 0.010

    def test_the_environment_can_zero_it(self):
        with mock.patch.dict(os.environ, {"HUNTER_PRICE_RISK_WIDEN": "0"}):
            assert load().price_risk_widen == 0.0

    @pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "-0.01", "1.5",
                                     "abc"])
    def test_a_non_finite_negative_or_oversized_value_is_refused(self, bad):
        # A NaN here would widen every quote by NaN and round to a price no
        # comparison can reject.
        with mock.patch.dict(os.environ, {"HUNTER_PRICE_RISK_WIDEN": bad}):
            with pytest.raises(ValueError, match="HUNTER_PRICE_RISK_WIDEN"):
                load()

    def test_an_empty_value_is_not_an_override(self):
        with mock.patch.dict(os.environ, {"HUNTER_PRICE_RISK_WIDEN": ""}):
            assert load().price_risk_widen == 0.010

    def test_overrides_now_floor_to_the_cent(self):
        """Touch-reach is gone; quotes floor to the standard 1-cent grid.

        The retired HUNTER_REWARD_OFFSET forced a quote to 0.495 on a
        0.495/0.505 book. That sub-cent placement is purged: even with band
        risk zeroed, the resting quote now floors to a whole cent at or below
        the touch (0.49), never a half-cent price the venue would reject.
        """
        from core_brain.quotes import Inventory, quote_resting_price

        book = {"best_bid": 0.495, "best_ask": 0.505}   # mid 0.500, 10 ticks
        with mock.patch.dict(os.environ, {"HUNTER_PRICE_RISK_WIDEN": "0"}):
            cfg = load()
        assert cfg.price_risk_widen == 0.0
        price, _, band, _ = quote_resting_price(cfg, Inventory(), "UP", book)
        assert band.extra_offset == 0.0
        # Whole-cent, floored -- never 0.495.
        assert abs(price * 100 - round(price * 100)) < 1e-9
        assert price <= 0.49 + 1e-9

    def test_zeroing_the_widen_does_not_disable_the_size_cut(self):
        # The coin-flip SIZE treatment is a separate term with its own knob.
        # Zeroing the offset term must not quietly restore full size in the
        # least readable part of the price range.
        from core_brain import risk

        with mock.patch.dict(os.environ, {"HUNTER_PRICE_RISK_WIDEN": "0"}):
            cfg = load()
        band = risk.band_risk_factor(cfg, 0.50)
        assert band.extra_offset == 0.0
        assert band.size_mult < 1.0, "size is still cut at the coin flip"
