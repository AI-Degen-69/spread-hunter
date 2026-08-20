"""Tests for engine.cycle_stream — the live cycle-telemetry ring (#51).

Covers the ring file (append, rotation, tail reads, concurrent appends) and
the cycle_intent table (decide inserts, submit updates, retention window).
All tests use tmp_path; nothing ever touches live/run/.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from engine.cycle_stream import emit, read_ring

REQUIRED_FIELDS = {
    "ts", "service", "cycle", "phase", "action",
    "market_slug", "reason", "latency_ms", "pid", "extra",
}


def _parse_lines(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _query_intent(db: Path, sql: str, params=()) -> list[tuple]:
    with sqlite3.connect(str(db)) as conn:
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
            emit(i, "reconciling", "reconcile_ok", service="engine",
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
            emit(i, "scanning", "rerank_done", service="engine", ring_path=ring)
        events = _parse_lines(ring)
        assert len(events) == 499  # rotated once at 501, then 502..600 appended
        for e in events:
            assert REQUIRED_FIELDS <= set(e)
            assert e["cycle"] >= 102

    def test_fleet_service_never_rotates(self, tmp_path):
        # Q3: only the engine process owns rotation. Fleet appends must never
        # trigger a rewrite, so a concurrent engine rotation cannot lose them.
        ring = tmp_path / "cycle_events.jsonl"
        for i in range(1, 511):
            emit(i, "quoting", "decide", service="fleet", ring_path=ring)
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

    def test_cycle_intent_retention_200(self, tmp_path):
        ring = tmp_path / "events.jsonl"
        db = tmp_path / "live.db"
        for i in range(1, 211):
            emit(i, "quoting", "decide", market_slug=f"m{i % 3}",
                 ring_path=ring, db_path=db)
        (count,) = _query_intent(db, "SELECT COUNT(*) FROM cycle_intent")[0]
        assert count == 200
