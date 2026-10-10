"""Tests for engine.cycle_stream — the live cycle-telemetry ring (#51).

Covers the ring file (append, rotation, tail reads, concurrent appends) and
the cycle_intent table (decide inserts, submit updates, retention window).
All tests use tmp_path; nothing ever touches live/run/.
"""
from __future__ import annotations

import builtins
import contextlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from core_brain.cycle_stream import (
    KEEP_LINES,
    close_intent_connections,
    emit,
    fill_extra,
    lifecycle_extra,
    make_fill_observer,
    relayer_extra,
    read_ring,
)
from core_brain.order_registry import FillRecord, OrderRecord

REQUIRED_FIELDS = {
    "ts", "service", "cycle", "phase", "action",
    "market_slug", "reason", "latency_ms", "pid", "extra",
}


def _parse_lines(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _count_ring_reads(monkeypatch, ring: Path) -> list[str]:
    """Record every read-open of `ring`, so a test can assert how few there are.

    The rotation check is the only thing that opens the ring for reading, so
    the length of the returned list is the number of whole-file rereads.
    """
    reads: list[str] = []
    real_open = builtins.open

    def counting_open(file, mode="r", *args, **kwargs):
        if str(file) == str(ring) and "r" in str(mode):
            reads.append(str(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", counting_open)
    return reads


def _query_intent(db: Path, sql: str, params=()) -> list[tuple]:
    # closing(), not a bare `with`: a sqlite3 connection's context manager
    # commits on exit but leaves the handle open, and on Windows that open
    # handle blocks anything that later replaces the file.
    with contextlib.closing(sqlite3.connect(str(db))) as conn:
        return conn.execute(sql, params).fetchall()


class TestRingFile:
    def test_emit_creates_file(self, tmp_path):
        ring = tmp_path / "cycle_events.jsonl"
        emit(1, "reconciling", "reconcile_ok", ring_path=ring)
        assert ring.exists()
        events = _parse_lines(ring)
        assert len(events) == 1
        assert REQUIRED_FIELDS <= set(events[0])
        assert events[0]["cycle"] == 1
        assert events[0]["action"] == "reconcile_ok"

    def test_emit_appends_not_overwrites(self, tmp_path):
        ring = tmp_path / "cycle_events.jsonl"
        for i in range(1, 4):
            emit(i, "reconciling", "reconcile_ok", ring_path=ring)
        events = _parse_lines(ring)
        assert len(events) == 3
        assert [e["cycle"] for e in events] == [1, 2, 3]

    def test_rotation_at_500(self, tmp_path):
        ring = tmp_path / "cycle_events.jsonl"
        for i in range(1, 511):
            emit(i, "reconciling", "reconcile_ok", service="query",
                 ring_path=ring)
        # Algorithm: append, then if lines > 500 keep the last 400. The 501st
        # append triggers one rotation (keeps events 102..501 = 400 lines);
        # events 502..510 add 9 more. The plan's draft numbers (410 / first
        # cycle 111) do not follow from its own rule; these are the actual ones.
        events = _parse_lines(ring)
        assert len(events) == 409
        assert events[0]["cycle"] == 102
        assert events[-1]["cycle"] == 510

    def test_rotation_preserves_valid_json(self, tmp_path):
        ring = tmp_path / "cycle_events.jsonl"
        for i in range(1, 601):
            emit(i, "scanning", "rerank_done", service="query", ring_path=ring)
        events = _parse_lines(ring)
        assert len(events) == 499  # rotated once at 501, then 502..600 appended
        for e in events:
            assert REQUIRED_FIELDS <= set(e)
            assert e["cycle"] >= 102

    def test_decide_service_never_rotates(self, tmp_path):
        # Q3: only the query process owns rotation. Decide appends must never
        # trigger a rewrite, so a concurrent query rotation cannot lose them.
        ring = tmp_path / "cycle_events.jsonl"
        # phase="quoting" + action="decide" also writes a cycle_intent row, so
        # db_path has to point into tmp_path. Without it the row lands in the
        # production registry, which conftest now blocks outright.
        db = tmp_path / "live.db"
        for i in range(1, 511):
            emit(i, "quoting", "decide", service="decide", ring_path=ring,
                 db_path=db)
        assert len(_parse_lines(ring)) == 510

    def test_emit_never_raises(self, tmp_path, capsys):
        # ring_path is a directory: the append fails, but emit must not raise.
        emit(1, "reconciling", "reconcile_ok", ring_path=tmp_path)
        assert "WARNING" in capsys.readouterr().err

    def test_read_ring_tail(self, tmp_path):
        ring = tmp_path / "cycle_events.jsonl"
        for i in range(1, 51):
            emit(i, "reconciling", "reconcile_ok", ring_path=ring)
        events = read_ring(ring_path=ring, tail=10)
        assert len(events) == 10
        assert [e["cycle"] for e in events] == list(range(41, 51))

    def test_ring_is_not_reread_on_every_append(self, tmp_path, monkeypatch):
        # The rotation check used to open and readlines() the whole ring on
        # every single emit. On Windows that read-open costs ~20ms once the
        # file has just been modified (Defender rescans it), so a 500-emit
        # loop spent ten seconds inside open(). The line count is knowable
        # without re-reading, so the read must be rare, not per-append.
        ring = tmp_path / "cycle_events.jsonl"
        reads = _count_ring_reads(monkeypatch, ring)
        for i in range(1, 201):
            emit(i, "reconciling", "reconcile_ok", service="query",
                 ring_path=ring)
        assert len(reads) <= 2, f"{len(reads)} full reads for 200 appends"

    def test_rotation_still_fires_after_an_external_writer_appends(
            self, tmp_path, monkeypatch):
        # The fleet and screener append to the ring without importing
        # core_brain, so the in-process line count can go stale. A size that
        # does not match our own bookkeeping must fall back to a real read.
        ring = tmp_path / "cycle_events.jsonl"
        reads = _count_ring_reads(monkeypatch, ring)
        for i in range(1, 100):
            emit(i, "reconciling", "reconcile_ok", service="query",
                 ring_path=ring)
        with open(ring, "a", encoding="utf-8") as fh:
            for i in range(450):
                fh.write(json.dumps({"cycle": 1000 + i}) + "\n")
        emit(999, "reconciling", "reconcile_ok", service="query",
             ring_path=ring)
        # Exactly two reads: one to seed the count, one because the external
        # 450 lines moved the size off our bookkeeping. Asserting the count
        # rather than only the rotation is what makes this fail against the
        # old implementation, which reread the ring on all 100 emits.
        assert len(reads) == 2, f"{len(reads)} full reads"
        assert len(_parse_lines(ring)) == KEEP_LINES

    def test_concurrent_appends(self, tmp_path):
        ring = tmp_path / "cycle_events.jsonl"

        def worker(pid_tag: int):
            for i in range(50):
                emit(i, "reconciling", "reconcile_ok", service="fleet",
                     ring_path=ring, extra={"worker": pid_tag})

        threads = [threading.Thread(target=worker, args=(w,)) for w in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Every line is a complete JSON object regardless of interleaving.
        events = _parse_lines(ring)
        assert len(events) == 150


class TestCycleIntent:
    def test_cycle_intent_write(self, tmp_path):
        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        emit(7, "quoting", "decide", market_slug="will-eth-pump",
             extra={"intent_count": 3, "top_skip_reason": "spread_too_wide"},
             ring_path=ring, db_path=db)
        rows = _query_intent(db, "SELECT cycle, market_slug, intent_count, "
                                  "submitted, cancelled, top_skip_reason "
                                  "FROM cycle_intent")
        assert len(rows) == 1
        cycle, slug, intent_count, submitted, cancelled, skip = rows[0]
        assert (cycle, slug, intent_count) == (7, "will-eth-pump", 3)
        assert skip == "spread_too_wide"
        assert (submitted, cancelled) == (0, 0)

    def test_cycle_intent_submit_updates_row(self, tmp_path):
        # The submit event of the same visit updates the decide row instead of
        # creating a second one: one row per market visit, with outcomes.
        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        emit(7, "quoting", "decide", market_slug="will-eth-pump",
             extra={"intent_count": 2, "condition_id": "0xcid"},
             ring_path=ring, db_path=db)
        emit(7, "quoting", "submit", market_slug="will-eth-pump",
             extra={"submitted": 1, "cancelled": 1},
             ring_path=ring, db_path=db)
        rows = _query_intent(db, "SELECT intent_count, submitted, cancelled, "
                                  "condition_id FROM cycle_intent")
        assert len(rows) == 1
        assert rows[0] == (2, 1, 1, "0xcid")

    def test_cycle_intent_submit_targets_exact_visit(self, tmp_path):
        """A later decide for the same market must not capture an earlier submit."""
        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        emit(7, "quoting", "decide", market_slug="mkt",
             extra={"intent_count": 1}, ring_path=ring, db_path=db)
        emit(8, "quoting", "decide", market_slug="mkt",
             extra={"intent_count": 2}, ring_path=ring, db_path=db)
        emit(7, "quoting", "submit", market_slug="mkt",
             extra={"submitted": 1, "cancelled": 0}, ring_path=ring, db_path=db)
        rows = _query_intent(db, "SELECT cycle, intent_count, submitted, "
                                  "cancelled FROM cycle_intent ORDER BY id")
        assert rows[0] == (7, 1, 1, 0)
        assert rows[1] == (8, 2, 0, 0)

    def test_cycle_intent_market_error_persists_partial_counts(self, tmp_path):
        """A submit/cancel failure records the partial counts, not zeros."""
        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        emit(7, "quoting", "decide", market_slug="mkt",
             extra={"intent_count": 2}, ring_path=ring, db_path=db)
        emit(7, "quoting", "market_error", market_slug="mkt",
             reason="submit/cancel: boom",
             extra={"submitted": 1, "cancelled": 0}, ring_path=ring, db_path=db)
        rows = _query_intent(db, "SELECT submitted, cancelled FROM cycle_intent")
        assert rows[0] == (1, 0)

    def test_cycle_intent_unmatched_update_warns(self, tmp_path, capsys):
        """Updating a non-existent visit row emits a stderr warning and changes nothing."""
        from core_brain.cycle_stream import _update_cycle_intent
        db = tmp_path / "live.db"
        ring = tmp_path / "events.jsonl"
        emit(1, "quoting", "decide", market_slug="existing-slug",
             extra={"intent_count": 1}, ring_path=ring, db_path=db, run_id="run-1")
        capsys.readouterr()  # clear any prior output

        _update_cycle_intent(
            market_slug="unmatched-slug",
            cycle=1,
            run_id="run-1",
            submitted=1,
            cancelled=0,
            db_path=db,
        )
        captured = capsys.readouterr()
        assert "WARNING: cycle_intent update matched 0 rows:" in captured.err
        assert "market_slug=unmatched-slug" in captured.err
        assert "cycle=1" in captured.err
        assert "run_id=run-1" in captured.err

        rows = _query_intent(db, "SELECT market_slug, submitted FROM cycle_intent")
        assert len(rows) == 1
        assert rows[0] == ("existing-slug", 0)

    def test_cycle_intent_missing_db_warns(self, tmp_path, capsys):
        """Updating against a non-existent db emits a stderr warning and returns."""
        from core_brain.cycle_stream import _update_cycle_intent
        missing_db = tmp_path / "does_not_exist.db"
        _update_cycle_intent(
            market_slug="slug",
            cycle=1,
            run_id="run-1",
            submitted=1,
            db_path=missing_db,
        )
        captured = capsys.readouterr()
        assert "WARNING: cycle_intent update skipped, db missing:" in captured.err
        assert str(missing_db) in captured.err

    def test_one_connection_serves_many_intent_writes(self, tmp_path, monkeypatch):
        """Connecting costs ~3ms, and emit() runs once per market visit.

        The engine writes one registry for its whole life, so the connection
        is opened once and kept, not rebuilt per decide event.
        """
        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        connects: list[str] = []
        real_connect = sqlite3.connect

        def counting_connect(target, *args, **kwargs):
            connects.append(str(target))
            return real_connect(target, *args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", counting_connect)
        for i in range(1, 51):
            emit(i, "quoting", "decide", market_slug="m",
                 ring_path=ring, db_path=db)

        assert connects.count(str(db)) == 1, (
            f"{connects.count(str(db))} connections for 50 decide events")

    def test_a_dead_cached_handle_still_attributes_the_submit(self, tmp_path):
        """Closing the handle in place is what the retry actually exists for.

        `close_intent_connections()` also drops the cache entry, so a test
        using it never reaches the retry -- the next write just opens a fresh
        connection. Closing the handle while leaving it cached is the real
        failure state, and without the retry the submit event's counts never
        reach the row the decide event inserted.
        """
        from core_brain import cycle_stream

        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        emit(4, "quoting", "decide", market_slug="m",
             ring_path=ring, db_path=db)

        cycle_stream._DB_CACHE[str(db)].close()  # dead, and still cached

        emit(4, "quoting", "submit", market_slug="m",
             extra={"submitted": 2, "cancelled": 1},
             ring_path=ring, db_path=db)

        assert _query_intent(
            db, "SELECT submitted, cancelled FROM cycle_intent") == [(2, 1)]

    def test_a_reopened_connection_keeps_writing(self, tmp_path):
        """The public close is a supported call, not a one-way door."""
        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        emit(1, "quoting", "decide", market_slug="m",
             ring_path=ring, db_path=db)

        close_intent_connections()

        emit(2, "quoting", "decide", market_slug="m",
             ring_path=ring, db_path=db)
        cycles = [row[0] for row in
                  _query_intent(db, "SELECT cycle FROM cycle_intent ORDER BY id")]
        assert cycles == [1, 2]

    def test_replacing_the_store_needs_the_handle_closed_first(self, tmp_path):
        """Swapping the file under a live handle is not something the retry
        can rescue: on POSIX the open handle stays bound to the old inode, the
        write lands in a file nobody reads, and no error is raised to retry on.
        Closing first is the procedure, and this pins it -- after the close,
        the next event lands in the database that is actually on the path.
        """
        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        emit(1, "quoting", "decide", market_slug="m",
             ring_path=ring, db_path=db)
        assert _query_intent(db, "SELECT COUNT(*) FROM cycle_intent")[0] == (1,)

        close_intent_connections()  # nothing holds the file now
        replacement = tmp_path / "fresh.db"
        replacement.write_bytes(b"")
        os.replace(replacement, db)

        emit(2, "quoting", "decide", market_slug="m",
             ring_path=ring, db_path=db)
        cycles = [row[0] for row in
                  _query_intent(db, "SELECT cycle FROM cycle_intent ORDER BY id")]
        assert cycles == [2], "the record must land in the database on the path"

    def test_a_locked_database_warns_and_never_stalls_the_loop(
            self, tmp_path, capsys):
        """A contended write is dropped with a warning, not blocked on.

        emit() runs on the trading loop. Waiting out a long lock would stall a
        cycle, which costs more than the telemetry row is worth, so the retry
        is bounded and the row is discarded if the lock outlasts it.
        """
        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        emit(1, "quoting", "decide", market_slug="m",
             ring_path=ring, db_path=db)

        blocker = sqlite3.connect(str(db), timeout=0.1)
        blocker.execute("BEGIN EXCLUSIVE")
        try:
            started = time.monotonic()
            emit(2, "quoting", "decide", market_slug="m",
                 ring_path=ring, db_path=db)
            elapsed = time.monotonic() - started
        finally:
            blocker.rollback()
            blocker.close()

        assert "cycle_intent insert failed" in capsys.readouterr().err
        assert elapsed < 10, f"emit blocked {elapsed:.1f}s on a locked database"

        # The loop keeps going: the next write lands once the lock is gone.
        emit(3, "quoting", "decide", market_slug="m",
             ring_path=ring, db_path=db)
        cycles = [row[0] for row in
                  _query_intent(db, "SELECT cycle FROM cycle_intent ORDER BY id")]
        assert cycles == [1, 3]

    def test_cycle_intent_retention_200(self, tmp_path):
        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        for i in range(1, 211):
            emit(i, "quoting", "decide", market_slug=f"m{i % 3}",
                 ring_path=ring, db_path=db)
        (count,) = _query_intent(db, "SELECT COUNT(*) FROM cycle_intent")[0]
        assert count == 200


def _fill_and_order():
    fill = FillRecord(
        trade_id="tr_fill_1",
        order_uuid="order-uuid-1",
        size=3.0,
        price=0.48,
        venue_ts=1723840001000,
        recorded_ts=1723840005000,
    )
    order = OrderRecord(
        id="order-uuid-1",
        order_id="0xvenue_fill_1",
        condition_id="0xcond_fill",
        token_id="0xtok_fill",
        side="BUY",
        price=0.50,
        original_size=10.0,
        status="open",
        posted_ts=1723840000000,
        last_polled_ts=1723840000000,
        pair_id="pair_fill_1",
    )
    return fill, order


class TestFillTelemetry:
    def test_fill_extra_uses_fill_size_and_price(self):
        fill, order = _fill_and_order()
        extra = fill_extra(fill, order)
        assert extra["size"] == 3.0
        assert extra["price"] == 0.48
        assert extra["side"] == "BUY"
        assert extra["trade_id"] == "tr_fill_1"
        assert extra["condition_id"] == "0xcond_fill"
        assert extra["token_id"] == "0xtok_fill"
        assert extra["order_id"] == "0xvenue_fill_1"
        assert extra["pair_id"] == "pair_fill_1"
        assert "outcome" not in extra
        assert "market_title" not in extra

    def test_fill_extra_takes_outcome_and_title_from_meta(self):
        fill, order = _fill_and_order()
        extra = fill_extra(
            fill, order,
            {"outcome": "UP", "title": "Brazil election", "slug": "brazil"},
        )
        assert extra["outcome"] == "UP"
        assert extra["market_title"] == "Brazil election"

    def test_fill_extra_ignores_unknown_outcome(self):
        fill, order = _fill_and_order()
        extra = fill_extra(fill, order, {"outcome": "YES"})
        assert "outcome" not in extra

    def test_fill_observer_emits_fill_recorded(self):
        fill, order = _fill_and_order()
        calls = []

        def fake_emit(**kw):
            calls.append(kw)

        observer = make_fill_observer(
            fake_emit, service="query", phase="reconciling",
            meta_lookup=lambda cid: {"slug": "brazil-election", "title": "Brazil"},
        )
        observer(fill, order)
        assert len(calls) == 1
        call = calls[0]
        assert call["action"] == "fill_recorded"
        assert call["service"] == "query"
        assert call["phase"] == "reconciling"
        assert call["market_slug"] == "brazil-election"
        assert call["extra"]["size"] == 3.0
        assert call["extra"]["price"] == 0.48
        assert call["extra"]["side"] == "BUY"

    def test_fill_observer_meta_lookup_failure_still_emits(self):
        fill, order = _fill_and_order()
        calls = []

        def fake_emit(**kw):
            calls.append(kw)

        def boom(cid):
            raise RuntimeError("feed unreadable")

        observer = make_fill_observer(
            fake_emit, service="query", phase="reconciling", meta_lookup=boom)
        observer(fill, order)
        assert len(calls) == 1
        assert calls[0]["action"] == "fill_recorded"
        assert "market_title" not in calls[0]["extra"]

    def test_fill_observer_never_raises(self):
        fill, order = _fill_and_order()

        def boom(**kw):
            raise RuntimeError("ring down")

        observer = make_fill_observer(boom, service="query", phase="reconciling")
        observer(fill, order)  # must not raise

    def test_fill_recorded_passes_through_ring_and_writes_no_intent_row(self, tmp_path):
        from core_brain import cycle_stream as cycle_stream_module

        ring = tmp_path / "cycle_events.jsonl"
        db = tmp_path / "live.db"

        def emit_fn(**kw):
            cycle_stream_module.emit(
                kw.get("cycle", 0), kw.get("phase", ""), kw.get("action", ""),
                service=kw.get("service", "query"),
                market_slug=kw.get("market_slug", ""),
                extra=kw.get("extra"),
                ring_path=ring, db_path=db,
            )

        fill, order = _fill_and_order()
        observer = make_fill_observer(emit_fn, service="query", phase="reconciling")
        observer(fill, order)

        events = read_ring(ring_path=ring, tail=10)
        assert len(events) == 1
        assert events[0]["action"] == "fill_recorded"
        assert events[0]["extra"]["size"] == 3.0
        assert events[0]["extra"]["price"] == 0.48
        with contextlib.closing(sqlite3.connect(str(db))) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='cycle_intent'").fetchall()
        assert tables == []


class TestLifecycleTelemetry:
    def test_allow_list_copies_known_keys_and_drops_response(self):
        result = {
            "action": "exited", "pair_id": "pair_e1", "condition_id": "0xcond_e",
            "token_id": "0xtok_e", "side": "UP", "size": 4.0,
            "requested_size": 4.0, "fill_price": 0.44, "min_price": 0.43,
            "route": "hard_stop", "lifecycle_state": "HARD_STOP",
            "settlement_reason": "grace expired",
            "response": {"venue": "SOLD"}, "positions_checked": True,
        }
        extra = lifecycle_extra(result)
        assert extra["pair_id"] == "pair_e1"
        assert extra["outcome"] == "UP"
        assert extra["venue_side"] == "SELL"
        assert extra["fill_price"] == 0.44
        assert "response" not in extra
        assert "positions_checked" not in extra
        assert "side" not in extra

    def test_completed_maps_to_buy_and_ignores_unknown_side(self):
        extra = lifecycle_extra({"action": "completed", "side": "YES", "size": 2.0})
        assert extra["venue_side"] == "BUY"
        assert "outcome" not in extra

    def test_wait_actions_carry_no_venue_side(self):
        extra = lifecycle_extra({"action": "patient_wait", "pair_id": "p"})
        assert "venue_side" not in extra
        assert extra["pair_id"] == "p"

    def test_non_dict_returns_empty(self):
        assert lifecycle_extra(None) == {}
        assert lifecycle_extra("exited") == {}


class TestRelayerTelemetry:
    def test_size_only_when_measured(self):
        assert relayer_extra(condition_id="0xc") == {"condition_id": "0xc"}
        assert relayer_extra(condition_id="0xc", size=5.0)["size"] == 5.0

    def test_hash_and_id_stay_separate(self):
        extra = relayer_extra(
            condition_id="0xc", relayer_state="STATE_EXECUTED",
            transaction_hash="0xhash", transaction_id="relay-1",
        )
        assert extra["transaction_hash"] == "0xhash"
        assert extra["transaction_id"] == "relay-1"
        assert extra["relayer_state"] == "STATE_EXECUTED"

    def test_same_hash_and_id_dedupes(self):
        extra = relayer_extra(condition_id="0xc", transaction_hash="0xabc", transaction_id="0xabc")
        assert extra["transaction_hash"] == "0xabc"
        assert "transaction_id" not in extra
