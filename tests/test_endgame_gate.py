"""The endgame proximity gate (#240): no new pair legs near resolution on a slow loop.

Post-mortem of the largest shadow loss (close id 64, spx-up-or-down
2026-09-18, -$1.14): a pair posted with ~32min to resolution, the loop did
not revisit the market for 2m52s (full-cycle cadence ~3.5min), one leg
filled alone into convergence, and the saver sold it at $0.01.
"""
import os
from unittest import mock

import pytest

from core_brain.config import MakerConfig, load
from core_brain.order_registry import OrderRegistry, registry_cycle_cadence_sec
from core_brain.quotes import Inventory, decide_quotes


def _healthy_book(bid, ask, token="tok"):
    return {"token_id": token, "best_bid": bid, "best_ask": ask,
            "bids": {bid: 5000.0}, "asks": {ask: 5000.0}}


def _gate_cfg(**kw):
    base = dict(objective="rewards", size_mode="shares", quote_shares=120,
                min_quote_shares=50, reward_offset=0.02,
                price_band_low=0.10, price_band_high=0.90,
                max_completable_pair_cost=1.00)
    base.update(kw)
    return MakerConfig(**base)


def _tight_books():
    return (_healthy_book(0.50, 0.52, "tok-up"),
            _healthy_book(0.46, 0.48, "tok-dn"))


class TestEndgameConfigKnobs:
    def test_shipped_defaults_keep_the_gate_off(self):
        cfg = MakerConfig()
        assert cfg.enforce_endgame_gate is False
        assert cfg.endgame_horizon_min == 45.0
        assert cfg.endgame_max_cadence_sec == 120.0
        assert cfg.endgame_action == "refuse"
        assert cfg.endgame_deepen_offset == 0.02
        assert cfg.observed_cadence_sec is None

    def test_environment_overrides(self):
        env = {"HUNTER_ENDGAME_GATE": "1",
               "HUNTER_ENDGAME_HORIZON_MIN": "30",
               "HUNTER_ENDGAME_MAX_CADENCE_SEC": "180",
               "HUNTER_ENDGAME_ACTION": "deepen",
               "HUNTER_ENDGAME_DEEPEN_OFFSET": "0.03"}
        with mock.patch.dict(os.environ, env):
            cfg = load()
        assert cfg.enforce_endgame_gate is True
        assert cfg.endgame_horizon_min == 30.0
        assert cfg.endgame_max_cadence_sec == 180.0
        assert cfg.endgame_action == "deepen"
        assert cfg.endgame_deepen_offset == 0.03

    @pytest.mark.parametrize("var", ["HUNTER_ENDGAME_HORIZON_MIN",
                                     "HUNTER_ENDGAME_MAX_CADENCE_SEC",
                                     "HUNTER_ENDGAME_DEEPEN_OFFSET"])
    @pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "abc"])
    def test_non_finite_or_garbage_values_are_refused(self, var, bad):
        with mock.patch.dict(os.environ, {var: bad}):
            with pytest.raises(ValueError, match=var):
                load()

    def test_out_of_range_values_are_refused(self):
        with mock.patch.dict(os.environ, {"HUNTER_ENDGAME_HORIZON_MIN": "2000"}):
            with pytest.raises(ValueError, match="HUNTER_ENDGAME_HORIZON_MIN"):
                load()
        with mock.patch.dict(os.environ, {"HUNTER_ENDGAME_DEEPEN_OFFSET": "-0.01"}):
            with pytest.raises(ValueError, match="HUNTER_ENDGAME_DEEPEN_OFFSET"):
                load()

    def test_unknown_action_is_refused(self):
        with mock.patch.dict(os.environ, {"HUNTER_ENDGAME_ACTION": "widen"}):
            with pytest.raises(ValueError, match="HUNTER_ENDGAME_ACTION"):
                load()

    def test_empty_values_are_not_overrides(self):
        env = {"HUNTER_ENDGAME_GATE": "", "HUNTER_ENDGAME_HORIZON_MIN": "",
               "HUNTER_ENDGAME_MAX_CADENCE_SEC": "", "HUNTER_ENDGAME_ACTION": "",
               "HUNTER_ENDGAME_DEEPEN_OFFSET": ""}
        with mock.patch.dict(os.environ, env):
            cfg = load()
        assert cfg.enforce_endgame_gate is False
        assert cfg.endgame_horizon_min == 45.0


class TestCycleCadenceHelper:
    def _seed(self, reg, rows):
        with reg._conn() as conn:
            for cycle, ts in rows:
                conn.execute(
                    "INSERT INTO cycle_intent (ts,cycle,market_slug,run_id)"
                    " VALUES (?,?,?,?)",
                    (ts, cycle, "m", "r"),
                )
            conn.commit()

    def test_returns_none_without_rows(self, tmp_path):
        reg = OrderRegistry(db_path=tmp_path / "t.db")
        assert registry_cycle_cadence_sec(reg) is None

    def test_returns_none_with_a_single_cycle(self, tmp_path):
        reg = OrderRegistry(db_path=tmp_path / "t.db")
        self._seed(reg, [(1, 1000.0), (1, 1001.0)])
        assert registry_cycle_cadence_sec(reg) is None

    def test_median_gap_across_cycles(self, tmp_path):
        reg = OrderRegistry(db_path=tmp_path / "t.db")
        self._seed(reg, [(1, 1000.0), (1, 1005.0), (2, 1210.0),
                         (3, 1300.0), (4, 1620.0)])
        # cycle starts at 1000/1210/1300/1620 -> gaps 210/90/320.
        assert registry_cycle_cadence_sec(reg) == pytest.approx(210.0)

    def test_returns_none_without_a_registry(self):
        assert registry_cycle_cadence_sec(None) is None


class TestEndgameGateRegression:
    """The S&P 2026-09-18 loss shape: 32min left, ~3.5min loop cadence."""

    def test_flat_pair_is_refused_inside_the_endgame(self):
        up, down = _tight_books()
        cfg = _gate_cfg(enforce_endgame_gate=True,
                        observed_cadence_sec=210.0)
        intents, why = decide_quotes(cfg, up, down, Inventory(), 32 * 60, None)
        assert intents == []
        assert why.count("endgame_gate") == 2

    def test_same_inputs_post_with_the_gate_off(self):
        # Fails without the change: proves the gate, not the books, refuses.
        up, down = _tight_books()
        cfg = _gate_cfg()
        intents, why = decide_quotes(cfg, up, down, Inventory(), 32 * 60, None)
        assert len(intents) == 2
        assert not why

    def test_deepen_reprices_instead_of_refusing(self):
        up, down = _tight_books()
        plain = _gate_cfg()
        plain_intents, _ = decide_quotes(plain, up, down, Inventory(),
                                        32 * 60, None)
        cfg = _gate_cfg(enforce_endgame_gate=True,
                        observed_cadence_sec=210.0,
                        endgame_action="deepen",
                        endgame_deepen_offset=0.02)
        intents, why = decide_quotes(cfg, up, down, Inventory(), 32 * 60, None)
        assert len(intents) == 2
        assert not why
        for deep, ref in zip(sorted(intents, key=lambda i: i.side),
                             sorted(plain_intents, key=lambda i: i.side)):
            assert deep.price < ref.price
            assert "endgame_gate" in deep.reason
            assert "deepened" in deep.reason

    def test_light_side_balancing_quote_is_never_blocked(self):
        # 100 DOWN shares held: the UP leg reduces exposure, so it posts.
        up, down = _tight_books()
        cfg = _gate_cfg(enforce_endgame_gate=True,
                        observed_cadence_sec=210.0)
        inv = Inventory(down_shares=100.0, down_cost=43.0)
        intents, _ = decide_quotes(cfg, up, down, inv, 32 * 60, None)
        assert [i.side for i in intents] == ["UP"]

    def test_far_from_resolution_the_gate_stays_quiet(self):
        up, down = _tight_books()
        cfg = _gate_cfg(enforce_endgame_gate=True,
                        observed_cadence_sec=210.0)
        intents, why = decide_quotes(cfg, up, down, Inventory(),
                                     4 * 3600, None)
        assert len(intents) == 2
        assert not why

    def test_unknown_cadence_fails_open(self):
        up, down = _tight_books()
        cfg = _gate_cfg(enforce_endgame_gate=True)
        intents, why = decide_quotes(cfg, up, down, Inventory(), 32 * 60, None)
        assert len(intents) == 2
        assert not why
