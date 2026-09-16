"""The reversion view is the dashboard's read-only window onto the forward test.

Journeys under test:
1. As the Owner, the page tells me how much the watch has collected, because a
   verdict read off nine trades is not yet an answer.
2. As the Owner, a store that does not exist yet renders a page that says so
   rather than an error, because the page is opened WHILE the watch runs.
3. As the Owner, leagues and price bands are reported APART, because Dota's
   jumps stick where LoL's come back and one pooled number is true of neither.
4. As the Owner, a group's verdict is decided against the same pre-registered
   bar the backward measurement was held to, so the page cannot soften it.
5. As the Owner, a store this viewer cannot read says so, because "no trades
   yet" for a file with the wrong schema is a page that lies for hours.
6. As the Owner, a fade is priced at the quote it would really have traded at,
   never at the mid the move was measured on.
7. As the Owner, the production registry is refused by name at EVERY entry
   point -- the writer and both readers -- not only at the resolver a route
   happens to call first.
8. As the Owner, a jump caught minutes before the watch ends is settled by
   the next run rather than lost, because the store is the record.
9. As the Owner, a store name that merely POINTS at the registry is refused
   too, because SQLite follows a symbolic link and the writer would then
   create its tables inside the registry.
10. As the Owner, a row too old to ever be scored is counted as unscorable
    rather than as pending, because pending promises a result that is coming.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from core_brain.reversion_view import (
    MIN_GROUP_TRADES,
    RefusedStore,
    resolve_reversion_db,
    reversion_results,
    reversion_sweep_grid,
    reversion_status,
)
from core_brain.reversion_watch import (
    FORWARD,
    band_of,
    fade_entry,
    fade_exit,
    has_started,
    open_store,
    pending_trades,
    realised_cents,
    settle,
)

BOOK = {"bid": 0.44, "bid_size": 900.0, "ask": 0.45, "ask_size": 800.0,
        "mid": 0.445}


def _store(tmp_path, rows=(), quotes=6):
    """A store holding the given (league, band, in_game, pnl) trades."""
    conn = open_store(tmp_path / "reversion.db")
    for index in range(quotes):
        conn.execute("INSERT INTO quotes VALUES (?,?,?,?)",
                     (f"lol-a-b-{index % 2}", 1_700_000_000 + index * 60,
                      0.44, 0.45))
    for index, (league, band, in_game, pnl) in enumerate(rows):
        conn.execute(
            "INSERT INTO events (ts,slug,league,band,in_game,direction,"
            "mid_before,mid_now,entry_px,entry_size,spread_c,token_id,exit_ts,"
            "exit_px,exit_size,pnl_c) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1_700_000_000 + index, f"{league}-a-b", league, band, in_game,
             "up", 0.40, 0.44, 0.44, 500.0, 1.0, f"tok-{index}",
             None if pnl is None else 1_700_000_900 + index,
             None if pnl is None else 0.42, 500.0, pnl))
    conn.commit()
    conn.close()
    return tmp_path / "reversion.db"


def test_status_reports_what_has_been_collected(tmp_path):
    path = _store(tmp_path, rows=[("lol", "mid", 1, 1.5), ("lol", "mid", 1, None)])

    status = reversion_status(path)

    assert status["state"] == "READY"
    assert status["events"] == 2
    assert status["scored"] == 1
    assert status["pending"] == 1
    assert status["markets"] == 2


def test_status_of_a_store_that_does_not_exist_yet_is_a_state(tmp_path):
    status = reversion_status(tmp_path / "not-yet.db")

    assert status["state"] == "MISSING"
    assert status["exists"] is False
    assert status["events"] == 0


def test_results_before_any_trade_is_scored_say_so(tmp_path):
    path = _store(tmp_path, rows=[("lol", "mid", 1, None)])

    results = reversion_results(path)

    assert results["state"] == "NOT_SCORED"
    assert results["pending"] == 1
    assert results["groups"] == []


def test_leagues_and_bands_are_reported_apart(tmp_path):
    path = _store(tmp_path, rows=(
        [("lol", "mid", 1, 2.0)] * 12
        + [("lol", "tail", 1, -4.0)] * 11
        + [("dota2", "mid", 1, 0.0)] * 10))

    groups = {(g["league"], g["band"]): g for g in reversion_results(path)["groups"]}

    assert set(groups) == {("lol", "mid"), ("lol", "tail"), ("dota2", "mid")}
    assert groups[("lol", "mid")]["cents"] == pytest.approx(2.0)
    assert groups[("lol", "tail")]["cents"] == pytest.approx(-4.0)


def test_a_thin_group_reports_no_certainty(tmp_path):
    thin = MIN_GROUP_TRADES - 1
    path = _store(tmp_path, rows=[("lol", "mid", 1, 3.0)] * thin)

    group = reversion_results(path)["groups"][0]

    assert group["trades"] == thin
    assert group["sureness"] is None
    assert group["verdict"] == "NO_SIGNAL"


def test_a_group_below_the_bar_is_not_called_a_signal(tmp_path):
    # Same mean either way; only the scatter differs, so only certainty decides.
    noisy = [("lol", "mid", 1, pnl) for pnl in (9.0, -8.0) * 10]
    path = _store(tmp_path, rows=noisy)

    group = reversion_results(path)["groups"][0]

    assert group["sureness"] < 3.0
    assert group["verdict"] == "NO_SIGNAL"


def test_a_steady_paying_group_clears_the_bar(tmp_path):
    steady = [("lol", "mid", 1, pnl) for pnl in (1.6, 1.4) * 15]
    path = _store(tmp_path, rows=steady)

    group = reversion_results(path)["groups"][0]

    assert group["sureness"] >= 3.0
    assert group["verdict"] == "PAYS"


def test_in_game_and_pre_game_are_split(tmp_path):
    path = _store(tmp_path, rows=(
        [("lol", "mid", 1, 2.0)] * 10 + [("lol", "mid", 0, -1.0)] * 10))

    phases = {row["phase"]: row for row in reversion_results(path)["in_game"]}

    assert phases["in_game"]["cents"] == pytest.approx(2.0)
    assert phases["pre_game"]["cents"] == pytest.approx(-1.0)


def test_a_store_with_the_wrong_schema_is_not_reported_as_empty(tmp_path):
    # An older watch wrote its quotes to a table called `ticks`. Reading that
    # file must not render a page that quietly says zero.
    path = tmp_path / "reversion.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE ticks (slug TEXT, ts INTEGER)")
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, pnl_c REAL)")

    assert reversion_status(path)["state"] == "UNREADABLE"
    assert reversion_results(path)["state"] == "UNREADABLE"


def test_the_order_registry_is_refused_by_name(tmp_path):
    with pytest.raises(RefusedStore):
        resolve_reversion_db(tmp_path / "orders.db")


def test_the_order_registry_is_refused_through_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SHL_REVERSION_DB", str(tmp_path / "orders.db"))

    with pytest.raises(RefusedStore):
        resolve_reversion_db()


def test_the_viewer_never_creates_or_writes_a_store(tmp_path):
    path = _store(tmp_path, rows=[("lol", "mid", 1, 1.0)] * 10)

    reversion_status(path)
    reversion_results(path)

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 10


def test_a_fade_enters_at_the_quote_not_at_the_mid():
    # An up-move is sold, and a sale happens at the bid. Pricing the entry at
    # the mid would hand the strategy half the spread it must actually pay.
    direction, entry_px, size = fade_entry(BOOK, move=+0.04)
    assert (direction, entry_px, size) == ("up", 0.44, 900.0)

    direction, entry_px, size = fade_entry(BOOK, move=-0.04)
    assert (direction, entry_px, size) == ("down", 0.45, 800.0)


def test_a_fade_exits_on_the_opposite_side():
    assert fade_exit(BOOK, "up") == (0.45, 800.0)
    assert fade_exit(BOOK, "down") == (0.44, 900.0)


def test_a_round_trip_that_only_recovers_the_spread_makes_nothing():
    # Sell the bid at 0.44, buy the ask back at the same book: a dead loss of
    # the spread, which is exactly what an edge has to beat.
    assert realised_cents("up", 0.44, 0.45) == pytest.approx(-1.0)
    assert realised_cents("down", 0.45, 0.44) == pytest.approx(-1.0)


def test_a_fade_that_works_pays_the_move_minus_the_spread():
    assert realised_cents("up", 0.44, 0.42) == pytest.approx(2.0)


def test_the_band_follows_the_measured_middle():
    assert band_of(0.50) == "mid"
    assert band_of(0.35) == "mid"
    assert band_of(0.65) == "mid"
    assert band_of(0.20) == "tail"
    assert band_of(0.88) == "tail"


def test_an_unstamped_game_time_is_neither_before_nor_during():
    assert has_started("", 1_700_000_000) is None
    assert has_started("2020-01-01 00:00:00+00", 1_700_000_000) == 1
    assert has_started("2099-01-01 00:00:00+00", 1_700_000_000) == 0


class _OneBook:
    """A venue that answers every book read with the same two-sided quote."""

    def __init__(self, bid=0.42, ask=0.43):
        self.quote = {"bids": [{"price": str(bid), "size": "700"}],
                      "asks": [{"price": str(ask), "size": "600"}]}
        self.reads = 0

    def get(self, url, params=None, timeout=None):
        self.reads += 1
        return self

    def raise_for_status(self):
        return None

    def json(self):
        return self.quote


def test_the_writer_refuses_the_order_registry_too(tmp_path):
    # The refusal used to live only in the resolver the HTTP route called, so
    # a direct call to the writer walked straight past it and would have
    # CREATED tables inside the production registry.
    with pytest.raises(RefusedStore):
        open_store(tmp_path / "orders.db")

    assert not (tmp_path / "orders.db").exists()


def test_both_readers_refuse_the_order_registry_directly(tmp_path):
    registry = tmp_path / "orders.db"
    registry.write_bytes(b"")

    with pytest.raises(RefusedStore):
        reversion_status(registry)
    with pytest.raises(RefusedStore):
        reversion_results(registry)


def test_a_jump_caught_near_the_deadline_is_settled_by_the_next_run(tmp_path):
    # The trade lived in memory and the process exited; only the store knows it
    # is owed an exit. A later run must be able to pick it up and score it.
    path = tmp_path / "reversion.db"
    conn = open_store(path)
    opened_at = 1_700_000_000
    conn.execute(
        "INSERT INTO events (ts,slug,league,band,in_game,direction,mid_before,"
        "mid_now,entry_px,entry_size,spread_c,token_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (opened_at, "lol-a-b", "lol", "mid", 1, "up", 0.40, 0.44, 0.44,
         500.0, 1.0, "tok-1"))
    conn.commit()
    conn.close()

    later = open_store(path)
    owed = pending_trades(later)
    assert [t["id"] for t in owed] == [1]
    assert owed[0]["due"] == opened_at + FORWARD

    venue = _OneBook(bid=0.42, ask=0.43)
    left = settle(later, owed, session=venue, now=opened_at + FORWARD)

    assert left == []
    # Faded an up-move at 0.44, bought it back at the 0.43 ask: one cent.
    row = later.execute(
        "SELECT pnl_c, exit_px FROM events WHERE id=1").fetchone()
    assert row[0] == pytest.approx(1.0)
    assert row[1] == pytest.approx(0.43)
    later.close()


def test_a_trade_still_inside_its_window_is_left_alone(tmp_path):
    path = tmp_path / "reversion.db"
    conn = open_store(path)
    conn.execute(
        "INSERT INTO events (ts,slug,league,band,in_game,direction,mid_before,"
        "mid_now,entry_px,entry_size,spread_c,token_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (1_700_000_000, "lol-a-b", "lol", "mid", 1, "up", 0.40, 0.44, 0.44,
         500.0, 1.0, "tok-1"))
    conn.commit()

    venue = _OneBook()
    left = settle(conn, pending_trades(conn), session=venue,
                  now=1_700_000_000 + FORWARD - 1)

    assert len(left) == 1
    assert venue.reads == 0
    assert conn.execute("SELECT pnl_c FROM events").fetchone()[0] is None
    conn.close()


def test_a_store_written_before_the_token_column_is_migrated(tmp_path):
    path = tmp_path / "reversion.db"
    with sqlite3.connect(path) as conn:
        # The shape the first watch wrote: everything except the token.
        conn.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, ts INTEGER, "
            "slug TEXT, league TEXT, band TEXT, in_game INTEGER, "
            "direction TEXT, mid_before REAL, mid_now REAL, entry_px REAL, "
            "entry_size REAL, spread_c REAL, exit_ts INTEGER, exit_px REAL, "
            "exit_size REAL, pnl_c REAL)")

    migrated = open_store(path)

    columns = {row[1] for row in migrated.execute("PRAGMA table_info(events)")}
    assert "token_id" in columns
    migrated.close()

def test_a_symlink_pointing_at_the_registry_is_refused(tmp_path):
    # The name alone is not the file: SQLite follows the link, so a link called
    # `reversion.db` would hand the writer the production registry.
    registry = tmp_path / "orders.db"
    registry.write_bytes(b"")
    alias = tmp_path / "reversion.db"
    try:
        alias.symlink_to(registry)
    except (OSError, NotImplementedError):
        pytest.skip("this platform will not create symbolic links unprivileged")

    with pytest.raises(RefusedStore):
        resolve_reversion_db(alias)
    with pytest.raises(RefusedStore):
        open_store(alias)
    with pytest.raises(RefusedStore):
        reversion_status(alias)


def test_a_row_recorded_before_the_token_is_unscorable_not_pending(tmp_path):
    # ALTER TABLE cannot invent the token these rows never carried, and nothing
    # here can map a slug back to the token it traded on. Calling them pending
    # would park them in settle forever and promise a result that never lands.
    path = tmp_path / "reversion.db"
    conn = open_store(path)
    conn.execute("INSERT INTO quotes VALUES (?,?,?,?)",
                 ("lol-a-b", 1_700_000_000, 0.44, 0.45))
    conn.execute(
        "INSERT INTO events (ts,slug,league,band,in_game,direction,mid_before,"
        "mid_now,entry_px,entry_size,spread_c,token_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (1_700_000_000, "lol-a-b", "lol", "mid", 1, "up", 0.40, 0.44, 0.44,
         500.0, 1.0, None))
    conn.execute(
        "INSERT INTO events (ts,slug,league,band,in_game,direction,mid_before,"
        "mid_now,entry_px,entry_size,spread_c,token_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (1_700_000_100, "lol-c-d", "lol", "mid", 1, "up", 0.40, 0.44, 0.44,
         500.0, 1.0, "tok-live"))
    conn.commit()

    assert [t["token"] for t in pending_trades(conn)] == ["tok-live"]

    status = reversion_status(path)
    assert status["events"] == 2
    assert status["pending"] == 1
    assert status["unscorable"] == 1
    assert reversion_results(path)["unscorable"] == 1
    conn.close()


def test_an_unscorable_row_is_never_parked_in_settlement(tmp_path):
    conn = open_store(tmp_path / "reversion.db")
    conn.execute(
        "INSERT INTO events (ts,slug,league,band,in_game,direction,mid_before,"
        "mid_now,entry_px,entry_size,spread_c,token_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (1_700_000_000, "lol-a-b", "lol", "mid", 1, "up", 0.40, 0.44, 0.44,
         500.0, 1.0, None))
    conn.commit()

    venue = _OneBook()
    assert settle(conn, pending_trades(conn), session=venue,
                  now=1_700_000_000 + FORWARD * 10) == []
    assert venue.reads == 0
    conn.close()
