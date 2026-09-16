"""Reading the ring's tail must not read the whole ring.

`read_ring` took the last 100 lines by loading every line first. The ring grows
for the life of a run -- 33.8 MB and 108,317 lines on a one-day rehearsal -- and
three endpoints (`/api/scan-state`, `/api/guardrail-alerts`,
`/api/guardrail-health`) call it on every 2s dashboard poll, so the dashboard
read ~100 MB per poll to look at 100 lines. A profiler caught four threads
inside `read_ring` at once; polls ran 5-11s, the page aborted them at its 5s
timeout, and the STALE banner flickered on and off.
"""

import builtins
import json

import pytest

from core_brain.cycle_stream import read_ring

# A ring far larger than any tail a caller asks for.
RING_LINES = 60_000
# The tail of a 100-line read is kilobytes; anything near the file size means
# the whole ring was pulled in again.
MAX_BYTES_READ = 2 * 1024 * 1024


@pytest.fixture()
def big_ring(tmp_path):
    ring = tmp_path / "cycle_events.jsonl"
    with open(ring, "w", encoding="utf-8") as fh:
        for i in range(RING_LINES):
            fh.write(json.dumps({"seq": i, "pad": "x" * 400}) + "\n")
    assert ring.stat().st_size > 20 * 1024 * 1024
    return ring


@pytest.fixture()
def count_bytes_read(monkeypatch):
    """Total bytes every `read`/`readlines` in this test hands back."""
    total = {"n": 0}
    real_open = builtins.open

    class CountingFile:
        def __init__(self, fh):
            self._fh = fh

        def __getattr__(self, name):
            return getattr(self._fh, name)

        def __enter__(self):
            self._fh.__enter__()
            return self

        def __exit__(self, *exc):
            return self._fh.__exit__(*exc)

        def __iter__(self):
            for line in self._fh:
                total["n"] += len(line)
                yield line

        def read(self, *a, **kw):
            data = self._fh.read(*a, **kw)
            total["n"] += len(data)
            return data

        def readlines(self, *a, **kw):
            lines = self._fh.readlines(*a, **kw)
            total["n"] += sum(len(x) for x in lines)
            return lines

    monkeypatch.setattr(builtins, "open",
                        lambda *a, **kw: CountingFile(real_open(*a, **kw)))
    return total


def test_reading_the_tail_does_not_read_the_whole_ring(big_ring, count_bytes_read):
    # Act
    events = read_ring(big_ring, tail=100)

    # Assert
    assert len(events) == 100
    assert count_bytes_read["n"] < MAX_BYTES_READ, (
        f"read {count_bytes_read['n'] / 1e6:.1f} MB to fetch 100 lines")


def test_the_tail_is_the_last_events_in_order(big_ring):
    # Act
    events = read_ring(big_ring, tail=100)

    # Assert
    assert [e["seq"] for e in events] == list(range(RING_LINES - 100, RING_LINES))


def test_a_tail_larger_than_the_ring_returns_every_event(tmp_path):
    # Arrange
    ring = tmp_path / "cycle_events.jsonl"
    ring.write_text("".join(json.dumps({"seq": i}) + "\n" for i in range(5)),
                    encoding="utf-8")

    # Act / Assert
    assert [e["seq"] for e in read_ring(ring, tail=100)] == [0, 1, 2, 3, 4]


def test_a_non_positive_tail_still_returns_every_event(tmp_path):
    # Arrange
    ring = tmp_path / "cycle_events.jsonl"
    ring.write_text("".join(json.dumps({"seq": i}) + "\n" for i in range(5)),
                    encoding="utf-8")

    # Act / Assert
    assert len(read_ring(ring, tail=0)) == 5


def test_a_blank_or_broken_line_is_skipped(tmp_path):
    # Arrange
    ring = tmp_path / "cycle_events.jsonl"
    ring.write_text('{"seq": 1}\n\nnot json\n{"seq": 2}\n', encoding="utf-8")

    # Act / Assert
    assert [e["seq"] for e in read_ring(ring, tail=100)] == [1, 2]


def test_a_missing_ring_reads_as_empty(tmp_path):
    assert read_ring(tmp_path / "nope.jsonl", tail=100) == []
