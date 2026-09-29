"""Market resolution sweeper: closed markets get a terminal marker, not QUOTING forever."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from core_brain.market_resolution import (
    MarketEndState,
    book_shadow_settlement,
    fetch_market_end_state,
    fetch_open_market_state,
    parse_end_state,
    sweep_market_resolutions,
)
from core_brain.order_registry import (
    FillRecord,
    OrderRecord,
    OrderRegistry,
    QuoteRecord,
    init_db,
)


class FakeMarket:
    def __init__(self, cid):
        self.cid = cid
        self.up_token = "tok-up"
        self.down_token = "tok-dn"
        self.market_slug = "fake-market"


# ---------------------------------------------------------------------------
# parse_end_state: pure classification from a gamma row
# ---------------------------------------------------------------------------

def test_parse_closed_true_marks_resolved():
    row = {"condition_id": "0xC", "closed": True, "endDate": None}
    s = parse_end_state(row, now_ts=0)
    assert s.resolved is True
    assert s.closed is True


def test_parse_end_date_in_past_marks_resolved():
    row = {"condition_id": "0xC", "closed": None,
           "endDate": "1970-01-01T00:00:00Z"}
    s = parse_end_state(row, now_ts=1_000_000_000)
    assert s.resolved is True
    assert s.end_date_passed is True


def test_parse_end_date_in_future_is_not_resolved():
    row = {"condition_id": "0xC", "closed": None,
           "endDate": "2999-01-01T00:00:00Z"}
    s = parse_end_state(row, now_ts=0)
    assert s.resolved is False
    assert s.end_date_passed is False


def test_parse_winner_from_outcome_prices():
    row = {
        "condition_id": "0xC", "closed": True,
        "outcomes": ["Up", "Down"], "outcomePrices": ["1", "0"],
    }
    s = parse_end_state(row, now_ts=0)
    assert s.resolved is True
    assert s.winner_token == "Up"
    assert s.winner_price == 1.0


def test_parse_no_winner_when_prices_below_threshold():
    row = {
        "condition_id": "0xC", "closed": False,
        "outcomes": ["Up", "Down"], "outcomePrices": ["0.5", "0.5"],
    }
    s = parse_end_state(row, now_ts=0)
    assert s.resolved is False
    assert s.winner_token is None


def test_parse_non_dict_returns_none():
    assert parse_end_state(None) is None  # type: ignore[arg-type]
    assert parse_end_state("not-a-dict", now_ts=0) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# fetch_market_end_state: network IO with injectable urlopen
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
    def read(self):
        import json
        return json.dumps(self._payload).encode()
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_fetch_returns_state_on_success():
    payload = [{"condition_id": "0xC", "closed": True}]
    state = fetch_market_end_state(
        "https://gamma", "0xC",
        urlopen=lambda url, timeout=10: _FakeResp(payload))
    assert state.resolved is True
    assert state.unreachable is False


# ---------------------------------------------------------------------------
# The stated end itself, and the OPEN listing (#311)
#
# The resolution read asks for `closed=true`, which is the only way to see an
# ended market -- and which returns an EMPTY list for a market that is still
# open. An aged-out leg needs the opposite read: the open market's own stated
# end. Measured against the live venue (2026-09-29): the `closed=true` filter
# returned 0 rows for an open ATP market while the unfiltered read returned it
# with `endDate` a week out and `gameStartTime` hours past.
# ---------------------------------------------------------------------------

_OPEN_END_ISO = "2027-01-01T00:00:00Z"
_OPEN_END_TS = 1798761600.0  # the same instant, in epoch seconds


def test_parse_end_state_carries_the_stated_end_timestamp():
    # Arrange - the epoch the parser already computes was thrown away.
    row = {"condition_id": "0xC", "closed": False, "acceptingOrders": True,
           "endDate": _OPEN_END_ISO}

    # Act
    state = parse_end_state(row, now_ts=1_790_000_000.0)

    # Assert - an aged-out deadline needs the instant, not just "not passed".
    assert state.end_ts == _OPEN_END_TS
    assert state.resolved is False


def test_parse_end_state_keeps_end_ts_none_when_the_date_is_unreadable():
    # Arrange / Act - a malformed end is unreadable, never a guessed instant.
    state = parse_end_state(
        {"condition_id": "0xC", "closed": False, "endDate": "not-a-date"},
        now_ts=1_790_000_000.0)

    # Assert
    assert state.end_ts is None


def test_the_open_read_returns_the_market_the_closed_read_cannot_see():
    # Arrange - an open market, exactly the shape the live probe returned.
    payload = [{"condition_id": "0xC", "closed": False,
                "acceptingOrders": True, "endDate": _OPEN_END_ISO}]
    seen = {}

    def urlopen(req, timeout=10):
        seen["url"] = req.full_url
        return _FakeResp(payload)

    # Act
    state = fetch_open_market_state("https://gamma", "0xC", urlopen=urlopen)

    # Assert
    assert state is not None
    assert state.closed is False
    assert state.end_ts == _OPEN_END_TS
    assert state.unreachable is False


def test_the_open_read_asks_for_the_side_the_closed_read_excludes():
    # Arrange - asking with `closed=true` is what made this read return
    # "unreachable" for every live leg, i.e. an arm that never fires.
    payload = [{"condition_id": "0xC", "closed": False,
                "acceptingOrders": True, "endDate": _OPEN_END_ISO}]
    seen = {}

    def urlopen(req, timeout=10):
        seen["url"] = req.full_url
        return _FakeResp(payload)

    # Act
    fetch_open_market_state("https://gamma", "0xC", urlopen=urlopen)

    # Assert
    assert "condition_ids=0xC" in seen["url"]
    assert "closed=true" not in seen["url"]


def test_the_closed_read_keeps_asking_for_the_closed_side():
    # Arrange / Act - the two reads must not be collapsed into one: resolution
    # detection depends on the closed filter.
    seen = {}

    def urlopen(req, timeout=10):
        seen["url"] = req.full_url
        return _FakeResp([{"condition_id": "0xC", "closed": True}])

    fetch_market_end_state("https://gamma", "0xC", urlopen=urlopen)

    # Assert
    assert "closed=true" in seen["url"]


def test_the_open_read_refuses_to_claim_a_market_it_cannot_see():
    # Arrange - the venue's open listing does not carry this market, so it is
    # no longer open. That is "nothing to act on", distinct from a failed read.
    state = fetch_open_market_state(
        "https://gamma", "0xC",
        urlopen=lambda req, timeout=10: _FakeResp([]))

    # Act / Assert
    assert state is None


def test_the_open_read_is_unreachable_on_a_network_error():
    # Arrange
    def boom(req, timeout=10):
        raise OSError("network down")

    # Act
    state = fetch_open_market_state("https://gamma", "0xC", urlopen=boom)

    # Assert - unreadable, never None: a failed read must not read as
    # "closed by the venue" either.
    assert state is not None
    assert state.unreachable is True


def test_the_open_read_is_unreachable_on_a_malformed_row():
    # Arrange / Act
    state = fetch_open_market_state(
        "https://gamma", "0xC",
        urlopen=lambda req, timeout=10: _FakeResp([{"nope": 1}]))

    # Assert
    assert state is not None
    assert state.unreachable is True


def test_fetch_returns_unreachable_on_network_error():
    def boom(url, timeout=10):
        raise OSError("network down")
    state = fetch_market_end_state("https://gamma", "0xC", urlopen=boom)
    assert state.unreachable is True
    assert state.resolved is False


def test_fetch_returns_unreachable_on_empty_result():
    state = fetch_market_end_state(
        "https://gamma", "0xC",
        urlopen=lambda url, timeout=10: _FakeResp([]))
    assert state.unreachable is True


# ---------------------------------------------------------------------------
# sweep_market_resolutions: end-to-end against a real registry
# ---------------------------------------------------------------------------

@pytest.fixture
def registry(tmp_path):
    db = tmp_path / "shadow.db"
    init_db(db)
    reg = OrderRegistry(db_path=db, run_id="shadow-test")
    return reg, db


def _make_order(reg, cid, status="open"):
    import uuid
    now_ms = 1_000_000
    reg.create_order(OrderRecord(
        id=str(uuid.uuid4()), condition_id=cid, token_id="tok-up",
        side="BUY", price=0.47, original_size=20, status=status,
        posted_ts=now_ms, last_polled_ts=now_ms,
        pair_id=None, run_id=reg._run_id(),
    ))


def _make_quote(reg, cid):
    reg.log_quote(QuoteRecord(
        ts=1.0, condition_id=cid, token_id="tok-up", side="UP",
        price=0.47, size=20, market_slug="fake",
    ))


def test_sweep_records_resolved_and_cancels_open_rows(registry):
    reg, db = registry
    cid = "0xSTUCK"
    _make_order(reg, cid, status="open")
    _make_quote(reg, cid)

    # The dropped market is closed on the venue.
    def fetch(gamma_host, cid_in):
        return MarketEndState(condition_id=cid_in, closed=True, resolved=True)

    results = sweep_market_resolutions(
        reg, db, markets=[], fetch_state=fetch,  # empty universe => dropped
        now_fn=lambda: 2.0,
    )
    assert len(results) == 1
    r = results[0]
    assert r.action == "resolved_recorded"
    assert r.cancelled_rows == 1

    # The terminal marker landed in the resolutions table (cid lowercased
    # by the sweep for case-insensitive matching).
    rows = reg.get_all_resolutions()
    assert len(rows) == 1
    assert rows[0]["condition_id"] == cid.lower()
    # The resting order was cancelled.
    active = [o for o in reg.get_active_orders()
             if o.status in ("open", "pending", "partial")]
    assert active == []


def test_sweep_skips_markets_still_open_on_venue(registry):
    """Left the feed but the venue still has it open -> NOT resolved."""
    reg, db = registry
    cid = "0xDROPPEDBUTLIVE"
    _make_order(reg, cid, status="open")
    _make_quote(reg, cid)

    def fetch(gamma_host, cid_in):
        return MarketEndState(condition_id=cid_in, closed=False,
                              end_date_passed=False, resolved=False)

    results = sweep_market_resolutions(
        reg, db, markets=[], fetch_state=fetch, now_fn=lambda: 2.0)
    assert results[0].action == "still_open"
    # Nothing recorded.
    assert reg.get_all_resolutions() == []
    # Order untouched (still open).
    assert any(o["status"] == "open" for o in reg.get_all_orders())


def test_sweep_unreachable_does_not_mark(registry):
    """A failed gamma read must never mark a market resolved."""
    reg, db = registry
    cid = "0xNETFAIL"
    _make_order(reg, cid, status="open")
    _make_quote(reg, cid)

    def fetch(gamma_host, cid_in):
        return MarketEndState(condition_id=cid_in, unreachable=True)

    results = sweep_market_resolutions(
        reg, db, markets=[], fetch_state=fetch, now_fn=lambda: 2.0)
    assert results[0].action == "unreachable"
    assert reg.get_all_resolutions() == []
    assert any(o["status"] == "open" for o in reg.get_all_orders())


def test_sweep_skips_markets_still_in_universe(registry):
    """A market still in the current universe feed is active, not stale."""
    reg, db = registry
    cid = "0xACTIVE"
    _make_order(reg, cid, status="open")

    def fetch(gamma_host, cid_in):  # pragma: no cover - must not be called
        raise AssertionError("active market must not be fetched")

    results = sweep_market_resolutions(
        reg, db, markets=[FakeMarket(cid)], fetch_state=fetch, now_fn=lambda: 2.0)
    assert results == []
    assert reg.get_all_resolutions() == []


def test_sweep_already_resolved_is_idempotent(registry):
    """Re-sweeping a recorded cid does not rewrite or re-cancel."""
    reg, db = registry
    cid = "0xONCE"
    _make_order(reg, cid, status="open")
    _make_quote(reg, cid)

    def fetch(gamma_host, cid_in):
        return MarketEndState(condition_id=cid_in, closed=True, resolved=True)

    first = sweep_market_resolutions(
        reg, db, markets=[], fetch_state=fetch, now_fn=lambda: 2.0)
    assert first[0].action == "resolved_recorded"

    # Second sweep: the order is already cancelled, but the cid is in
    # resolutions -> short-circuits to already_resolved without fetching.
    second = sweep_market_resolutions(
        reg, db, markets=[], fetch_state=fetch, now_fn=lambda: 3.0)
    assert second[0].action == "already_resolved"
    assert len(reg.get_all_resolutions()) == 1


def test_sweep_partial_stranded_reports_without_exit(registry):
    """A partial at market end is reported, not fabricated-PnL-exited."""
    reg, db = registry
    cid = "0xPARTIAL"
    _make_order(reg, cid, status="partial")
    _make_quote(reg, cid)

    def fetch(gamma_host, cid_in):
        return MarketEndState(condition_id=cid_in, closed=True, resolved=True,
                              winner_token="Up",
                              winning_token_id="tok-up")

    results = sweep_market_resolutions(
        reg, db, markets=[], fetch_state=fetch, now_fn=lambda: 2.0)
    r = results[0]
    assert r.action == "partial_stranded"
    assert r.partial_rows == 1
    assert r.cancelled_rows == 0
    assert r.winning_token == "Up"
    # Resolution recorded.
    assert len(reg.get_all_resolutions()) == 1
    # No closes row fabricated (no held shares -- order was only partial).
    assert reg.get_all_closes() == []
    # Partial row untouched (not cancelled).
    assert any(o["status"] == "partial" for o in reg.get_all_orders())


# ---------------------------------------------------------------------------
# Settings: winner token id parsing + shadow settlement PnL booking
# ---------------------------------------------------------------------------

def test_parse_winner_token_id_from_clob_token_ids():
    row = {
        "condition_id": "0xC", "closed": True,
        "outcomes": ["Up", "Down"], "outcomePrices": ["1", "0"],
        "clobTokenIds": ["0xAAA", "0xBBB"],
    }
    s = parse_end_state(row, now_ts=0)
    assert s.winner_token == "Up"
    assert s.winning_token_id == "0xAAA"


def test_parse_winner_when_gamma_ships_json_strings():
    """gamma returns outcomes/outcomePrices/clobTokenIds as JSON strings."""
    import json as _json
    row = {
        "condition_id": "0xC", "closed": True,
        "outcomes": _json.dumps(["Up", "Down"]),
        "outcomePrices": _json.dumps(["1", "0"]),
        "clobTokenIds": _json.dumps(["0xAAA", "0xBBB"]),
    }
    s = parse_end_state(row, now_ts=0)
    assert s.resolved is True
    assert s.winner_token == "Up"
    assert s.winning_token_id == "0xAAA"


def test_book_shadow_settlement_realizes_pnl_for_held_shares(registry):
    """Held shares on the winning side redeem at $1.00; PnL = proceeds - cost."""
    reg, db = registry
    cid = "0xSETTLE"
    # The run bought 20 winning (UP) shares at 0.55.
    _make_order(reg, cid, status="open")
    _make_order(reg, cid, status="open")
    orders = reg.get_all_orders()  # dicts
    o1 = orders[0]
    reg.record_fill(FillRecord(
        trade_id=f"sh-fill-{o1['id'][:8]}", order_uuid=o1["id"],
        size=20.0, price=0.55, recorded_ts=0,
    ))
    # Give the winning order a winning token id.
    with reg._conn() as conn:
        conn.execute(
            "UPDATE orders SET token_id = 'tok-up' WHERE id = ?", (o1["id"],))
        conn.commit()
    # The other order rests on the losing token.
    with reg._conn() as conn:
        conn.execute(
            "UPDATE orders SET token_id = 'tok-down' WHERE id = ?", (orders[1]["id"],))
        conn.commit()

    state = MarketEndState(
        condition_id=cid, closed=True, resolved=True,
        winner_token="Up", winning_token_id="tok-up", winner_price=1.0)

    booked = book_shadow_settlement(
        reg, cid, state, run_id=reg._run_id(), now_fn=lambda: 2.0)
    assert booked is not None
    assert booked["shares"] == 20.0
    assert booked["proceeds"] == 20.0
    assert abs(booked["cost_basis"] - 11.0) < 1e-6
    assert abs(booked["realized_pnl"] - 9.0) < 1e-6

    # The close lands with method='shadow_settlement'.
    closes = reg.get_all_closes()
    assert len(closes) == 1
    assert closes[0]["method"] == "shadow_settlement"
    assert abs(closes[0]["realized_pnl"] - 9.0) < 1e-6


def test_book_shadow_settlement_skips_when_no_winner_token(registry):
    """No winner token id -> cannot tell which shares redeem -> no fabricated close."""
    reg, db = registry
    cid = "0xNOBERTTLE"
    _make_order(reg, cid, status="open")
    o1 = reg.get_all_orders()[0]
    reg.record_fill(FillRecord(
        trade_id=f"no-win-fill-{o1['id'][:8]}", order_uuid=o1["id"],
        size=5.0, price=0.5, recorded_ts=0))

    state = MarketEndState(condition_id=cid, closed=True, resolved=True,
                           winner_token="Up", winning_token_id=None)
    booked = book_shadow_settlement(
        reg, cid, state, run_id=reg._run_id(), now_fn=lambda: 2.0)
    assert booked is None
    assert reg.get_all_closes() == []


def test_sweep_with_book_settlement_records_close(registry):
    """The full sweep path books settlement when book_settlement=True."""
    reg, db = registry
    cid = "0xSWEEPSETTLE"
    _make_order(reg, cid, status="open")
    _make_order(reg, cid, status="open")
    o1 = reg.get_all_orders()[0]
    reg.record_fill(FillRecord(
        trade_id=f"sw-fill-{o1['id'][:8]}", order_uuid=o1["id"],
        size=10.0, price=0.5, recorded_ts=0))
    with reg._conn() as conn:
        conn.execute("UPDATE orders SET token_id = 'tok-up' WHERE id = ?",
                     (o1["id"],))
        conn.commit()
    _make_quote(reg, cid)

    def fetch(gamma_host, cid_in):
        return MarketEndState(condition_id=cid_in, closed=True, resolved=True,
                              winner_token="Up", winning_token_id="tok-up")

    results = sweep_market_resolutions(
        reg, db, markets=[], fetch_state=fetch, now_fn=lambda: 2.0,
        book_settlement=True)
    r = results[0]
    assert r.action == "resolved_recorded"
    assert r.settled_shares == 10.0
    assert abs(r.settled_pnl - 5.0) < 1e-6
    assert len(reg.get_all_resolutions()) == 1
    assert len(reg.get_all_closes()) == 1


def test_book_shadow_settlement_skips_when_condition_already_closed(registry):
    """A merge/exit close already realised PnL; settling again double-counts."""
    from core_brain.order_registry import CloseRecord
    reg, db = registry
    cid = "0xALREADYCLOSED"
    _make_order(reg, cid, status="open")
    o1 = reg.get_all_orders()[0]
    reg.record_fill(FillRecord(
        trade_id=f"ac-fill-{o1['id'][:8]}", order_uuid=o1["id"],
        size=10.0, price=0.5, recorded_ts=0))
    with reg._conn() as conn:
        conn.execute("UPDATE orders SET token_id = 'tok-up' WHERE id = ?",
                     (o1["id"],))
        conn.commit()
    # The run already merged/exited this condition.
    reg.log_close(CloseRecord(
        ts=1.0, condition_id=cid, method="shadow_merge", shares=10.0,
        cost_basis=5.0, proceeds=10.0, realized_pnl=5.0,
        run_id=reg._run_id()))

    state = MarketEndState(condition_id=cid, closed=True, resolved=True,
                           winner_token="Up", winning_token_id="tok-up")
    booked = book_shadow_settlement(
        reg, cid, state, run_id=reg._run_id(), now_fn=lambda: 2.0)
    assert booked is None
    # Only the merge close remains -- no second, double-counted settlement.
    assert len(reg.get_all_closes()) == 1


def test_repeat_sweep_book_settlement_is_idempotent(registry):
    """A re-run settles a market recorded earlier (via the already_resolved
    path) and does not double-book once the settlement close exists."""
    reg, db = registry
    cid = "0xRESETTLE"
    _make_order(reg, cid, status="open")
    o1 = reg.get_all_orders()[0]
    reg.record_fill(FillRecord(
        trade_id=f"re-fill-{o1['id'][:8]}", order_uuid=o1["id"],
        size=10.0, price=0.5, recorded_ts=0))
    with reg._conn() as conn:
        conn.execute("UPDATE orders SET token_id = 'tok-up' WHERE id = ?",
                     (o1["id"],))
        conn.commit()
    _make_quote(reg, cid)

    def fetch(gamma_host, cid_in):
        return MarketEndState(condition_id=cid_in, closed=True, resolved=True,
                              winner_token="Up", winning_token_id="tok-up")

    # First pass records resolution + settlement.
    r1 = sweep_market_resolutions(
        reg, db, markets=[], fetch_state=fetch, now_fn=lambda: 2.0,
        book_settlement=True)[0]
    assert r1.action == "resolved_recorded"
    assert abs(r1.settled_pnl - 5.0) < 1e-6
    assert len(reg.get_all_closes()) == 1

    # Second pass: already_resolved, but settlement must NOT double-book
    # (the existing shadow_settlement close is found by the guard).
    r2 = sweep_market_resolutions(
        reg, db, markets=[], fetch_state=fetch, now_fn=lambda: 3.0,
        book_settlement=True)[0]
    assert r2.action == "already_resolved"
    assert r2.settled_shares == 0.0
    assert len(reg.get_all_closes()) == 1


def test_sweep_live_does_not_book_or_cancel(registry):
    """Live semantics: default no settlement, and cancel_resting=False leaves rows."""
    reg, db = registry
    cid = "0xLIVE"
    _make_order(reg, cid, status="open")
    _make_quote(reg, cid)

    def fetch(gamma_host, cid_in):
        return MarketEndState(condition_id=cid_in, closed=True, resolved=True,
                              winner_token="Up", winning_token_id="tok-up")

    results = sweep_market_resolutions(
        reg, db, markets=[], fetch_state=fetch, now_fn=lambda: 2.0,
        book_settlement=False, cancel_resting=False)
    r = results[0]
    assert r.action == "resolved_recorded"
    assert r.cancelled_rows == 0
    assert r.settled_shares == 0.0
    # Resolution recorded, but no fabricated close and no rogue cancel.
    assert len(reg.get_all_resolutions()) == 1
    assert reg.get_all_closes() == []
    assert any(o["status"] == "open" for o in reg.get_all_orders())
