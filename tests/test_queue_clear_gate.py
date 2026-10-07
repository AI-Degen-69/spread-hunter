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

import math
import os
from unittest import mock

import pytest

from core_brain import risk
from core_brain.config import MakerConfig, load
from core_brain.markets import recent_sell_flow

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
