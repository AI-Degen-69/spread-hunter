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
        assert cfg.max_completable_pair_cost == 1.00
        assert cfg.enforce_completable_pair_cost is True
        assert cfg.requote_dead_band == 0.03

    def test_completable_cap_is_overridable_from_the_environment(self):
        with mock.patch.dict(os.environ, {"HUNTER_COMPLETABLE_CAP": "0.98"}):
            assert load().max_completable_pair_cost == 0.98

    def test_dead_band_is_overridable_from_the_environment(self):
        with mock.patch.dict(os.environ, {"HUNTER_REQUOTE_DEAD_BAND": "0.0"}):
            assert load().requote_dead_band == 0.0


class TestCompletablePairBlock:
    def test_blocks_when_bid_plus_hedge_ask_reaches_the_cap(self):
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        why = risk.completable_pair_block(cfg, 0.55, 0.45)
        assert why is not None
        assert "completable pair" in why
        assert "1.0000" in why

    def test_blocks_when_the_pair_would_cost_more_than_a_dollar(self):
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        assert risk.completable_pair_block(cfg, 0.60, 0.45) is not None

    def test_allows_a_pair_that_completes_under_the_cap(self):
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        assert risk.completable_pair_block(cfg, 0.52, 0.45) is None

    def test_has_no_opinion_when_the_hedge_book_has_no_ask(self):
        # book_health already refuses an unreadable book, with its own wording.
        # A second, differently-worded refusal for the same condition would
        # make the operator's reason string ambiguous.
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        assert risk.completable_pair_block(cfg, 0.99, None) is None
        assert risk.completable_pair_block(cfg, 0.99, 0.0) is None

    def test_is_switchable_off(self):
        cfg = MakerConfig(max_completable_pair_cost=1.00,
                          enforce_completable_pair_cost=False)
        assert risk.completable_pair_block(cfg, 0.60, 0.45) is None

    def test_a_zero_cap_disables_the_rule(self):
        # The same escape hatch max_naked_usd and max_fleet_naked_usd have.
        cfg = MakerConfig(max_completable_pair_cost=0.0)
        assert risk.completable_pair_block(cfg, 0.60, 0.45) is None

    def test_a_tighter_cap_refuses_a_pair_a_dollar_cap_would_allow(self):
        cfg = MakerConfig(max_completable_pair_cost=0.98)
        assert risk.completable_pair_block(cfg, 0.54, 0.45) is not None


from core_brain.quotes import Inventory, decide_quotes


def _healthy_book(bid, ask, token="tok"):
    """A book that clears every book_health arm, so a test reaches the gate."""
    return {"token_id": token, "best_bid": bid, "best_ask": ask,
            "bids": {bid: 5000.0}, "asks": {ask: 5000.0}}


def _gate_cfg(**kw):
    base = dict(objective="rewards", size_mode="shares", quote_shares=120,
                min_quote_shares=50, reward_offset=0.02,
                price_band_low=0.10, price_band_high=0.90,
                max_completable_pair_cost=1.00)
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
        cfg = _gate_cfg(enforce_completable_pair_cost=False)
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
        with mock.patch.dict(os.environ, {"HUNTER_COMPLETABLE_CAP": bad}):
            if bad == "":
                assert load().max_completable_pair_cost == 1.00
            else:
                with pytest.raises(ValueError, match="HUNTER_COMPLETABLE_CAP"):
                    load()

    @pytest.mark.parametrize("bad", ["1.05", "-0.01"])
    def test_a_cap_outside_zero_to_a_dollar_is_refused(self, bad):
        with mock.patch.dict(os.environ, {"HUNTER_COMPLETABLE_CAP": bad}):
            with pytest.raises(ValueError, match="HUNTER_COMPLETABLE_CAP"):
                load()

    def test_zero_still_disables_the_cap(self):
        with mock.patch.dict(os.environ, {"HUNTER_COMPLETABLE_CAP": "0"}):
            assert load().max_completable_pair_cost == 0.0

    def test_a_dollar_is_still_accepted(self):
        with mock.patch.dict(os.environ, {"HUNTER_COMPLETABLE_CAP": "1.0"}):
            assert load().max_completable_pair_cost == 1.0

    @pytest.mark.parametrize("bad", ["nan", "inf", "-0.01", "1.5", "abc"])
    def test_a_non_finite_negative_or_oversized_dead_band_is_refused(self, bad):
        with mock.patch.dict(os.environ, {"HUNTER_REQUOTE_DEAD_BAND": bad}):
            with pytest.raises(ValueError, match="HUNTER_REQUOTE_DEAD_BAND"):
                load()

    def test_a_zero_dead_band_is_accepted(self):
        with mock.patch.dict(os.environ, {"HUNTER_REQUOTE_DEAD_BAND": "0"}):
            assert load().requote_dead_band == 0.0

    def test_the_dead_band_ceiling_is_accepted(self):
        # Both ends of the bound, so a future tightening cannot move the
        # ceiling without a test saying so. 1.00 is the instrument's whole
        # price range and already means "never re-quote"; it is the last
        # value that is a setting rather than a typo.
        with mock.patch.dict(os.environ, {"HUNTER_REQUOTE_DEAD_BAND": "1.0"}):
            assert load().requote_dead_band == 1.0

    def test_the_queue_hold_ships_off(self):
        # The threshold #304 picked off the recorded cancels is 200, but the
        # shipped default stays 0.0: this is a lever on real money and the
        # repo moves those from a recorded rehearsal, not from reading a
        # prior store. A rehearsal sets HUNTER_REQUOTE_HOLD_QUEUE.
        assert MakerConfig().requote_hold_queue_shares == 0.0

    def test_the_queue_hold_is_turnable_on_for_one_rehearsal(self):
        with mock.patch.dict(os.environ, {"HUNTER_REQUOTE_HOLD_QUEUE": "200"}):
            assert load().requote_hold_queue_shares == 200.0

    def test_zero_turns_the_queue_hold_back_off(self):
        # 0 must be accepted and must mean OFF, not "hold nothing ever": it is
        # how a rehearsal restores the symmetric re-quote and gets a result it
        # can attribute to the hold alone.
        with mock.patch.dict(os.environ, {"HUNTER_REQUOTE_HOLD_QUEUE": "0"}):
            assert load().requote_hold_queue_shares == 0.0

    @pytest.mark.parametrize("bad", ["nan", "inf", "-0.01", "abc"])
    def test_a_non_finite_negative_or_unparseable_queue_threshold_is_refused(self, bad):
        with mock.patch.dict(os.environ, {"HUNTER_REQUOTE_HOLD_QUEUE": bad}):
            with pytest.raises(ValueError, match="HUNTER_REQUOTE_HOLD_QUEUE"):
                load()

    def test_the_queue_threshold_ceiling_is_accepted(self):
        # The bound is a share count, not a price. The largest queue measured
        # on this venue was 1,999,936 shares, so 2,000,000 is the last value
        # that is a setting; past it the hold would keep every order forever.
        with mock.patch.dict(os.environ, {"HUNTER_REQUOTE_HOLD_QUEUE": "2000000"}):
            assert load().requote_hold_queue_shares == 2000000.0

    def test_a_queue_threshold_past_the_ceiling_is_refused(self):
        with mock.patch.dict(os.environ, {"HUNTER_REQUOTE_HOLD_QUEUE": "2000001"}):
            with pytest.raises(ValueError, match="HUNTER_REQUOTE_HOLD_QUEUE"):
                load()
