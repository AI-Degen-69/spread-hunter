"""The queue-clear gate (#393): refuse to rest behind a queue that never clears.

Two halves, tested apart so a failure names which one is wrong:

* `markets.recent_sell_flow` -- MEASURE what could actually reach a new bid: taker
  SELL shares on that token at or below the bid's own price, inside a bounded
  window, with an honest status (`complete` / `truncated` / `unavailable`).
* `risk.queue_clear_block` -- the RULE, which is the repo's existing measured
  maker-queue bar (`scoring.selector.queue_minutes_at` + `maker_queue_allowed`),
  never a second copy of it.

The gate ships RECORD-ONLY (`enforce_queue_clear_gate = False`): the reason is
reported on every gated placement and nothing is dropped until
`HUNTER_QUEUE_CLEAR_GATE=1`. The operator chose that this session; the repo's own
precedent for this rule ships the same way ("enforcing on day one refuses every
market and takes the bot silent").

No network and no signer: `now` and `session` are injected.
"""
from __future__ import annotations

import logging
import math
import os
from unittest import mock

import pytest

from core_brain import risk
from core_brain.config import MakerConfig, load
from core_brain.markets import SellFlow, recent_sell_flow
from core_brain.quotes import QuoteIntent
from core_brain.trader_loop import VenueSeam, _admit_placements, _visit_one

NOW = 1_788_000_000.0
WINDOW = 1800.0  # the 30m tape the issue cites


class _TapeSession:
    """A session that answers the trades endpoint with canned pages.

    Mirrors `_TapeSession` in `tests/test_velocity_gate.py`, plus a call log so a
    test can assert how many pages the reader asked for.
    """

    def __init__(self, pages, boom: bool = False):
        #: One payload per page; the last one repeats if a test asks for more.
        self.pages = list(pages)
        self.boom = boom
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params or {}))
        if self.boom:
            raise OSError("tape unreachable")
        idx = min(len(self.calls) - 1, max(0, len(self.pages) - 1))
        payload = self.pages[idx] if self.pages else []

        class _Resp:
            def json(self_inner):
                return payload

        return _Resp()


def _trade(ts, price=0.47, size=10.0, side="SELL", token="tok", tx="0xtx"):
    return {"timestamp": ts, "price": price, "size": size, "side": side,
            "asset": token, "transactionHash": tx}


def _flow(session, *, window=WINDOW, now=NOW, page_size=500, max_pages=3):
    return recent_sell_flow("0xmarket", window, now=now, session=session,
                            page_size=page_size, max_pages=max_pages)


class TestRecentSellFlow:
    def test_counts_only_sell_rows_inside_window(self):
        session = _TapeSession([[
            _trade(NOW - 60, 0.47, 10.0, side="SELL"),
            _trade(NOW - 60, 0.47, 5.0, side="BUY"),      # lifts an ask, never our bid
            _trade(NOW - 4000, 0.47, 99.0, side="SELL"),  # outside the 30m window
        ]])
        flow = _flow(session)
        assert flow.by_token == {"tok": {0.47: 10.0}}
        assert flow.status == "complete"
        assert flow.window_sec == WINDOW

    def test_drops_future_and_millisecond_stamps(self):
        # A row stamped in seconds*1000 is a millisecond clock on the same field.
        # Dropped, never converted: an hour of "future" tape would read as flow.
        session = _TapeSession([[
            _trade(NOW + 3600, 0.47, 10.0),
            _trade(NOW * 1000, 0.47, 10.0),
        ]])
        flow = _flow(session)
        assert flow.by_token == {}
        assert flow.status == "complete"

    def test_dedups_repeated_rows(self):
        row = _trade(NOW - 60, 0.47, 10.0)
        session = _TapeSession([[row, dict(row)]])
        assert _flow(session).by_token == {"tok": {0.47: 10.0}}

    def test_skips_bad_rows(self):
        session = _TapeSession([[
            _trade(NOW - 60, 0.47, "nan"),          # unparseable size
            _trade(NOW - 60, 0.47, 0.0),            # a print cannot be zero shares
            {"timestamp": NOW - 60, "price": 0.47, "size": 10.0, "asset": "tok"},
            _trade(NOW - 60, 0.47, 10.0, side="   "),   # blank side
            "not-a-dict",
        ]])
        flow = _flow(session)
        assert flow.by_token == {}
        assert flow.status == "complete"

    def test_side_is_matched_case_insensitively_and_trimmed(self):
        session = _TapeSession([[_trade(NOW - 60, 0.47, 10.0, side=" sell ")]])
        assert _flow(session).by_token == {"tok": {0.47: 10.0}}

    def test_request_error_is_unavailable(self):
        flow = _flow(_TapeSession([], boom=True))
        assert flow.status == "unavailable"
        assert flow.by_token == {}

    def test_non_list_payload_is_unavailable(self):
        flow = _flow(_TapeSession([{"error": "nope"}]))
        assert flow.status == "unavailable"
        assert flow.by_token == {}

    @pytest.mark.parametrize("payload", [{}, None, 0, "nope"])
    def test_a_falsy_non_list_payload_is_not_an_empty_window(self, payload):
        # Station IV review: `r.json() or []` read `null` and `{}` as an empty
        # tape, i.e. a COMPLETE window with no sells at any price -- the one
        # reading that refuses a placement -- on a response that carried no
        # measurement at all. The ranker's tape reader refuses the same shapes.
        flow = _flow(_TapeSession([payload]))
        assert flow.status == "unavailable"
        assert flow.by_token == {}

    def test_full_pages_before_the_window_start_are_truncated(self):
        # Every page is full and every row is newer than the window start, so the
        # walk never reaches the edge of the window: the reading is a floor, and
        # the status says so rather than letting a short window look quiet.
        pages = [[_trade(NOW - 30 - i, 0.47, 10.0, tx=f"0xp{p}-{i}")
                  for i in range(2)] for p in range(3)]
        session = _TapeSession(pages)
        flow = _flow(session, page_size=2, max_pages=3)
        assert flow.status == "truncated"
        assert len(session.calls) == 3
        # Three pages, both rows each, all at the one level they share.
        assert flow.by_token == {"tok": {0.47: 60.0}}

    def test_a_page_reaching_the_window_start_is_complete(self):
        # A full page whose oldest row is at/behind the window start has covered
        # the whole window, so one page is enough.
        session = _TapeSession([[ _trade(NOW - 30, 0.47, 10.0),
                                 _trade(NOW - WINDOW - 5, 0.47, 10.0) ]])
        flow = _flow(session, page_size=2, max_pages=3)
        assert flow.status == "complete"
        assert len(session.calls) == 1

    def test_a_row_with_no_stamp_cannot_mark_the_window_covered(self):
        # Station V / CodeRabbit: `float(t.get("timestamp") or 0.0)` turned a row
        # with no stamp into epoch 0, which then became `oldest` -- so a full
        # page that never reached the window start was reported as COMPLETE.
        # The reading is a floor, and reading a floor as a measurement is what
        # refuses placements under HUNTER_QUEUE_CLEAR_GATE=1.
        page = [{"price": 0.47, "size": 10.0, "side": "SELL", "asset": "tok"},
                _trade(NOW - 30, 0.47, 10.0, tx="0xone")]
        flow = _flow(_TapeSession([page]), page_size=2, max_pages=1)
        assert flow.status == "truncated"
        assert flow.by_token == {"tok": {0.47: 10.0}}

    @pytest.mark.parametrize("stamp", [None, 0, -5, ""])
    def test_a_non_positive_or_missing_stamp_never_defines_the_edge(self, stamp):
        page = [{"timestamp": stamp, "price": 0.47, "size": 10.0,
                 "side": "SELL", "asset": "tok"},
                _trade(NOW - 30, 0.47, 10.0, tx="0xone")]
        flow = _flow(_TapeSession([page]), page_size=2, max_pages=1)
        assert flow.status == "truncated"
        assert flow.by_token == {"tok": {0.47: 10.0}}

    def test_quiet_window_is_complete_and_empty(self):
        flow = _flow(_TapeSession([[]]))
        assert flow.status == "complete"
        assert flow.by_token == {}

    def test_paging_asks_for_the_next_offset(self):
        page = [_trade(NOW - 30 - i, 0.47, 10.0) for i in range(2)]
        session = _TapeSession([page])
        _flow(session, page_size=2, max_pages=3)
        assert [c["offset"] for c in session.calls] == [0, 2, 4]
        assert session.calls[0]["market"] == "0xmarket"
        assert session.calls[0]["limit"] == 2


def _gate_cfg(**kw):
    """A cfg that carries only what this gate reads, following `_gate_cfg`
    in `tests/test_completable_pair_gate.py`."""
    base = dict(max_queue_clear_minutes=60.0, enforce_queue_clear_gate=False,
                queue_flow_window_sec=WINDOW)
    base.update(kw)
    return MakerConfig(**base)


class TestQueueClearBlock:
    def test_record_only_reports_the_reason_and_allows(self):
        # 85,000 shares ahead against 2,000 shares of reachable SELL flow in 30m
        # is 1,275 minutes -- the live shape from 2026-10-06.
        allowed, why = risk.queue_clear_block(_gate_cfg(), "UP", 0.47,
                                              85000.0, 2000.0, 30.0)
        assert allowed is True
        assert "would refuse" in why
        assert "maker queue" in why
        assert "(UP @ 0.4700)" in why

    def test_enforcing_refuses_the_same_placement(self):
        allowed, why = risk.queue_clear_block(
            _gate_cfg(enforce_queue_clear_gate=True), "UP", 0.47,
            85000.0, 2000.0, 30.0)
        assert allowed is False
        assert "maker queue" in why
        assert "would refuse" not in why

    def test_a_clear_queue_is_allowed_with_no_reason(self):
        allowed, why = risk.queue_clear_block(
            _gate_cfg(enforce_queue_clear_gate=True), "DOWN", 0.45,
            100.0, 3000.0, 30.0)
        assert allowed is True
        assert why == ""

    def test_zero_queue_is_allowed_even_with_zero_flow(self):
        # Nothing ahead of us needs no time to clear, and "no flow at our price"
        # must not turn into an infinite wait for an empty level.
        allowed, why = risk.queue_clear_block(
            _gate_cfg(enforce_queue_clear_gate=True), "UP", 0.47, 0.0, 0.0, 30.0)
        assert allowed is True
        assert why == ""

    def test_nonzero_queue_with_zero_flow_never_clears(self):
        # The strongest form of the finding: a queue nobody ever sold into.
        allowed, why = risk.queue_clear_block(
            _gate_cfg(enforce_queue_clear_gate=True), "UP", 0.47, 50.0, 0.0, 30.0)
        assert allowed is False
        assert "never clears" in why

    def test_exactly_at_the_bar_is_refused(self):
        # `>=`, not `>`: a ceiling reached, not approached -- the same reading
        # every other cap in this repo takes.
        allowed, why = risk.queue_clear_block(
            _gate_cfg(enforce_queue_clear_gate=True), "UP", 0.47,
            6000.0, 3000.0, 30.0)          # 3000/30min -> 100/min -> 60 min
        assert allowed is False
        assert "60 min bar" in why

    def test_just_under_the_bar_is_allowed(self):
        allowed, why = risk.queue_clear_block(
            _gate_cfg(enforce_queue_clear_gate=True), "UP", 0.47,
            5999.0, 3000.0, 30.0)
        assert allowed is True
        assert why == ""

    def test_a_zero_bar_turns_the_gate_off(self):
        # The escape hatch every other limit here has.
        allowed, why = risk.queue_clear_block(
            _gate_cfg(max_queue_clear_minutes=0.0, enforce_queue_clear_gate=True),
            "UP", 0.47, 85000.0, 0.0, 30.0)
        assert allowed is True
        assert why == ""

    def test_a_nan_bar_cannot_silently_allow_everything(self):
        # NaN would make every comparison false, so the gate would read as
        # enforced while permitting anything. Refused loudly instead.
        allowed, why = risk.queue_clear_block(
            _gate_cfg(max_queue_clear_minutes=math.nan,
                      enforce_queue_clear_gate=True),
            "UP", 0.47, 85000.0, 0.0, 30.0)
        assert allowed is False
        assert why


def _sell_flow(by_token, status="complete", window=WINDOW):
    return SellFlow(status=status, window_sec=window, by_token=by_token)


class _FakeMarket:
    """The attributes `_visit_one` and `quote_t_remaining` read."""

    def __init__(self, cid="0xabc"):
        self.condition_id = cid
        self.up_token = "tok-up"
        self.down_token = "tok-dn"
        self.market_slug = "fake-market"
        self.tick_size = 0.01
        self.neg_risk = False

    def t_remaining(self, now=None):
        return 14400.0


def _intent(side="UP", token="tok-up", price=0.47, size=5, crossed=False):
    return QuoteIntent(side=side, token_id=token, price=price, size=size,
                       mid=price + 0.01, edge_vs_mid=0.01, crossed=crossed)


#: 85,000 shares ahead at UP's own price -- the live 2026-10-06 shape.
_DEEP_UP = {"token_id": "tok-up", "best_bid": 0.46, "best_ask": 0.50,
            "bids": {0.47: 85000.0}, "asks": {0.50: 5000.0}}
#: Nothing ahead of our 0.45 bid at all: the queue clears whatever the tape says.
_CLEAR_DOWN = {"token_id": "tok-dn", "best_bid": 0.44, "best_ask": 0.48,
               "bids": {}, "asks": {0.48: 5000.0}}
#: A real but shallow front: 100 shares ahead against flow that reaches it.
_SHALLOW_UP = {"token_id": "tok-up", "best_bid": 0.46, "best_ask": 0.50,
               "bids": {0.47: 100.0}, "asks": {0.50: 5000.0}}


class _CountingFlow:
    """A flow port that records how many times it was asked, and what for."""

    def __init__(self, flow=None, boom=False):
        self.flow = flow
        self.boom = boom
        self.calls: list[tuple] = []

    def __call__(self, condition_id, window_sec):
        self.calls.append((condition_id, window_sec))
        if self.boom:
            raise OSError("tape unreachable")
        return self.flow


class TestAdmitPlacements:
    def test_nothing_planned_is_a_no_op_that_reads_nothing(self):
        port = _CountingFlow(_sell_flow({}))
        admitted, why = _admit_placements([], _FakeMarket(), _DEEP_UP,
                                          _CLEAR_DOWN, port, _gate_cfg())
        assert admitted == []
        assert why == ""
        assert port.calls == []

    def test_a_clear_front_never_reads_the_tape(self):
        # Nothing ahead of us at our own price: no measurement to pay for.
        port = _CountingFlow(_sell_flow({}))
        intent = _intent(price=0.47)
        admitted, why = _admit_placements(
            [intent], _FakeMarket(),
            {"token_id": "tok-up", "bids": {}, "asks": {}}, _CLEAR_DOWN,
            port, _gate_cfg())
        assert admitted == [intent]
        assert why == ""
        assert port.calls == []

    def test_a_crossed_intent_never_rests_and_is_never_gated(self):
        # Fill-or-kill legs do not join the queue, so a deep front is irrelevant
        # -- and must not cost a tape read either.
        port = _CountingFlow(_sell_flow({}))
        crossed = _intent(crossed=True)
        admitted, why = _admit_placements([crossed], _FakeMarket(), _DEEP_UP,
                                          _CLEAR_DOWN, port, _gate_cfg())
        assert admitted == [crossed]
        assert why == ""
        assert port.calls == []

    def test_record_only_reports_the_reason_and_keeps_both_legs(self):
        port = _CountingFlow(_sell_flow({"tok-up": {0.47: 2000.0}}))
        up = _intent(side="UP", token="tok-up", price=0.47)
        down = _intent(side="DOWN", token="tok-dn", price=0.45)
        admitted, why = _admit_placements([up, down], _FakeMarket(), _DEEP_UP,
                                          _CLEAR_DOWN, port, _gate_cfg())
        assert admitted == [up, down]
        assert "would refuse" in why
        assert "maker queue" in why
        assert port.calls == [("0xabc", WINDOW)]

    def test_enforcing_drops_the_whole_couple_never_one_leg(self):
        # A fresh couple carries no pair_id until `_submit_intents` mints one, so
        # the only way to keep the legs together is to drop them together. One
        # resting leg with no partner is the Unpaired alert state.
        port = _CountingFlow(_sell_flow({"tok-up": {0.47: 2000.0}}))
        up = _intent(side="UP", token="tok-up", price=0.47)
        down = _intent(side="DOWN", token="tok-dn", price=0.45)
        admitted, why = _admit_placements(
            [up, down], _FakeMarket(), _DEEP_UP, _CLEAR_DOWN, port,
            _gate_cfg(enforce_queue_clear_gate=True))
        assert admitted == []
        assert "maker queue" in why
        assert "2 new placement(s) dropped with it" in why

    def test_an_existing_pair_id_is_dropped_with_its_blocked_leg(self):
        # A carried pair_id (a ladder rung replacing one leg of a live pair) gets
        # no exemption: the batch goes with it either way.
        port = _CountingFlow(_sell_flow({"tok-up": {0.47: 2000.0}}))
        up = _intent(side="UP", token="tok-up", price=0.47)
        down = _intent(side="DOWN", token="tok-dn", price=0.45)
        up.pair_id = down.pair_id = "pair-live"
        admitted, why = _admit_placements(
            [up, down], _FakeMarket(), _DEEP_UP, _CLEAR_DOWN, port,
            _gate_cfg(enforce_queue_clear_gate=True))
        assert admitted == []
        assert why

    def test_a_crossed_leg_still_goes_out_beside_a_blocked_couple(self):
        # The crossed leg is a balance hedge completing from inventory; it does
        # not rest, so the queue has nothing to say about it.
        port = _CountingFlow(_sell_flow({"tok-up": {0.47: 2000.0}}))
        up = _intent(side="UP", token="tok-up", price=0.47)
        down = _intent(side="DOWN", token="tok-dn", price=0.45)
        crossed = _intent(side="UP", token="tok-up", price=0.50, crossed=True)
        admitted, why = _admit_placements(
            [up, down, crossed], _FakeMarket(), _DEEP_UP, _CLEAR_DOWN, port,
            _gate_cfg(enforce_queue_clear_gate=True))
        assert admitted == [crossed]
        assert why

    def test_reachable_flow_counts_at_or_below_our_price_only(self):
        # A seller at 0.46 reached our 0.47 bid; a print at 0.48 lifted somebody
        # else's ask and left our bid exactly where it was.
        boundary = _intent(price=0.48)
        book = {"token_id": "tok-up", "bids": {0.48: 5900.0}, "asks": {}}
        allowed_by = _CountingFlow(_sell_flow({"tok-up": {0.48: 1000.0, 0.47: 1000.0}}))
        admitted, why = _admit_placements(
            [boundary], _FakeMarket(), book, _CLEAR_DOWN, allowed_by,
            _gate_cfg(enforce_queue_clear_gate=True))
        # 2000 shares reachable -> 5900 / (2000/30) = 88.5 min > 60: blocked.
        assert admitted == []
        assert "maker queue" in why

        ignored = _CountingFlow(_sell_flow({"tok-up": {0.49: 1_000_000.0}}))
        admitted, why = _admit_placements(
            [boundary], _FakeMarket(), book, _CLEAR_DOWN, ignored,
            _gate_cfg(enforce_queue_clear_gate=True))
        # Nothing reached our price at all: the queue never clears.
        assert admitted == []
        assert "never clears" in why

    def test_an_unmeasurable_tape_fails_open_and_says_so(self):
        for status in ("unavailable", "truncated"):
            up = _intent(side="UP", token="tok-up", price=0.47)
            down = _intent(side="DOWN", token="tok-dn", price=0.45)
            admitted, why = _admit_placements(
                [up, down], _FakeMarket(), _DEEP_UP, _CLEAR_DOWN,
                _CountingFlow(_sell_flow({}, status=status)),
                _gate_cfg(enforce_queue_clear_gate=True))
            assert admitted == [up, down]
            assert status in why

    def test_a_flow_read_that_raises_fails_open(self):
        up = _intent(side="UP", token="tok-up", price=0.47)
        admitted, why = _admit_placements(
            [up], _FakeMarket(), _DEEP_UP, _CLEAR_DOWN,
            _CountingFlow(boom=True),
            _gate_cfg(enforce_queue_clear_gate=True))
        assert admitted == [up]
        assert "failed" in why

    def test_a_flow_read_that_raises_also_warns_on_the_loop_log(self, caplog):
        # Station IV review: a broken port must not fail open SILENTLY. The
        # reason travels with the placement, and the loop's log says so too.
        up = _intent(side="UP", token="tok-up", price=0.47)
        with caplog.at_level(logging.WARNING,
                             logger="main_spread_hunter_loop"):
            _admit_placements([up], _FakeMarket(), _DEEP_UP, _CLEAR_DOWN,
                              _CountingFlow(boom=True),
                              _gate_cfg(enforce_queue_clear_gate=True))
        assert "queue gate tape read failed" in caplog.text
        assert "OSError" in caplog.text

    def test_a_missing_port_leaves_every_caller_as_it_was(self):
        # The shadow seam and every existing test build a seam with no flow port.
        up = _intent(side="UP", token="tok-up", price=0.47)
        admitted, why = _admit_placements(
            [up], _FakeMarket(), _DEEP_UP, _CLEAR_DOWN, None,
            _gate_cfg(enforce_queue_clear_gate=True))
        assert admitted == [up]
        assert why == ""


class TestGateInVisitOne:
    def _seam(self, cfg, decide, submitted, flow_port, open_orders=(),
              up_book=_DEEP_UP):
        books = {"tok-up": up_book, "tok-dn": _CLEAR_DOWN}
        return VenueSeam(
            base_cfg=cfg,
            client=object(),
            registry=None,
            fetch_market=lambda cid: _FakeMarket(),
            fetch_books=lambda host, tok: books[tok],
            decide=decide,
            submit_fn=lambda c, r, m, intents, c2: (
                submitted.append(list(intents)) or len(intents)),
            cancel_fn=lambda *a, **k: 0,
            open_orders_fn=lambda m: list(open_orders),
            flow_fn=flow_port,
        )

    @staticmethod
    def _two_legs(*a, **k):
        return ([_intent(side="UP", token="tok-up", price=0.47),
                 _intent(side="DOWN", token="tok-dn", price=0.45)], "")

    def test_the_dry_run_reports_the_reason_and_still_shows_the_plan(self):
        submitted = []
        seam = self._seam(_gate_cfg(), self._two_legs, submitted,
                          _CountingFlow(_sell_flow({"tok-up": {0.47: 2000.0}})))
        result = _visit_one(seam, {"cid": "0xabc"}, cycle=1, live=False)
        assert submitted == []                       # dry run touches nothing
        assert "would refuse" in result.queue_why
        assert len(result.intents) == 2              # record-only: the plan stands
        assert result.status == "DRY_RUN"

    def test_enforcing_leaves_nothing_resting_for_that_market(self):
        submitted = []
        seam = self._seam(_gate_cfg(enforce_queue_clear_gate=True),
                          self._two_legs, submitted,
                          _CountingFlow(_sell_flow({"tok-up": {0.47: 2000.0}})))
        result = _visit_one(seam, {"cid": "0xabc"}, cycle=1, live=True)
        assert submitted == []
        assert "maker queue" in result.queue_why
        assert result.submitted == 0

    def test_a_clear_queue_places_both_legs_exactly_as_before(self):
        # 100 shares ahead against 3,000 shares of reachable flow in 30m is one
        # minute of queue: nothing to refuse.
        submitted = []
        port = _CountingFlow(_sell_flow({"tok-up": {0.47: 3000.0}}))
        seam = self._seam(_gate_cfg(enforce_queue_clear_gate=True),
                          self._two_legs, submitted, port, up_book=_SHALLOW_UP)
        result = _visit_one(seam, {"cid": "0xabc"}, cycle=1, live=True)
        assert len(submitted[0]) == 2
        assert result.queue_why == ""
        assert result.submitted == 2

    def test_a_seam_without_the_port_is_untouched(self):
        # Today's wiring, and the shadow seam, set no flow port: a deep queue
        # must change nothing for them.
        submitted = []
        seam = self._seam(_gate_cfg(enforce_queue_clear_gate=True),
                          self._two_legs, submitted, None)
        result = _visit_one(seam, {"cid": "0xabc"}, cycle=1, live=True)
        assert len(submitted[0]) == 2
        assert result.queue_why == ""

    def test_a_measured_window_is_the_configured_one(self):
        submitted = []
        port = _CountingFlow(_sell_flow({"tok-up": {0.47: 2000.0}}))
        seam = self._seam(_gate_cfg(queue_flow_window_sec=600.0),
                          self._two_legs, submitted, port)
        _visit_one(seam, {"cid": "0xabc"}, cycle=1, live=True)
        assert port.calls == [("0xabc", 600.0)]


class TestQueueGateSettings:
    def test_ships_record_only_at_sixty_minutes_on_a_thirty_minute_tape(self):
        cfg = MakerConfig()
        assert cfg.enforce_queue_clear_gate is False
        assert cfg.max_queue_clear_minutes == 60.0
        assert cfg.queue_flow_window_sec == 1800.0

    def test_env_turns_the_gate_on_and_moves_the_bar(self):
        with mock.patch.dict(os.environ, {
                "HUNTER_QUEUE_CLEAR_GATE": "1",
                "HUNTER_MAX_QUEUE_CLEAR_MIN": "15"}):
            cfg = load()
            assert cfg.enforce_queue_clear_gate is True
            assert cfg.max_queue_clear_minutes == 15.0

    @pytest.mark.parametrize("raw", ["0", "false", "FALSE", "off", "Off"])
    def test_false_spellings_turn_the_gate_off(self, raw):
        with mock.patch.dict(os.environ, {"HUNTER_QUEUE_CLEAR_GATE": raw}):
            assert load().enforce_queue_clear_gate is False

    @pytest.mark.parametrize("raw", ["1", "true", "on", "yes"])
    def test_any_other_value_turns_the_gate_on(self, raw):
        with mock.patch.dict(os.environ, {"HUNTER_QUEUE_CLEAR_GATE": raw}):
            assert load().enforce_queue_clear_gate is True

    @pytest.mark.parametrize("bad", ["abc", "nan", "inf", "-1", "0"])
    def test_a_bad_bar_is_refused_at_load(self, bad):
        # "0" belongs here with the rest: a zero bar is the DISABLE value, and a
        # disabled rule set through the bar's own name would read as enforced.
        with mock.patch.dict(os.environ, {"HUNTER_MAX_QUEUE_CLEAR_MIN": bad}):
            with pytest.raises(ValueError, match="HUNTER_MAX_QUEUE_CLEAR_MIN"):
                load()

    def test_the_bar_ceiling_is_accepted(self):
        # A day, mirroring HUNTER_ENDGAME_HORIZON_MIN's ceiling: past that the
        # "queue" is the market's whole remaining life, which no bid can be
        # behind.
        with mock.patch.dict(os.environ,
                             {"HUNTER_MAX_QUEUE_CLEAR_MIN": "1440"}):
            assert load().max_queue_clear_minutes == 1440.0

    def test_a_bar_past_the_ceiling_is_refused(self):
        with mock.patch.dict(os.environ,
                             {"HUNTER_MAX_QUEUE_CLEAR_MIN": "1441"}):
            with pytest.raises(ValueError, match="HUNTER_MAX_QUEUE_CLEAR_MIN"):
                load()
