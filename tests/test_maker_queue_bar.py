"""The maker-queue bar: how long the queue at OUR price takes to clear.

Session B of shadow run `run-2809a7161de1` rested 344 orders across 10 markets
for 30 minutes and filled nothing. Not one order's queue drained. The existing
selection bars cannot see why:

  * `filter_std_vol_min_24h_usd` measures market-wide 24h tape in dollars,
    across every price. A market can clear six figures a day and trade nothing
    at `mid - 2.5c`, which is the only level our maker order ever sits at.
  * `filter_common_vol_min_top3_depth_usd` and `book_health` measure resting size near
    mid -- which, from the maker's side, IS the queue ahead of us. They are
    floors on exactly the quantity that needs a ceiling.

Depth is good for exiting and bad for queueing, and the selector only knew the
first half. This is the other half.
"""
import math
import sys

import pytest

from scoring.selector import cancel_decay, maker_queue_allowed, queue_minutes_at


class TestQueueMinutes:
    def test_a_queue_takes_its_size_divided_by_the_observed_rate(self):
        # 600 resting, 20 shares/min observed at that level -> 30 minutes.
        assert queue_minutes_at(600.0, 300.0, window_minutes=15.0) == 30.0

    def test_the_measured_market_matches_the_recorded_run(self):
        # atp-bonzi-halys, session B: 22,081 resting, 966 shares of tape at our
        # levels over 30 minutes. The best market in that universe.
        got = queue_minutes_at(22081.0, 966.0, window_minutes=30.0)
        assert round(got) == 686

    def test_no_observed_volume_is_unmeasurable_not_instant(self):
        # Four of the ten markets were in this state. Treating "we never saw a
        # trade at our price" as missing data would invert the rule exactly
        # where the finding is strongest.
        assert queue_minutes_at(50000.0, 0.0, window_minutes=30.0) == math.inf

    def test_an_empty_level_clears_immediately(self):
        assert queue_minutes_at(0.0, 100.0, window_minutes=30.0) == 0.0

    def test_a_zero_window_is_unmeasurable(self):
        assert queue_minutes_at(600.0, 300.0, window_minutes=0.0) == math.inf


class TestMakerQueueAllowed:
    def test_a_queue_inside_the_bar_is_admitted(self):
        ok, reason = maker_queue_allowed(14.9, max_queue_minutes=15.0)
        assert ok, reason

    def test_a_queue_exactly_at_the_bar_is_refused(self):
        # Pinned like `filter_common_vol_min_top3_depth_usd`: a ceiling reached, not
        # approached, the reading every other cap in this repo takes.
        ok, reason = maker_queue_allowed(15.0, max_queue_minutes=15.0)
        assert not ok
        assert "maker queue" in reason

    def test_an_unmeasurable_queue_is_refused(self):
        ok, reason = maker_queue_allowed(math.inf, max_queue_minutes=15.0)
        assert not ok
        assert "no trade observed" in reason

    def test_a_zero_bar_disables_the_rule(self):
        # The escape hatch every other limit in MakerConfig has.
        ok, _ = maker_queue_allowed(50397.0, max_queue_minutes=0.0)
        assert ok

    def test_record_only_admits_everything_and_still_reports_the_number(self):
        # How this ships first. A bar that would reject the entire universe is
        # a verdict, not a filter; enforcing it on day one takes the bot
        # silent. Record-only turns it into the measurement we do not have.
        ok, reason = maker_queue_allowed(50397.0, max_queue_minutes=15.0,
                                         enforce=False)
        assert ok
        assert "50397" in reason.replace(",", "")
        assert "would refuse" in reason

    @pytest.mark.parametrize("minutes,expected", [(686.0, False), (0.5, True)])
    def test_the_recorded_universe_is_refused_and_a_fast_queue_is_not(
            self, minutes, expected):
        ok, _ = maker_queue_allowed(minutes, max_queue_minutes=15.0)
        assert ok is expected


class TestCancelDecay:
    """Whether the fill model can be salvaged without a real-money order.

    The model drains queue only on TRADES. A real book also advances an order
    when the orders ahead of it are cancelled, and at queues of 13,000-80,000
    shares that is very likely the dominant mechanism. It is observable for
    free: the level shrank by some amount, the tape explains part of it, and
    the residual is cancels.
    """

    def test_the_residual_after_trades_is_cancels(self):
        # Level fell 1000 -> 400. Tape explains 250. The other 350 cancelled.
        assert cancel_decay(1000.0, 400.0, traded=250.0) == 350.0

    def test_a_level_that_only_traded_shows_no_cancels(self):
        assert cancel_decay(1000.0, 750.0, traded=250.0) == 0.0

    def test_a_growing_level_reports_net_joins_as_negative(self):
        # Not clamped to zero: orders joining behind us is real information,
        # and hiding it would make the daily estimate read as pure decay.
        assert cancel_decay(1000.0, 1200.0, traded=0.0) == -200.0

    def test_trades_larger_than_the_drop_mean_the_level_was_refilled(self):
        # 1000 -> 900 while 400 traded: 300 shares joined while we watched.
        assert cancel_decay(1000.0, 900.0, traded=400.0) == -300.0


class TestConfigDefaults:
    def test_the_bar_ships_recording_not_enforcing(self):
        from scoring.config import MakerConfig

        cfg = MakerConfig()
        assert cfg.filter_std_queue_max_clear_min == 15.0
        assert cfg.filter_std_gate_queue_max_on is False, (
            "enforcing on day one refuses every measured market and takes the "
            "bot silent -- record first")


class TestTheRankerCanActuallyRejectOnIt:
    """A config knob that cannot reject anything is a trap, not a setting.

    `filter_std_queue_max_clear_min` and `maker_queue_allowed` existed, but
    `scripts/filter_markets.py:evaluate` never called either, so setting
    `filter_std_gate_queue_max_on = True` would have changed nothing while
    reading as an enforced bar. The knob has to be able to keep a market out
    of `runtime/markets.json` or it should not exist.
    """

    def _market(self):
        return {"condition_id": "0xabc", "question": "Will X win?",
                "market_slug": "atp-test-2026-08-26"}

    def test_an_unclearable_queue_is_refused_by_the_ranker(self):
        from scripts.filter_markets import queue_bar_reject

        row = queue_bar_reject(self._market(), source="spread",
                               max_queue_minutes=15.0,
                               queue_minutes_fn=lambda _m: 686.0)
        assert row is not None
        assert row["eligible"] is False
        assert "maker queue" in row["reject_reason"]
        assert row["queue_minutes"] == 686.0

    def test_a_market_with_no_trade_at_our_price_is_refused(self):
        import math

        from scripts.filter_markets import queue_bar_reject

        row = queue_bar_reject(self._market(), source="spread",
                               max_queue_minutes=15.0,
                               queue_minutes_fn=lambda _m: math.inf)
        assert row is not None
        assert "no trade observed" in row["reject_reason"]

    def test_a_fast_queue_passes_through_untouched(self):
        from scripts.filter_markets import queue_bar_reject

        assert queue_bar_reject(self._market(), source="spread",
                                max_queue_minutes=15.0,
                                queue_minutes_fn=lambda _m: 3.0) is None

    def test_the_bar_is_inert_while_it_is_not_enforced(self):
        # How this ships: record-only. With no bar passed in, the ranker must
        # not reject and must not pay for a tape read it will not use.
        from scripts.filter_markets import queue_bar_reject

        calls = []

        def counting_fn(_m):
            calls.append(1)
            return 50397.0

        assert queue_bar_reject(self._market(), source="spread",
                                max_queue_minutes=None,
                                queue_minutes_fn=counting_fn) is None
        assert calls == [], "measured a queue it was never going to act on"

    def test_no_measurement_source_means_no_opinion(self):
        from scripts.filter_markets import queue_bar_reject

        assert queue_bar_reject(self._market(), source="spread",
                                max_queue_minutes=15.0,
                                queue_minutes_fn=None) is None


class TestTheBarClimbsTheWholeStack:
    """Round 2 taught the lesson twice: fix the path, not the frame shown.

    Round 1 said `evaluate` never called the bar. Fixing `evaluate` alone left
    `score_pool` dropping the arguments, so the bar was still unreachable from
    the ranking command -- the same bug, one frame up. These tests assert the
    bar at EVERY frame between the operator's command and the predicate, so a
    third round cannot find it one frame higher again.
    """

    def _job(self, slug="atp-slow-2026-08-26"):
        market = {"condition_id": "0xslow", "question": "Will X win?",
                  "market_slug": slug,
                  "tokens": [{"token_id": "t1"}, {"token_id": "t2"}],
                  "rewards": {"max_spread": 3.5, "min_size": 50}}
        return (1.0, market, 500_000.0, "spread")

    def test_score_pool_forwards_the_bar_to_evaluate(self):
        from scripts.filter_markets import score_pool

        out = score_pool([self._job()], session_factory=lambda: None,
                         max_workers=1, max_queue_minutes=15.0,
                         queue_minutes_fn=lambda _m: 686.0)
        assert len(out) == 1
        assert out[0]["eligible"] is False
        assert "maker queue" in out[0]["reject_reason"]

    def test_score_pool_leaves_a_fast_queue_to_the_normal_path(self):
        # It must reach the book fetch rather than being rejected here. With no
        # session the fetch fails and evaluate returns None -- which is proof
        # enough that the queue bar did not short-circuit it.
        from scripts.filter_markets import score_pool

        out = score_pool([self._job()], session_factory=lambda: None,
                         max_workers=1, max_queue_minutes=15.0,
                         queue_minutes_fn=lambda _m: 3.0)
        assert [r for r in out if r.get("reject_reason", "").startswith(
            "maker queue")] == []

    def test_main_resolves_the_bar_only_when_enforcement_is_on(self):
        from scripts.filter_markets import resolve_queue_bar

        class Cfg:
            filter_std_queue_max_clear_min = 15.0
            filter_std_gate_queue_max_on = False

        assert resolve_queue_bar(Cfg()) is None

        Cfg.filter_std_gate_queue_max_on = True
        assert resolve_queue_bar(Cfg()) == 15.0

    def test_resolve_queue_bar_raises_when_canonical_field_is_missing(self):
        """No `getattr(..., <literal>)` fallback: a missing canonical setting fails loudly."""
        from scripts.filter_markets import resolve_queue_bar

        class IncompleteCfg:
            pass

        with pytest.raises(AttributeError):
            resolve_queue_bar(IncompleteCfg())

    @pytest.mark.parametrize("bar", [0.0, -1.0, -15.0])
    def test_a_non_positive_limit_is_disabled_even_when_enforced(self, bar):
        """`0 disables` is the escape hatch every limit in this repo has, and a
        negative one is a typo. Both must resolve to None rather than travel:
        a negative bar is truthy, so `queue_bar_reject` would pay for a tape
        read per market before `maker_queue_allowed` declined to use it."""
        from scripts.filter_markets import resolve_queue_bar

        class Cfg:
            filter_std_queue_max_clear_min = bar
            filter_std_gate_queue_max_on = True

        assert resolve_queue_bar(Cfg()) is None

    def test_a_disabled_limit_never_measures_a_queue(self):
        from scripts.filter_markets import queue_bar_reject

        calls = []
        assert queue_bar_reject({"condition_id": "0x1"}, source="spread",
                                max_queue_minutes=-5.0,
                                queue_minutes_fn=lambda _m: calls.append(1)) is None
        assert calls == []

    def test_main_passes_the_resolved_bar_into_score_pool(self, monkeypatch):
        """The last frame: the operator's ranking command itself.

        `main` opens its own `requests.Session` inline, so the venue reads are
        stubbed at the module boundary rather than injected -- everything from
        `main`'s first line to the `score_pool` call is the real code path.
        Stopping inside the spy keeps the test off the network and away from
        `runtime/markets.json`, which this must never write.
        """
        from dataclasses import replace as dc_replace

        import scripts.filter_markets as fm

        seen = {}

        class _StopHere(RuntimeError):
            pass

        def spy(jobs, **kw):
            seen.update(kw)
            raise _StopHere()

        class _Resp:
            @staticmethod
            def json():
                return {}

        class _Session:
            def get(self, *a, **k):
                return _Resp()

        monkeypatch.setattr(fm, "score_pool", spy)
        monkeypatch.setattr(fm, "_CFG", dc_replace(
            fm._CFG, filter_std_gate_queue_max_on=True,
            filter_std_queue_max_clear_min=15.0))
        monkeypatch.setattr(fm.requests, "Session", _Session)
        monkeypatch.setattr(fm, "gamma_universe",
                            lambda *a, **k: ([], {"volume_bar": 0.0,
                                                  "pages_fetched": 0,
                                                  "rows_scanned": 0,
                                                  "truncated": False,
                                                  "ordering_violated": False,
                                                  "cheap_rejects": {},
                                                  "cheap_examples": {}}))
        monkeypatch.setattr(sys, "argv", ["filter_markets.py"])

        with pytest.raises(_StopHere):
            fm.main()

        assert seen.get("max_queue_minutes") == 15.0, (
            "main did not forward the bar -- the same bug one frame up")
        assert seen.get("min_movement_usd") == fm.MIN_MOVEMENT_USD
