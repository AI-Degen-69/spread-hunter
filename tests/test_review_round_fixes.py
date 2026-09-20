"""Findings from the PR #238 review round, each proved before it was fixed.

Four behaviours, one file: they arrived together and they are all about a
number being taken from the wrong moment or the wrong source.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

from dashboard import server


# ── A background rebuild is stamped when it FINISHES ───────────────────────

def test_a_slow_background_rebuild_is_not_born_stale(monkeypatch):
    """`(time.monotonic(), build())` stamps the snapshot before building it.

    Python evaluates the tuple left to right, so a build that takes longer
    than SNAPSHOT_TTL_SEC landed in the cache already expired: the very next
    reader saw a stale entry and kicked off another rebuild, and the endpoint
    rebuilt continuously while never serving anything fresh.
    """
    # Arrange -- a build slower than the TTL.
    monkeypatch.setattr(server, "SNAPSHOT_TTL_SEC", 0.2)
    key = ("slow-refresh-probe",)
    server._snapshots.pop(key, None)
    builder = threading.Lock()
    builder.acquire()

    # Act
    server._refresh_snapshot(key, lambda: (time.sleep(0.5), "fresh")[1], builder)

    # Assert -- the entry must be younger than the TTL the instant it lands.
    stamped_at, value = server._snapshots[key]
    age = time.monotonic() - stamped_at
    assert value == "fresh"
    assert age < server.SNAPSHOT_TTL_SEC, (
        f"a snapshot that took 0.5s to build was born {age:.2f}s old"
    )
    server._snapshots.pop(key, None)


# ── The SSE replay reads the tail of the file it measured ──────────────────

def test_the_replay_tail_and_the_follow_offset_come_from_one_open(tmp_path,
                                                                  monkeypatch):
    """Capturing the offset, then reopening the path, straddles a rotation.

    The stream recorded `offset`/`file_key` from one handle and then called
    `tail_lines(ring_path, ...)`, which opens the path again. A rotation in
    that gap replayed the NEW ring's tail while following the OLD ring's end
    offset -- so the follow loop resumed mid-file and skipped everything up to
    the stale offset.
    """
    from core_brain.cycle_stream import tail_lines_fh

    ring = tmp_path / "ring.jsonl"
    ring.write_text("".join(f'{{"n": {i}}}\n' for i in range(50)), encoding="utf-8")

    # Arrange / Act -- one handle answers both questions.
    with open(ring, "rb") as fh:
        offset = fh.seek(0, os.SEEK_END)
        lines = tail_lines_fh(fh, 3)

    # Assert -- and the handle is still usable for the stat that follows it.
    assert offset == ring.stat().st_size
    assert [line.strip() for line in lines] == ['{"n": 47}', '{"n": 48}', '{"n": 49}']


def test_reading_the_tail_leaves_the_handle_open_for_its_stat(tmp_path):
    from core_brain.cycle_stream import tail_lines_fh

    ring = tmp_path / "ring.jsonl"
    ring.write_text('{"n": 1}\n', encoding="utf-8")
    with open(ring, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        tail_lines_fh(fh, 5)
        assert os.fstat(fh.fileno()).st_size == ring.stat().st_size


def test_an_empty_ring_replays_nothing(tmp_path):
    from core_brain.cycle_stream import tail_lines_fh

    ring = tmp_path / "ring.jsonl"
    ring.write_bytes(b"")
    with open(ring, "rb") as fh:
        assert tail_lines_fh(fh, 5) == []


# ── The page ages the snapshot against the CONFIGURED scan interval ────────

def test_the_status_payload_carries_the_configured_scan_interval(monkeypatch):
    """`scripts.filter_loop` reads SH_FILTER_INTERVAL_SEC; the page hardcoded 600.

    An operator who sets a 60s scan cadence had a snapshot 20 minutes old
    still reading LIVE, because both the top-nav pill and the "last scan"
    colour compared it against a 600s constant baked into app.js.
    """
    monkeypatch.setenv("SH_FILTER_INTERVAL_SEC", "60")
    assert server.resolve_scan_interval() == 60.0


def test_a_missing_or_junk_scan_interval_falls_back_to_the_default(monkeypatch):
    monkeypatch.delenv("SH_FILTER_INTERVAL_SEC", raising=False)
    assert server.resolve_scan_interval() == 600.0
    for junk in ("", "  ", "abc", "0", "-5"):
        monkeypatch.setenv("SH_FILTER_INTERVAL_SEC", junk)
        assert server.resolve_scan_interval() == 600.0, f"{junk!r} was accepted"


def test_the_page_reads_the_interval_instead_of_a_baked_in_600():
    js = (server._STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "scan_interval_sec" in js
    assert "const SCAN_INTERVAL_SEC = 600;" not in js, "hardcoded cycle left in renderScreener"


# ── A failed /api/kpi does not borrow the last snapshot's age ──────────────

def test_the_scan_pill_does_not_age_against_a_previous_polls_snapshot():
    """`kpi || lastKpi` made a dead /api/kpi look like a fresh snapshot.

    `snapshot_age` is frozen at whatever the last successful read said, so an
    hour-long KPI outage kept the pill green on an age that stopped moving.
    The pill has its own honest state for this: SCAN DEGRADED, amber.
    """
    js = (server._STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "renderMarketScanPill(status, kpi || lastKpi)" not in js
    assert "renderMarketScanPill(status, kpi)" in js


# ── Nothing falls between the replay and the follow offset ─────────────────

def test_an_append_during_the_tail_read_is_not_skipped(tmp_path):
    """Reading the tail first and the offset second opens a gap.

    `tail_lines_fh` reads up to the EOF it saw when it started. Taking the
    follow offset AFTER it means the offset is a LATER EOF, so any line
    appended while the tail was being read is in neither the replay nor the
    follow range -- a silently lost event. Taking the offset first makes the
    two ranges overlap instead: the worst case is one line delivered twice,
    which for a telemetry stream beats one line delivered never.
    """
    from dashboard.server import _ring_replay_and_offset

    ring = tmp_path / "ring.jsonl"
    ring.write_text("".join(f'{{"n": {i}}}\n' for i in range(20)), encoding="utf-8")
    appended = '{"n": 999}\n'

    class ApppendingStat:
        """Appends to the ring the moment the offset has been taken."""

        def __init__(self, path):
            self.path = path
            self.fired = False

        def __call__(self, fileno):
            if not self.fired:
                self.fired = True
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(appended)
            return os.fstat(fileno)

    with open(ring, "rb") as fh:
        replay, offset, _key = _ring_replay_and_offset(fh, 5,
                                                       stat_fn=ApppendingStat(ring))

    # The follow loop resumes at `offset`, so every byte past it is streamed.
    # The appended line must therefore sit at or after `offset` -- never
    # before it and outside the replay.
    tail_bytes = ring.stat().st_size - len(appended)
    assert offset <= tail_bytes, (
        f"offset {offset} skipped past the append at byte {tail_bytes}"
    )
    # And the overlap is the documented trade: the appended line rode along in
    # the replay AND sits past `offset`, so it is delivered twice rather than
    # dropped.
    assert [line.strip() for line in replay][-1] == '{"n": 999}'


def test_the_replay_offset_and_key_all_describe_one_file(tmp_path):
    from dashboard.server import _ring_replay_and_offset

    ring = tmp_path / "ring.jsonl"
    ring.write_text('{"n": 1}\n{"n": 2}\n', encoding="utf-8")
    with open(ring, "rb") as fh:
        replay, offset, key = _ring_replay_and_offset(fh, 10)

    assert [line.strip() for line in replay] == ['{"n": 1}', '{"n": 2}']
    assert offset == ring.stat().st_size
    assert key is not None
