"""The price tape records forward what the venue stops serving backwards.

Journeys under test:
1. As the Owner, a minute already recorded is not recorded twice, so a pass that
   repeats, runs late, or restarts after downtime neither loses minutes nor
   double-counts them.
2. As the Owner, the market metadata needed to interpret a tape is stored with
   it, because a bare token id says nothing about what was being priced.
3. As the Owner, the venue's history payload is turned into ticks, and a point
   the venue mangled is dropped rather than taking the pass down with it.
4. As the Owner, a market under the volume floor is never recorded, because a
   tape nobody traded answers no question.
5. As the Owner, one market whose fetch fails does not abort the pass; the rest
   of the universe is still recorded.
6. As the Owner, a resolution is stamped onto the tape only when the venue
   reports a clean binary outcome, because an ambiguous one cannot score a
   position.
7. As the Owner, the store reports how much tape has been collected, because
   the whole point is knowing when the sample is finally big enough.
"""
from __future__ import annotations

import json
import math
import statistics

import pytest
import requests

import core_brain.price_tape as price_tape
from core_brain.price_tape import (
    DriftCell,
    TapeMarket,
    TapeStore,
    analyse,
    backfill,
    discover_markets,
    parse_history,
    poll_once,
    refresh_resolutions,
    signed_forward_returns,
    summarise,
)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


class _Session:
    """Stands in for requests.Session; conftest blocks real sockets."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        for fragment, payload in self.routes.items():
            if fragment in url:
                if isinstance(payload, Exception):
                    raise payload
                if callable(payload):
                    return _Response(payload(params or {}))
                return _Response(payload)
        raise AssertionError(f"unrouted url {url}")


def _market(token_id="tok-up", **over):
    fields = {
        "token_id": token_id,
        "condition_id": "0xabc",
        "question": "Minnesota Twins vs. Chicago White Sox: O/U 8.5",
        "slug": "twins-white-sox-ou",
        "volume_24h": 470_372.0,
    }
    fields.update(over)
    return TapeMarket(**fields)


def _row(**over):
    row = {
        "conditionId": "0xabc",
        "slug": "twins-white-sox-ou",
        "question": "Minnesota Twins vs. Chicago White Sox: O/U 8.5",
        "volume24hr": "470372",
        "clobTokenIds": json.dumps(["tok-up", "tok-down"]),
    }
    row.update(over)
    return row


def _store(tmp_path):
    return TapeStore(tmp_path / "price_tape.db")


# ------------------------------------------------------------------ dedup


def test_records_ticks_for_a_tracked_market(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())

    written = store.append_ticks("tok-up", [(1_700_000_060, 0.47), (1_700_000_120, 0.48)])

    assert written == 2
    assert store.summary().ticks == 2


def test_does_not_record_the_same_minute_twice(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    store.append_ticks("tok-up", [(1_700_000_060, 0.47), (1_700_000_120, 0.48)])

    written = store.append_ticks(
        "tok-up", [(1_700_000_120, 0.48), (1_700_000_180, 0.49)]
    )

    assert written == 1, "the overlapping minute must not be stored again"
    assert store.summary().ticks == 3


def test_re_recording_a_market_keeps_one_row(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    store.record_market(_market(volume_24h=999_999.0))

    assert store.summary().markets == 1


# ------------------------------------------------------------------ metadata


def test_keeps_the_metadata_needed_to_interpret_a_tape(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())

    stored = store.get_market("tok-up")

    assert stored.condition_id == "0xabc"
    assert stored.question.startswith("Minnesota Twins")
    assert stored.volume_24h == pytest.approx(470_372.0)


# ------------------------------------------------------------------ parsing


def test_parses_the_venue_history_payload():
    ticks = parse_history({"history": [{"t": 1_700_000_060, "p": 0.47},
                                       {"t": 1_700_000_120, "p": "0.48"}]})

    assert ticks == [(1_700_000_060, 0.47), (1_700_000_120, 0.48)]


def test_drops_a_mangled_point_instead_of_losing_the_pass():
    ticks = parse_history(
        {"history": [{"t": 1_700_000_060, "p": 0.47},
                     {"t": "not-a-time", "p": 0.48},
                     {"p": 0.49},
                     "garbage",
                     {"t": 1_700_000_180, "p": 0.50}]}
    )

    assert ticks == [(1_700_000_060, 0.47), (1_700_000_180, 0.50)]


def test_parses_an_unusable_payload_as_no_ticks():
    assert parse_history(None) == []
    assert parse_history({"history": "nope"}) == []


def test_backfill_stores_what_the_venue_returned(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    session = _Session({"prices-history": {"history": [
        {"t": 1_700_000_060, "p": 0.47}, {"t": 1_700_000_120, "p": 0.48}]}})

    written = backfill(store, "tok-up", session=session, hours=6)

    assert written == 2
    assert store.summary().ticks == 2


def test_backfill_asks_for_minute_fidelity(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    session = _Session({"prices-history": {"history": []}})

    backfill(store, "tok-up", session=session, hours=6)

    _url, params = session.calls[0]
    assert params["fidelity"] == 1, "a coarser tape cannot answer the drift question"
    assert params["market"] == "tok-up"


# ------------------------------------------------------------------ discovery


def test_discovery_skips_a_market_under_the_volume_floor():
    session = _Session({"/markets": [
        _row(),
        _row(conditionId="0xthin", clobTokenIds=json.dumps(["thin-up", "thin-down"]),
             volume24hr="120"),
    ]})

    markets = discover_markets(session=session, min_volume=20_000.0, limit=50)

    assert [m.token_id for m in markets] == ["tok-up"]


def test_discovery_skips_a_row_the_venue_mangled():
    session = _Session({"/markets": [
        _row(clobTokenIds="not-json"),
        _row(clobTokenIds=json.dumps(["only-one"])),
        _row(conditionId=""),
        "garbage",
        _row(),
    ]})

    markets = discover_markets(session=session, min_volume=20_000.0, limit=50)

    assert [m.token_id for m in markets] == ["tok-up"]


def test_discovery_stops_at_the_limit():
    rows = [_row(conditionId=f"0x{i}", clobTokenIds=json.dumps([f"up-{i}", f"dn-{i}"]))
            for i in range(10)]
    session = _Session({"/markets": rows})

    markets = discover_markets(session=session, min_volume=0.0, limit=3)

    assert len(markets) == 3


# ------------------------------------------------------------------ one pass


def test_a_pass_records_every_market_it_discovered(tmp_path):
    store = _store(tmp_path)
    session = _Session({
        "/markets": [_row()],
        "prices-history": {"history": [{"t": 1_700_000_060, "p": 0.47}]},
    })

    result = poll_once(store, session=session, min_volume=20_000.0, limit=50, hours=6)

    assert result.markets == 1
    assert result.ticks == 1
    assert result.failures == 0
    assert store.summary().markets == 1


def test_one_failed_fetch_does_not_abort_the_pass(tmp_path):
    store = _store(tmp_path)
    rows = [_row(conditionId="0xa", clobTokenIds=json.dumps(["good-up", "good-dn"])),
            _row(conditionId="0xb", clobTokenIds=json.dumps(["bad-up", "bad-dn"]))]

    def history(params):
        if params.get("market") == "bad-up":
            raise requests.ConnectionError("venue dropped it")
        return {"history": [{"t": 1_700_000_060, "p": 0.47}]}

    session = _Session({"/markets": rows, "prices-history": history})

    result = poll_once(store, session=session, min_volume=0.0, limit=50, hours=6)

    assert result.failures == 1
    assert result.ticks == 1, "the healthy market is still recorded"
    assert store.summary().markets == 2


def test_a_repeated_pass_adds_no_duplicate_minutes(tmp_path):
    store = _store(tmp_path)
    session = _Session({
        "/markets": [_row()],
        "prices-history": {"history": [{"t": 1_700_000_060, "p": 0.47}]},
    })

    poll_once(store, session=session, min_volume=0.0, limit=50, hours=6)
    second = poll_once(store, session=session, min_volume=0.0, limit=50, hours=6)

    assert second.ticks == 0
    assert store.summary().ticks == 1


# ------------------------------------------------------------------ resolution


def test_stamps_a_clean_binary_resolution_onto_the_tape(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    session = _Session({"/markets": [
        _row(closed=True, outcomePrices=json.dumps(["1", "0"]))]})

    stamped = refresh_resolutions(store, session=session)

    assert stamped == 1
    assert store.get_market("tok-up").up_wins is True


def test_records_a_no_resolution_as_such(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    session = _Session({"/markets": [
        _row(closed=True, outcomePrices=json.dumps(["0", "1"]))]})

    refresh_resolutions(store, session=session)

    assert store.get_market("tok-up").up_wins is False


def test_refuses_to_stamp_an_ambiguous_outcome(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    session = _Session({"/markets": [
        _row(closed=True, outcomePrices=json.dumps(["0.5", "0.5"]))]})

    stamped = refresh_resolutions(store, session=session)

    assert stamped == 0
    assert store.get_market("tok-up").up_wins is None


def test_leaves_an_open_market_unresolved(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    session = _Session({"/markets": [
        _row(closed=False, outcomePrices=json.dumps(["1", "0"]))]})

    stamped = refresh_resolutions(store, session=session)

    assert stamped == 0
    assert store.get_market("tok-up").up_wins is None


def test_a_resolved_market_is_no_longer_tracked(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    store.record_market(_market(token_id="other-up", condition_id="0xother"))
    store.mark_resolved("tok-up", True)

    assert store.tracked_tokens() == ["other-up"]


# ------------------------------------------------------- resolve-only flag


def test_resolve_only_stamps_the_backlog_and_records_no_ticks(
    tmp_path, monkeypatch, capsys
):
    db_path = tmp_path / "price_tape.db"
    store = TapeStore(db_path)
    store.record_market(_market(token_id="tok-clean", condition_id="0xclean"))
    store.record_market(_market(token_id="tok-open", condition_id="0xopen"))
    store.record_market(_market(token_id="tok-amb", condition_id="0xamb"))
    session = _Session({"/markets": [
        _row(conditionId="0xclean",
             clobTokenIds=json.dumps(["tok-clean", "tok-clean-down"]),
             closed=True, outcomePrices=json.dumps(["1", "0"])),
        _row(conditionId="0xamb",
             clobTokenIds=json.dumps(["tok-amb", "tok-amb-down"]),
             closed=True, outcomePrices=json.dumps(["0.5", "0.5"])),
    ]})
    monkeypatch.setattr(price_tape, "_new_session", lambda: session)

    rc = price_tape._main(["--db", str(db_path), "--resolve-only"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "stamped 1" in out
    assert "pending 2" in out
    assert TapeStore(db_path).get_market("tok-clean").up_wins is True
    assert TapeStore(db_path).get_market("tok-open").up_wins is None
    assert TapeStore(db_path).get_market("tok-amb").up_wins is None
    assert TapeStore(db_path).summary().ticks == 0


# ------------------------------------------------------------------ summary


def test_summary_reports_what_has_been_collected(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    store.record_market(_market(token_id="other-up", condition_id="0xother"))
    store.append_ticks("tok-up", [(1_700_000_060, 0.47), (1_700_000_120, 0.48)])
    store.append_ticks("other-up", [(1_700_000_180, 0.51)])
    store.mark_resolved("tok-up", False)

    summary = store.summary()

    assert summary.markets == 2
    assert summary.resolved == 1
    assert summary.ticks == 3
    assert summary.first_ts == 1_700_000_060
    assert summary.last_ts == 1_700_000_180


def test_summary_of_an_empty_store_is_not_an_error(tmp_path):
    summary = _store(tmp_path).summary()

    assert summary.markets == 0
    assert summary.ticks == 0
    assert summary.first_ts is None


# ------------------------------------------------------------------ deep backfill
# The venue honours minute fidelity only inside a bounded window, so reaching
# back past a day means walking the request back one day at a time. 20 live
# markets hold 438 market-days of minute tape right now; not pulling it was the
# reason the drift question looked like it needed weeks of forward recording.


def test_backfill_walks_back_one_day_at_a_time(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    session = _Session({"prices-history": {"history": []}})

    backfill(store, "tok-up", session=session, hours=72, now=1_700_000_000)

    windows = [(p["startTs"], p["endTs"]) for _u, p in session.calls]
    assert windows == [
        (1_700_000_000 - 86_400, 1_700_000_000),
        (1_700_000_000 - 172_800, 1_700_000_000 - 86_400),
        (1_700_000_000 - 259_200, 1_700_000_000 - 172_800),
    ]
    assert all(p["fidelity"] == 1 for _u, p in session.calls)


def test_backfill_of_under_a_day_stays_one_request(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    session = _Session({"prices-history": {"history": []}})

    backfill(store, "tok-up", session=session, hours=6, now=1_700_000_000)

    assert len(session.calls) == 1


def test_backfill_sums_the_ticks_from_every_window(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())

    def history(params):
        return {"history": [{"t": params["startTs"] + 60, "p": 0.47}]}

    session = _Session({"prices-history": history})

    written = backfill(store, "tok-up", session=session, hours=72, now=1_700_000_000)

    assert written == 3


def test_a_failed_window_keeps_the_windows_already_stored(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())

    def history(params):
        if params["startTs"] < 1_700_000_000 - 172_800:
            raise requests.ConnectionError("venue dropped it")
        return {"history": [{"t": params["startTs"] + 60, "p": 0.47}]}

    session = _Session({"prices-history": history})

    with pytest.raises(requests.ConnectionError):
        backfill(store, "tok-up", session=session, hours=72, now=1_700_000_000)

    assert store.summary().ticks == 2, "the windows fetched before the failure survive"


# ------------------------------------------------------------------ bad prices
# A binary share is worth between $0.00 and $1.00. `float()` happily accepts
# -1, 2, NaN and inf, and a tape holding one of those silently corrupts every
# statistic computed from it later.


def test_rejects_a_price_outside_the_zero_to_one_range():
    ticks = parse_history({"history": [
        {"t": 1_700_000_060, "p": -0.01},
        {"t": 1_700_000_120, "p": 1.01},
        {"t": 1_700_000_180, "p": 0.47},
    ]})

    assert ticks == [(1_700_000_180, 0.47)]


def test_rejects_nan_and_infinity():
    ticks = parse_history({"history": [
        {"t": 1_700_000_060, "p": "NaN"},
        {"t": 1_700_000_120, "p": "inf"},
        {"t": 1_700_000_180, "p": "-inf"},
        {"t": 1_700_000_240, "p": 0.5},
    ]})

    assert ticks == [(1_700_000_240, 0.5)]


def test_keeps_the_boundary_prices():
    ticks = parse_history({"history": [{"t": 1, "p": 0.0}, {"t": 2, "p": 1.0}]})

    assert ticks == [(1, 0.0), (2, 1.0)]


# ------------------------------------------------------------------ pagination
# The venue caps a market page at 100 rows however large a limit is asked for.
# Requesting 500 and reading the reply as the whole universe silently truncates
# it: a --limit of 250 returned 100 markets on 2026-09-07.


class _PagedSession:
    """Serves market rows a page at a time, honouring `offset`."""

    def __init__(self, rows, page_size=100, other=None):
        self.rows = rows
        self.page_size = page_size
        self.other = other or {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        params = dict(params or {})
        self.calls.append((url, params))
        for fragment, payload in self.other.items():
            if fragment in url:
                return _Response(payload)
        offset = int(params.get("offset", 0))
        return _Response(self.rows[offset:offset + self.page_size])


def _rows(count, prefix="m"):
    return [_row(conditionId=f"0x{prefix}{i}",
                 clobTokenIds=json.dumps([f"{prefix}-up-{i}", f"{prefix}-dn-{i}"]))
            for i in range(count)]


def test_discovery_reads_past_the_first_page():
    session = _PagedSession(_rows(250), page_size=100)

    markets = discover_markets(session=session, min_volume=0.0, limit=250)

    assert len(markets) == 250, "the venue caps a page at 100 rows"
    assert len(session.calls) >= 3


def test_discovery_stops_when_a_page_comes_back_empty():
    session = _PagedSession(_rows(120), page_size=100)

    markets = discover_markets(session=session, min_volume=0.0, limit=500)

    assert len(markets) == 120


def test_discovery_does_not_page_past_the_limit():
    session = _PagedSession(_rows(250), page_size=100)

    markets = discover_markets(session=session, min_volume=0.0, limit=50)

    assert len(markets) == 50
    assert len(session.calls) == 1, "one page already covered the limit"


def test_resolution_finds_a_tracked_market_on_a_later_page(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market(token_id="late-up-4", condition_id="0xlate4"))
    rows = _rows(100, prefix="early") + [
        _row(conditionId="0xlate4",
             clobTokenIds=json.dumps(["late-up-4", "late-dn-4"]),
             closed=True, outcomePrices=json.dumps(["1", "0"]))]
    session = _PagedSession(rows, page_size=100)

    stamped = refresh_resolutions(store, session=session)

    assert stamped == 1
    assert store.get_market("late-up-4").up_wins is True


def test_resolution_stops_once_every_tracked_market_is_stamped(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market(token_id="early-up-0", condition_id="0xearly0"))
    rows = [_row(conditionId="0xearly0",
                 clobTokenIds=json.dumps(["early-up-0", "early-dn-0"]),
                 closed=True, outcomePrices=json.dumps(["1", "0"]))] + _rows(400)
    session = _PagedSession(rows, page_size=100)

    refresh_resolutions(store, session=session)

    assert len(session.calls) == 1, "nothing tracked is left to look for"


def test_resolution_with_nothing_tracked_asks_the_venue_nothing(tmp_path):
    store = _store(tmp_path)
    session = _PagedSession(_rows(100))

    assert refresh_resolutions(store, session=session) == 0
    assert session.calls == []


# ------------------------------------------------------------------ analysis
# The tape answers one question: after price moves, does it keep going or come
# back? Loading 2.8M ticks takes 33 seconds, so the answer is computed by an
# explicit pass and stored; nothing recomputes it on a page load.


def test_signed_forward_return_is_positive_when_the_move_continues():
    # rises 5c over the lookback, then keeps rising over the horizon
    tape = [(i * 60, 0.50 + 0.005 * min(i, 10) + 0.005 * max(0, i - 10))
            for i in range(21)]

    xs = signed_forward_returns(tape, DriftCell(0.03, lookback=10, horizon=10))

    assert len(xs) == 1
    assert xs[0] > 0


def test_signed_forward_return_is_negative_when_the_move_comes_back():
    up = [(i * 60, 0.50 + 0.005 * i) for i in range(11)]         # +5c
    back = [((11 + i) * 60, 0.55 - 0.005 * (i + 1)) for i in range(10)]

    xs = signed_forward_returns(up + back, DriftCell(0.03, lookback=10, horizon=10))

    assert len(xs) == 1
    assert xs[0] < 0


def test_a_move_under_the_trigger_is_not_sampled():
    tape = [(i * 60, 0.50 + 0.0001 * i) for i in range(40)]

    assert signed_forward_returns(tape, DriftCell(0.03, lookback=10, horizon=10)) == []


def test_samples_do_not_overlap():
    tape = [(i * 60, 0.20 + 0.01 * i) for i in range(60)]

    xs = signed_forward_returns(tape, DriftCell(0.03, lookback=5, horizon=10))

    assert len(xs) <= (60 - 5) // 10 + 1, "a sample must consume its whole horizon"


def test_a_decided_market_stops_being_sampled():
    # walks straight through 0.97 and pins there
    tape = [(i * 60, min(0.99, 0.50 + 0.01 * i)) for i in range(80)]

    xs = signed_forward_returns(tape, DriftCell(0.03, lookback=10, horizon=10))

    assert all(abs(x) < 0.5 for x in xs), "no sample may start at the pin"


def test_summarise_reports_n_mean_and_t():
    stat = summarise([0.01, 0.02, 0.03, 0.02, 0.02])

    assert stat.n == 5
    assert stat.mean == pytest.approx(0.02)
    assert stat.t > 0


def test_summarise_of_a_thin_sample_reports_no_t():
    stat = summarise([0.01, 0.02])

    assert stat.n == 2
    assert stat.t == 0.0


def test_analysis_stores_one_finding_per_cell(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    store.append_ticks("tok-up", [(i * 60, 0.20 + 0.004 * i) for i in range(400)])
    cells = (DriftCell(0.03, 60, 60), DriftCell(0.05, 60, 240))

    written = analyse(store, cells=cells)

    assert written == 2
    labels = {f.label for f in store.findings()}
    assert labels == {"3c/60m/60m", "5c/60m/240m"}


def test_a_second_analysis_replaces_the_first(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    store.append_ticks("tok-up", [(i * 60, 0.20 + 0.004 * i) for i in range(400)])
    cells = (DriftCell(0.03, 60, 60),)

    analyse(store, cells=cells)
    analyse(store, cells=cells)

    assert len(store.findings()) == 1, "findings are a snapshot, not a log"


def test_findings_carry_the_cell_that_produced_them(tmp_path):
    store = _store(tmp_path)
    store.record_market(_market())
    store.append_ticks("tok-up", [(i * 60, 0.20 + 0.004 * i) for i in range(400)])

    analyse(store, cells=(DriftCell(0.05, 240, 1440),))
    finding = store.findings()[0]

    assert finding.trigger == pytest.approx(0.05)
    assert finding.lookback_min == 240
    assert finding.horizon_min == 1440
    assert finding.computed_at > 0


def test_analysis_of_an_empty_store_writes_nothing(tmp_path):
    store = _store(tmp_path)

    assert analyse(store, cells=(DriftCell(0.03, 60, 60),)) == 1
    assert store.findings()[0].n == 0


# ------------------------------------------------------------- real durations
# A cell names minutes, so it has to measure minutes. Row offsets only equal
# minutes when the tape has no gaps, and it has gaps: the venue skips minutes
# and `parse_history` drops points it mangled. A 60m cell walked by row offset
# silently measures however long 60 rows happen to span.


def _minute_tape(count, start=1_700_000_000, first=0.50, step=0.0):
    return [(start + i * 60, first + step * i) for i in range(count)]


def test_a_gap_does_not_stretch_the_window(tmp_path):
    # 120 clean minutes, then a two-hour hole, then more tape. Walked by row
    # offset the candidates straddling the hole would measure hours as "60m".
    early = _minute_tape(120, first=0.50, step=0.001)
    late = [(1_700_000_000 + 7200 + i * 60, 0.62 + 0.001 * i) for i in range(120)]
    cell = DriftCell(0.03, lookback=60, horizon=60)

    xs = signed_forward_returns(early + late, cell)

    # every retained sample must come from a stretch that really is 60 minutes
    assert xs, "the clean stretches still produce samples"
    assert all(abs(x) < 0.2 for x in xs), "no sample may span the hole"


def test_a_candidate_without_a_lookback_endpoint_is_skipped():
    # the tape opens straight into a big move, with no tick an hour earlier
    tape = [(1_700_000_000 + i * 60, 0.50 + 0.01 * i) for i in range(20)]

    assert signed_forward_returns(tape, DriftCell(0.03, 60, 60)) == []


def test_a_candidate_without_a_horizon_endpoint_is_skipped():
    # a clean hour of rising price, then the tape simply stops
    tape = _minute_tape(70, first=0.40, step=0.002)

    assert signed_forward_returns(tape, DriftCell(0.03, 60, 60)) == []


def test_samples_do_not_overlap_in_time():
    tape = _minute_tape(600, first=0.20, step=0.0008)
    cell = DriftCell(0.03, lookback=60, horizon=60)

    xs = signed_forward_returns(tape, cell)

    span_minutes = 600
    assert len(xs) <= span_minutes // cell.horizon, \
        "each sample must consume its whole horizon in minutes, not in rows"


def test_the_window_is_measured_from_timestamps_not_row_count():
    # Same shape twice: once at one row per minute, once at one row per two
    # minutes. A row-offset walk reads different windows out of them; a
    # timestamp walk reads the same one.
    dense = [(1_700_000_000 + i * 60, 0.30 + 0.001 * i) for i in range(400)]
    sparse = [(1_700_000_000 + i * 120, 0.30 + 0.002 * i) for i in range(200)]
    cell = DriftCell(0.03, lookback=60, horizon=60)

    d = signed_forward_returns(dense, cell)
    s = signed_forward_returns(sparse, cell)

    assert d and s
    assert abs(statistics.fmean(d) - statistics.fmean(s)) < 0.005


def test_summarise_uses_the_sample_standard_deviation():
    values = [0.01, 0.02, 0.03, 0.04, 0.05]
    stat = summarise(values)

    expected = statistics.fmean(values) / (
        statistics.stdev(values) / math.sqrt(len(values)))

    assert stat.t == pytest.approx(expected), \
        "a population sd understates the standard error and overstates t"
