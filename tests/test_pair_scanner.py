"""The pair scanner ranks live venue markets by how much a merged pair earns.

Journeys under test:
1. As the Owner, I see the profit a pair earns when BOTH legs fill at the touch,
   because that -- not the ask -- is the only price this strategy ever pays.
2. As the Owner, a market whose two best bids already sum to $1.00 or more is
   dropped, because assembling a pair there is a booked loss.
3. As the Owner, the queue resting ahead of me is reported as a cost, not as
   available size: a deep touch is money I have to wait behind, not money I get.
4. As the Owner, a pair is only called dislocated when it is cheaper than either
   leg's own spread already makes it -- because `edge_per_pair` and the leg
   spread are the same number whenever the two books mirror, and a wide pair is
   then a market nobody quotes, not money nobody noticed.
5. As the Owner, a genuine dislocation outranks any amount of spread-capture
   value, and below that the ranking prefers the queue that actually turns over.
6. As the Owner, a market the venue returned malformed is skipped rather than
   taking the whole scan down with it.
7. As the Owner, the scan records what taking both legs would have cost, so the
   maker-only nature of the strategy is visible in the output.
8. As the Owner, a venue number that is NaN or Infinity is refused at the
   boundary, because every comparison against NaN is False and one such row
   walks straight past the volume bar and then sorts above every real market.
9. As the Owner, a crossed book is refused rather than reported as a
   dislocation, because a half-stale /book response must not read as the venue
   breaking its own mirror invariant.
10. As the Owner, a mangled gamma response ends the scan with a named failure,
    not a traceback -- the same treatment a mangled book row already gets.
11. As the Owner, importing this read-only module never raises over a trial knob
    that only gates order placement.
12. As the Owner, a dislocation is only ever reported off ONE venue snapshot --
    both legs from a single `POST /books` carrying the same `timestamp` --
    because two serial reads can always straddle a market move, however many
    times they are repeated.
13. As the Owner, when the batch read is unavailable the scan still ranks
    markets, it just never claims a dislocation, because serial reads cannot
    support that claim.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core_brain.pair_scanner import (
    taker_fee_rate,
    PairQuote,
    build_quote,
    parse_candidate,
    rank,
    scan,
)


def _book(bids, asks):
    return {
        "bids": [{"price": str(p), "size": str(s)} for p, s in bids],
        "asks": [{"price": str(p), "size": str(s)} for p, s in asks],
    }


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


# ---------------------------------------------------------------- parse_candidate


def test_parses_a_well_formed_market_row():
    c = parse_candidate(_row())

    assert c is not None
    assert c.condition_id == "0xabc"
    assert c.up_token == "tok-up"
    assert c.down_token == "tok-down"
    assert c.volume_24h == pytest.approx(470372.0)


def test_market_without_exactly_two_clob_tokens_is_skipped():
    assert parse_candidate(_row(clobTokenIds=json.dumps(["only-one"]))) is None
    assert parse_candidate(_row(clobTokenIds="not json at all")) is None
    assert parse_candidate(_row(clobTokenIds=None)) is None


def test_market_without_a_condition_id_is_skipped():
    assert parse_candidate(_row(conditionId="")) is None


def test_slug_is_sanitised_before_it_reaches_a_report():
    c = parse_candidate(_row(slug='twins"><script>'))

    assert c is not None
    assert c.slug == "twinsscript"


# ---------------------------------------------------------------- build_quote


def test_edge_is_one_dollar_minus_the_two_best_bids():
    cand = parse_candidate(_row())

    q = build_quote(
        cand,
        _book(bids=[(0.60, 1000)], asks=[(0.61, 1000)]),
        _book(bids=[(0.39, 1000)], asks=[(0.40, 1000)]),
    )

    assert q is not None
    assert q.bid_pair == pytest.approx(0.99)
    assert q.edge_per_pair == pytest.approx(0.01)


def test_pair_at_or_above_a_dollar_is_dropped():
    cand = parse_candidate(_row())

    exactly_a_dollar = build_quote(
        cand,
        _book(bids=[(0.60, 100)], asks=[(0.61, 100)]),
        _book(bids=[(0.40, 100)], asks=[(0.41, 100)]),
    )
    over_a_dollar = build_quote(
        cand,
        _book(bids=[(0.62, 100)], asks=[(0.63, 100)]),
        _book(bids=[(0.40, 100)], asks=[(0.41, 100)]),
    )

    assert exactly_a_dollar is None
    assert over_a_dollar is None


def test_queue_ahead_is_the_thicker_leg_in_dollars():
    cand = parse_candidate(_row())

    q = build_quote(
        cand,
        _book(bids=[(0.60, 1000)], asks=[(0.61, 10)]),   # $600 resting
        _book(bids=[(0.39, 100)], asks=[(0.40, 10)]),    # $39 resting
    )

    assert q is not None
    # A pair needs both legs. The leg with less queue is not the constraint --
    # the pair can only be assembled as fast as the SLOWER leg clears, and the
    # slower leg is the one with more money in front of us.
    assert q.queue_ahead_usd == pytest.approx(600.0)


def test_ask_pair_records_what_taking_both_legs_would_cost():
    cand = parse_candidate(_row())

    q = build_quote(
        cand,
        _book(bids=[(0.60, 100)], asks=[(0.61, 100)]),
        _book(bids=[(0.39, 100)], asks=[(0.40, 100)]),
    )

    assert q is not None
    assert q.ask_pair == pytest.approx(1.01)
    assert q.taker_pair_is_profitable is False


def test_an_empty_book_side_is_skipped():
    cand = parse_candidate(_row())

    assert build_quote(cand, _book(bids=[], asks=[(0.61, 10)]),
                       _book(bids=[(0.39, 10)], asks=[(0.40, 10)])) is None
    assert build_quote(cand, _book(bids=[(0.60, 10)], asks=[]),
                       _book(bids=[(0.39, 10)], asks=[(0.40, 10)])) is None
    assert build_quote(cand, None, _book(bids=[(0.39, 10)], asks=[(0.40, 10)])) is None


def test_unparseable_book_levels_do_not_take_the_scan_down():
    cand = parse_candidate(_row())
    junk = {"bids": [{"price": "n/a", "size": "n/a"}], "asks": [{"price": "0.4", "size": "5"}]}

    assert build_quote(cand, junk, _book(bids=[(0.39, 10)], asks=[(0.40, 10)])) is None


# ---------------------------------------------------------------- rank


def _quote(cid, edge, queue, volume, leg_spread=None, one_snapshot=True):
    spread = edge if leg_spread is None else leg_spread
    return PairQuote(
        condition_id=cid,
        slug=cid,
        question=cid,
        up_token="u",
        down_token="d",
        volume_24h=volume,
        bid_pair=1.0 - edge,
        ask_up=(1.0 + edge) / 2.0,
        ask_down=(1.0 + edge) / 2.0,
        queue_ahead_usd=queue,
        leg_spread_up=spread,
        leg_spread_down=spread,
        one_snapshot=one_snapshot,
    )


def test_edge_equals_the_leg_spread_when_the_two_books_mirror():
    # ask_UP == 1 - bid_DOWN is how a binary market's two books relate, so
    # bid_pair collapses to 1 - spread_UP and the "edge" is just the spread.
    cand = parse_candidate(_row())

    q = build_quote(
        cand,
        _book(bids=[(0.38, 100)], asks=[(0.64, 100)]),
        _book(bids=[(0.36, 100)], asks=[(0.62, 100)]),
    )

    assert q is not None
    assert q.edge_per_pair == pytest.approx(0.26)
    assert q.leg_spread_up == pytest.approx(0.26)
    assert q.dislocation == pytest.approx(0.0)


def test_a_pair_cheaper_than_either_leg_spread_is_a_dislocation():
    cand = parse_candidate(_row())

    # The DOWN book has come apart: its bid sits far under the UP book's mirror.
    # Both legs carry the same venue timestamp, so the two prices were true at
    # one instant and the claim is supportable.
    q = build_quote(
        cand,
        {**_book(bids=[(0.60, 100)], asks=[(0.61, 100)]), "timestamp": "1788793591776"},
        {**_book(bids=[(0.30, 100)], asks=[(0.31, 100)]), "timestamp": "1788793591776"},
    )

    assert q is not None
    assert q.edge_per_pair == pytest.approx(0.10)
    assert q.leg_spread_up == pytest.approx(0.01)
    assert q.dislocation == pytest.approx(0.09)


def test_the_same_prices_without_a_shared_snapshot_are_not_a_dislocation():
    # Identical numbers to the test above. Only the evidence differs: two books
    # with no shared timestamp were read at two different moments, so the pair
    # may never have existed.
    cand = parse_candidate(_row())

    q = build_quote(
        cand,
        _book(bids=[(0.60, 100)], asks=[(0.61, 100)]),
        _book(bids=[(0.30, 100)], asks=[(0.31, 100)]),
    )

    assert q is not None
    assert q.edge_per_pair == pytest.approx(0.10)
    assert q.dislocation == 0.0


def test_float_residue_under_half_a_tick_is_not_a_dislocation():
    # Subtracting two book prices leaves residue at 1e-17. Without a floor that
    # residue reads as a dislocation and shuffles the whole ranking.
    cand = parse_candidate(_row())

    q = build_quote(
        cand,
        _book(bids=[(0.07, 100)], asks=[(0.08, 100)]),
        _book(bids=[(0.92, 100)], asks=[(0.93, 100)]),
    )

    assert q is not None
    # 1 - (0.07 + 0.92) = 0.01 in exact arithmetic; the leg spreads are 0.01 too.
    assert q.edge_per_pair - min(q.leg_spread_up, q.leg_spread_down) != 0.0
    assert q.dislocation == 0.0


def test_a_dislocation_outranks_any_spread_capture_value():
    wide_but_mirrored = _quote("wide", edge=0.13, queue=31.0, volume=510_760.0)
    thin_but_dislocated = _quote("real", edge=0.02, queue=5_000.0, volume=1_000.0,
                                 leg_spread=0.01)

    assert wide_but_mirrored.dislocation == pytest.approx(0.0)
    assert thin_but_dislocated.dislocation == pytest.approx(0.01)
    assert wide_but_mirrored.spread_capture_score > thin_but_dislocated.spread_capture_score
    assert [q.condition_id for q in rank([wide_but_mirrored, thin_but_dislocated])] == [
        "real", "wide"]


def test_spread_capture_score_prefers_the_queue_that_actually_clears():
    # 1c behind $45,000 of queue on $333k of daily flow: the queue clears ~7x.
    deep = _quote("fed", edge=0.01, queue=45_000.0, volume=333_761.0)
    # 2c behind $600 of queue on $172k of daily flow: the queue clears ~287x.
    shallow = _quote("alcaraz", edge=0.02, queue=600.0, volume=172_436.0)

    assert shallow.spread_capture_score > deep.spread_capture_score
    assert [q.condition_id for q in rank([deep, shallow])] == ["alcaraz", "fed"]


def test_a_market_with_no_flow_scores_zero_however_wide_the_edge():
    dead = _quote("dead", edge=0.04, queue=28.0, volume=0.0)

    assert dead.spread_capture_score == 0.0


def test_an_empty_queue_does_not_divide_by_zero():
    empty = _quote("empty", edge=0.01, queue=0.0, volume=1000.0)

    assert empty.spread_capture_score > 0.0


# ---------------------------------------------------------------- scan


class _FakeSession:
    """Stands in for requests.Session; serves gamma rows then CLOB books."""

    def __init__(self, rows, books):
        self.rows = rows
        self.books = books
        self.book_calls = []

    def get(self, url, params=None, timeout=None):
        params = params or {}
        if "/markets" in url:
            return _FakeResponse(self.rows)
        self.book_calls.append(params.get("token_id"))
        return _FakeResponse(self.books.get(params.get("token_id")))


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_scan_returns_only_profitable_pairs_ranked():
    rows = [
        _row(conditionId="0xwin", slug="win", clobTokenIds=json.dumps(["w-up", "w-down"])),
        _row(conditionId="0xlose", slug="lose", clobTokenIds=json.dumps(["l-up", "l-down"])),
    ]
    books = {
        "w-up": _book(bids=[(0.60, 100)], asks=[(0.62, 100)]),
        "w-down": _book(bids=[(0.38, 100)], asks=[(0.40, 100)]),
        "l-up": _book(bids=[(0.62, 100)], asks=[(0.63, 100)]),
        "l-down": _book(bids=[(0.40, 100)], asks=[(0.41, 100)]),
    }
    session = _FakeSession(rows, books)

    out = scan(min_volume_24h=1000.0, limit=10, session=session)

    assert [q.condition_id for q in out] == ["0xwin"]
    assert out[0].edge_per_pair == pytest.approx(0.02)


def test_scan_skips_books_for_markets_under_the_volume_bar():
    rows = [_row(conditionId="0xthin", volume24hr="10")]
    session = _FakeSession(rows, {})

    out = scan(min_volume_24h=50_000.0, limit=10, session=session)

    assert out == []
    assert session.book_calls == []


# ------------------------------------------------------------------ taker fees


def test_a_raw_ask_pair_under_a_dollar_is_not_takeable_once_fees_are_charged():
    # Crossing makes us the taker on BOTH legs, and the venue charges each one
    # fee_rate * p * (1 - p). Mid-book that is 3.5c a pair -- more than the 1c
    # this raw ask pair leaves on the table.
    cand = parse_candidate(_row())

    q = build_quote(
        cand,
        _book(bids=[(0.48, 100)], asks=[(0.495, 100)]),
        _book(bids=[(0.48, 100)], asks=[(0.495, 100)]),
    )

    assert q is not None
    assert q.ask_pair == pytest.approx(0.99)          # under $1.00 in shares
    assert q.taker_fee_per_pair == pytest.approx(
        taker_fee_rate() * 2 * 0.495 * 0.505)
    assert q.ask_pair + q.taker_fee_per_pair > 1.0
    assert q.taker_pair_is_profitable is False


def test_an_ask_pair_cheap_enough_to_clear_both_fees_is_takeable():
    cand = parse_candidate(_row())

    q = build_quote(
        cand,
        _book(bids=[(0.40, 100)], asks=[(0.44, 100)]),
        _book(bids=[(0.40, 100)], asks=[(0.44, 100)]),
    )

    assert q is not None
    assert q.ask_pair == pytest.approx(0.88)
    assert q.ask_pair + q.taker_fee_per_pair < 1.0
    assert q.taker_pair_is_profitable is True


# --------------------------------------------------------- malformed book levels


@pytest.mark.parametrize("bad_price", ["NaN", "Infinity", "-0.10", "1.40"])
def test_a_level_priced_outside_a_binary_share_takes_the_market_out(bad_price):
    # NaN is the dangerous one: every comparison against it is False, so an
    # unfiltered NaN bid would pass the `bid_pair >= 1.0` guard and rank a
    # market that has no price at all.
    cand = parse_candidate(_row())

    q = build_quote(
        cand,
        {"bids": [{"price": bad_price, "size": "100"}],
         "asks": [{"price": "0.61", "size": "100"}]},
        _book(bids=[(0.39, 100)], asks=[(0.40, 100)]),
    )

    assert q is None


@pytest.mark.parametrize("bad_size", ["0", "-25", "NaN"])
def test_a_level_with_no_real_size_takes_the_market_out(bad_size):
    cand = parse_candidate(_row())

    q = build_quote(
        cand,
        {"bids": [{"price": "0.60", "size": bad_size}],
         "asks": [{"price": "0.61", "size": "100"}]},
        _book(bids=[(0.39, 100)], asks=[(0.40, 100)]),
    )

    assert q is None


# ------------------------------------------------------ non-finite venue numbers


def test_a_nan_volume_is_refused_at_the_boundary():
    # `nan < min_volume_24h` is False, so an unfiltered NaN walks past the
    # volume bar; every score derived from it is then NaN and sorts first.
    assert parse_candidate(_row(volume24hr="NaN")) is None
    assert parse_candidate(_row(volume24hr="Infinity")) is None
    assert parse_candidate(_row(volume24hr="-Infinity")) is None


def test_a_missing_volume_is_still_zero_not_a_rejection():
    absent = parse_candidate(_row(volume24hr=None))
    unparseable = parse_candidate(_row(volume24hr="n/a"))

    assert absent is not None and absent.volume_24h == 0.0
    assert unparseable is not None and unparseable.volume_24h == 0.0


def test_scan_never_ranks_a_non_finite_market_above_a_real_one():
    rows = [
        _row(conditionId="0xnan", slug="nan", volume24hr="NaN",
             clobTokenIds=json.dumps(["n-up", "n-down"])),
        _row(conditionId="0xreal", slug="real", volume24hr="500000",
             clobTokenIds=json.dumps(["r-up", "r-down"])),
    ]
    books = {
        "n-up": _book(bids=[(0.60, 100)], asks=[(0.62, 100)]),
        "n-down": _book(bids=[(0.38, 100)], asks=[(0.40, 100)]),
        "r-up": _book(bids=[(0.60, 100)], asks=[(0.62, 100)]),
        "r-down": _book(bids=[(0.38, 100)], asks=[(0.40, 100)]),
    }
    session = _FakeSession(rows, books)

    out = scan(min_volume_24h=1000.0, limit=10, session=session)

    assert [q.condition_id for q in out] == ["0xreal"]


# ---------------------------------------------------------------- crossed books


def test_a_crossed_book_is_refused_not_reported_as_a_dislocation():
    # A half-stale /book response (fresh asks, stale bids) crosses the book.
    # Left unguarded it gives a negative leg spread, which makes `dislocation`
    # positive and sorts the market to the top of the operator's table.
    cand = parse_candidate(_row())

    crossed_up = build_quote(
        cand,
        _book(bids=[(0.60, 100)], asks=[(0.55, 100)]),
        _book(bids=[(0.30, 100)], asks=[(0.35, 100)]),
    )
    crossed_down = build_quote(
        cand,
        _book(bids=[(0.60, 100)], asks=[(0.61, 100)]),
        _book(bids=[(0.30, 100)], asks=[(0.29, 100)]),
    )

    assert crossed_up is None
    assert crossed_down is None


def test_a_locked_book_is_refused_too():
    # ask == bid is a zero-width market, not a free pair.
    cand = parse_candidate(_row())

    q = build_quote(
        cand,
        _book(bids=[(0.60, 100)], asks=[(0.60, 100)]),
        _book(bids=[(0.30, 100)], asks=[(0.35, 100)]),
    )

    assert q is None


# ------------------------------------------------------- gamma failure handling


class _RaisingResponse:
    def __init__(self, exc):
        self._exc = exc

    def raise_for_status(self):
        return None

    def json(self):
        raise self._exc


class _GammaFailureSession:
    def __init__(self, exc):
        self._exc = exc

    def get(self, url, params=None, timeout=None):
        return _RaisingResponse(self._exc)


def test_a_mangled_gamma_body_ends_the_scan_cleanly():
    session = _GammaFailureSession(ValueError("Expecting value: line 1 column 1"))

    assert scan(min_volume_24h=1000.0, limit=10, session=session) == []


def test_a_gamma_transport_failure_ends_the_scan_cleanly():
    import requests

    session = _GammaFailureSession(requests.RequestException("502 Bad Gateway"))

    assert scan(min_volume_24h=1000.0, limit=10, session=session) == []


# --------------------------------------------------------------- import safety


def test_importing_the_scanner_survives_a_trial_knob_in_the_environment():
    """A read-only module must not die on a gate that only guards order placement.

    `BOT_QUOTE_WIDE_BOOK_TRIAL` is exported to hand to a rehearsal, and
    `Start-Process` copies the operator's whole environment into every child.
    A module-scope `config.load()` without `for_display=True` then refuses at
    import, and the scanner cannot even be loaded -- nor can this test file.
    """
    repo_root = Path(__file__).resolve().parent.parent
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "BOT_QUOTE_WIDE_BOOK_TRIAL": "0.08",
        "PYTHONPATH": str(repo_root),
    }

    proc = subprocess.run(
        [sys.executable, "-c", "import core_brain.pair_scanner"],
        cwd=str(repo_root), env=env, capture_output=True, text=True, timeout=90,
    )

    assert proc.returncode == 0, proc.stderr[-600:]


# ------------------------------------------------ dislocation needs one snapshot


def _snap(book, ts, asset_id):
    """A /books entry: a book plus the venue metadata that dates it."""
    return {**book, "timestamp": ts, "asset_id": asset_id}


class _BatchSession:
    """Serves `POST /books`, the venue's atomic both-legs read."""

    def __init__(self, rows, books):
        self.rows = rows
        self.books = books
        self.posts = 0
        self.gets: list[str] = []

    def get(self, url, params=None, timeout=None):
        params = params or {}
        if "/markets" in url:
            return _FakeResponse(self.rows)
        self.gets.append(params.get("token_id"))
        return _FakeResponse(None)

    def post(self, url, json=None, timeout=None):
        self.posts += 1
        tokens = [entry["token_id"] for entry in (json or [])]
        return _FakeResponse([self.books[t] for t in tokens if t in self.books])


def _disloc_rows():
    return [_row(conditionId="0xd", slug="d", volume24hr="90000",
                 clobTokenIds=json.dumps(["d-up", "d-down"]))]


_UP_BOOK = _book(bids=[(0.60, 100)], asks=[(0.61, 100)])
_DOWN_DISLOCATED = _book(bids=[(0.30, 100)], asks=[(0.31, 100)])
_DOWN_MIRRORED = _book(bids=[(0.39, 100)], asks=[(0.40, 100)])


def test_both_legs_come_from_one_batch_call_not_two_serial_reads():
    # Two serial GETs can straddle a venue move no matter how often they are
    # repeated, so the scan must not use them for the primary read.
    session = _BatchSession(_disloc_rows(), {
        "d-up": _snap(_UP_BOOK, "1788793591776", "d-up"),
        "d-down": _snap(_DOWN_MIRRORED, "1788793591776", "d-down"),
    })

    scan(min_volume_24h=1000.0, limit=10, session=session)

    assert session.posts == 1
    assert session.gets == []


def test_a_dislocation_is_reported_when_both_legs_share_a_timestamp():
    session = _BatchSession(_disloc_rows(), {
        "d-up": _snap(_UP_BOOK, "1788793591776", "d-up"),
        "d-down": _snap(_DOWN_DISLOCATED, "1788793591776", "d-down"),
    })

    out = scan(min_volume_24h=1000.0, limit=10, session=session)

    assert len(out) == 1
    assert out[0].dislocation > 0.0


def test_legs_from_different_venue_moments_never_report_a_dislocation():
    # Same numbers as the test above; only the timestamps differ. The pair
    # existed at no single instant, so the claim is not supportable.
    session = _BatchSession(_disloc_rows(), {
        "d-up": _snap(_UP_BOOK, "1788793591776", "d-up"),
        "d-down": _snap(_DOWN_DISLOCATED, "1788793598000", "d-down"),
    })

    out = scan(min_volume_24h=1000.0, limit=10, session=session)

    assert len(out) == 1
    assert out[0].dislocation == 0.0
    assert out[0].edge_per_pair > 0.0      # still ranked for spread capture


def test_a_book_with_no_timestamp_never_reports_a_dislocation():
    session = _BatchSession(_disloc_rows(), {
        "d-up": {**_UP_BOOK, "asset_id": "d-up"},
        "d-down": {**_DOWN_DISLOCATED, "asset_id": "d-down"},
    })

    out = scan(min_volume_24h=1000.0, limit=10, session=session)

    assert len(out) == 1
    assert out[0].dislocation == 0.0


class _NoBatchSession(_FakeSession):
    """A venue without `POST /books`; the scan must fall back to serial GETs."""

    def post(self, url, json=None, timeout=None):
        import requests as _rq
        raise _rq.RequestException("404 Not Found")


def test_without_the_batch_endpoint_the_scan_still_ranks_but_claims_no_dislocation():
    session = _NoBatchSession(_disloc_rows(), {
        "d-up": _UP_BOOK,
        "d-down": _DOWN_DISLOCATED,
    })

    out = scan(min_volume_24h=1000.0, limit=10, session=session)

    assert len(out) == 1
    assert out[0].dislocation == 0.0       # serial reads cannot support it
    assert out[0].spread_capture_score > 0.0
    assert session.book_calls == ["d-up", "d-down"]
