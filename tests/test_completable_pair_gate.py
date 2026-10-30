"""The completable-cost gate: bid + hedge ask, the price a real pair assembles at.

`max_pair_cost` asks what a pair costs if BOTH legs fill as resting bids. On a
binary market UP + DOWN = 1.00, so the legs are anti-correlated (-0.9989 on
shadow run run-2809a7161de1) and that outcome is rare by construction. These
tests cover the other question: what the pair costs when one leg fills as maker
and the other has to be TAKEN at its ask.
"""
import os
from unittest import mock

import pytest

from core_brain import risk
from core_brain.config import MakerConfig, load


class TestConfigKnobs:
    def test_defaults_gate_at_one_dollar_with_a_three_cent_dead_band(self):
        cfg = MakerConfig()
        assert cfg.bot_pos_price_max_complete_usd == 1.00
        assert cfg.bot_pos_gate_complete_cost_on is True
        assert cfg.bot_order_requote_dead_band_usd == 0.03

    def test_completable_cap_is_overridable_from_the_environment(self):
        with mock.patch.dict(os.environ, {"BOT_POS_PRICE_MAX_COMPLETE_USD": "0.98"}):
            assert load().bot_pos_price_max_complete_usd == 0.98

    def test_dead_band_is_overridable_from_the_environment(self):
        with mock.patch.dict(os.environ, {"BOT_ORDER_REQUOTE_DEAD_BAND_USD": "0.0"}):
            assert load().bot_order_requote_dead_band_usd == 0.0


class TestCompletablePairBlock:
    def test_blocks_when_bid_plus_hedge_ask_reaches_the_cap(self):
        cfg = MakerConfig(bot_pos_price_max_complete_usd=1.00)
        why = risk.completable_pair_block(cfg, 0.55, 0.45)
        assert why is not None
        assert "completable pair" in why
        assert "1.0000" in why

    def test_blocks_when_the_pair_would_cost_more_than_a_dollar(self):
        cfg = MakerConfig(bot_pos_price_max_complete_usd=1.00)
        assert risk.completable_pair_block(cfg, 0.60, 0.45) is not None

    def test_allows_a_pair_that_completes_under_the_cap(self):
        cfg = MakerConfig(bot_pos_price_max_complete_usd=1.00)
        assert risk.completable_pair_block(cfg, 0.52, 0.45) is None

    def test_has_no_opinion_when_the_hedge_book_has_no_ask(self):
        # book_health already refuses an unreadable book, with its own wording.
        # A second, differently-worded refusal for the same condition would
        # make the operator's reason string ambiguous.
        cfg = MakerConfig(bot_pos_price_max_complete_usd=1.00)
        assert risk.completable_pair_block(cfg, 0.99, None) is None
        assert risk.completable_pair_block(cfg, 0.99, 0.0) is None

    def test_is_switchable_off(self):
        cfg = MakerConfig(bot_pos_price_max_complete_usd=1.00,
                          bot_pos_gate_complete_cost_on=False)
        assert risk.completable_pair_block(cfg, 0.60, 0.45) is None

    def test_a_zero_cap_disables_the_rule(self):
        # The same escape hatch max_naked_usd and max_fleet_naked_usd have.
        cfg = MakerConfig(bot_pos_price_max_complete_usd=0.0)
        assert risk.completable_pair_block(cfg, 0.60, 0.45) is None

    def test_a_tighter_cap_refuses_a_pair_a_dollar_cap_would_allow(self):
        cfg = MakerConfig(bot_pos_price_max_complete_usd=0.98)
        assert risk.completable_pair_block(cfg, 0.54, 0.45) is not None

    def test_canonical_gate_and_cap_without_warning(self):
        import warnings

        cfg = MakerConfig(bot_pos_price_max_complete_usd=0.97)
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always")
            # 0.50 + 0.47 == 0.97, equals cap -> blocked (cost >= cap)
            assert risk.completable_pair_block(cfg, 0.50, 0.47) is not None
            # 0.50 + 0.46 == 0.96, under cap -> allowed
            assert risk.completable_pair_block(cfg, 0.50, 0.46) is None

            # With gate off -> allowed
            cfg_off = MakerConfig(bot_pos_price_max_complete_usd=0.97, bot_pos_gate_complete_cost_on=False)
            assert risk.completable_pair_block(cfg_off, 0.50, 0.47) is None

        for w in recorded:
            if "risk.py" in str(w.filename):
                assert not any(name in str(w.message) for name in ("enforce_completable_pair_cost", "max_completable_pair_cost")), f"Unexpected warning: {w.message}"


from core_brain.quotes import Inventory, decide_quotes


def _healthy_book(bid, ask, token="tok"):
    """A book that clears every book_health arm, so a test reaches the gate."""
    return {"token_id": token, "best_bid": bid, "best_ask": ask,
            "bids": {bid: 5000.0}, "asks": {ask: 5000.0}}


def _gate_cfg(**kw):
    base = dict(bot_pos_objective="rewards", bot_quote_size_mode="shares", bot_quote_size_shares=120,
                bot_quote_size_min_shares=50, bot_quote_spread_static_offset_cents=0.02,
                bot_quote_price_band_min_cents=0.10, bot_quote_price_band_max_cents=0.90,
                bot_pos_price_max_complete_usd=1.00)
    base.update(kw)
    return MakerConfig(**base)


class TestGateInDecideQuotes:
    def test_declines_a_market_whose_pair_cannot_be_completed_under_a_dollar(self):
        # UP 0.55/0.60 and DOWN 0.41/0.47 sum to 1.07 at the asks. With the
        # reward objective each leg rests ~3c under mid -- measured 0.542 and
        # 0.410 -- so completing costs 0.542+0.47=1.012 and 0.410+0.60=1.010.
        # Both over the cap, and `why` names the completable arm on both sides.
        cfg = _gate_cfg()
        up = _healthy_book(0.55, 0.60, "tok-up")
        down = _healthy_book(0.41, 0.47, "tok-dn")
        intents, why = decide_quotes(cfg, up, down, Inventory(), 1e9, None)
        assert intents == []
        assert why.count("completable pair") == 2

    def test_still_quotes_both_legs_of_a_tight_market(self):
        # The regression guard: this rule must not empty the book. UP 0.50/0.52
        # and DOWN 0.46/0.48 rest at 0.49 and 0.45; completing costs 0.97 both
        # ways, comfortably under the cap.
        cfg = _gate_cfg()
        up = _healthy_book(0.50, 0.52, "tok-up")
        down = _healthy_book(0.46, 0.48, "tok-dn")
        intents, why = decide_quotes(cfg, up, down, Inventory(), 1e9, None)
        assert len(intents) == 2
        assert not why

    def test_switching_the_gate_off_restores_the_wide_market_quote(self):
        cfg = _gate_cfg(bot_pos_gate_complete_cost_on=False)
        up = _healthy_book(0.55, 0.57, "tok-up")
        down = _healthy_book(0.43, 0.46, "tok-dn")
        intents, _ = decide_quotes(cfg, up, down, Inventory(), 1e9, None)
        assert len(intents) == 2

    def test_stands_down_once_we_hold_the_hedge_leg(self):
        # 100 DOWN shares held at 0.43: completion is not needed, so the gate
        # has no business refusing the UP leg that would finish the pair at
        # 0.54 + 0.43 = 0.97. The UP quote must survive.
        cfg = _gate_cfg()
        up = _healthy_book(0.55, 0.57, "tok-up")
        down = _healthy_book(0.43, 0.46, "tok-dn")
        inv = Inventory(down_shares=100.0, down_cost=43.0)
        intents, why = decide_quotes(cfg, up, down, inv, 1e9, None)
        assert [i.side for i in intents] == ["UP"]


class TestOverrideValidation:
    """CodeRabbit round 1: `float()` accepts "nan" and "inf".

    A cap of NaN makes `cost >= cap` false for every cost, so the gate reads as
    enforced and silently permits a pair over $1.00 -- the one outcome this
    repo exists to prevent. A finite cap above $1.00 permits the same loss on
    purpose. Both must be refused at load, loudly, rather than discovered from
    the fills ledger.
    """

    @pytest.mark.parametrize("bad", ["nan", "NaN", "inf", "-inf", "abc", ""])
    def test_a_non_finite_or_unparseable_cap_is_refused(self, bad):
        # "" is the one blank that must NOT raise -- it means "unset".
        with mock.patch.dict(os.environ, {"BOT_POS_PRICE_MAX_COMPLETE_USD": bad}):
            if bad == "":
                assert load().bot_pos_price_max_complete_usd == 1.00
            else:
                with pytest.raises(ValueError, match="BOT_POS_PRICE_MAX_COMPLETE_USD"):
                    load()

    @pytest.mark.parametrize("bad", ["1.05", "-0.01"])
    def test_a_cap_outside_zero_to_a_dollar_is_refused(self, bad):
        with mock.patch.dict(os.environ, {"BOT_POS_PRICE_MAX_COMPLETE_USD": bad}):
            with pytest.raises(ValueError, match="BOT_POS_PRICE_MAX_COMPLETE_USD"):
                load()

    def test_zero_still_disables_the_cap(self):
        with mock.patch.dict(os.environ, {"BOT_POS_PRICE_MAX_COMPLETE_USD": "0"}):
            assert load().bot_pos_price_max_complete_usd == 0.0

    def test_a_dollar_is_still_accepted(self):
        with mock.patch.dict(os.environ, {"BOT_POS_PRICE_MAX_COMPLETE_USD": "1.0"}):
            assert load().bot_pos_price_max_complete_usd == 1.0

    @pytest.mark.parametrize("bad", ["nan", "inf", "-0.01", "1.5", "abc"])
    def test_a_non_finite_negative_or_oversized_dead_band_is_refused(self, bad):
        with mock.patch.dict(os.environ, {"BOT_ORDER_REQUOTE_DEAD_BAND_USD": bad}):
            with pytest.raises(ValueError, match="BOT_ORDER_REQUOTE_DEAD_BAND_USD"):
                load()

    def test_a_zero_dead_band_is_accepted(self):
        with mock.patch.dict(os.environ, {"BOT_ORDER_REQUOTE_DEAD_BAND_USD": "0"}):
            assert load().bot_order_requote_dead_band_usd == 0.0

    def test_the_dead_band_ceiling_is_accepted(self):
        # Both ends of the bound, so a future tightening cannot move the
        # ceiling without a test saying so. 1.00 is the instrument's whole
        # price range and already means "never re-quote"; it is the last
        # value that is a setting rather than a typo.
        with mock.patch.dict(os.environ, {"BOT_ORDER_REQUOTE_DEAD_BAND_USD": "1.0"}):
            assert load().bot_order_requote_dead_band_usd == 1.0

