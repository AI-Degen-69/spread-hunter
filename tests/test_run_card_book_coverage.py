"""Each rehearsal's run card says where its books came from.

A shadow rehearsal reads every order book through the unified `BookProvider`
seam (`core_brain/book_provider.py`): the live market socket first, REST when
the socket cannot serve a token. The socket only serves the tokens it has been
subscribed to, and everything else drops to REST one read at a time, silently.

So a rehearsal can look stream-backed while pricing most of its books off
REST, and the operator had no way to tell which from the dashboard -- the
counts were written into the run's heartbeat and never left it. Reading
`runtime/shadow_run_<run_id>.json` by hand was the only way to see them.

These tests pin the round trip: the counts the run recorded reach the run card,
on the row that recorded them, and a run that recorded nothing says nothing
rather than claiming zero coverage it never measured.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import core_brain.runtime_paths as runtime_paths
from core_brain.shadow_run import write_shadow_heartbeat
from dashboard import server as srv

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "dashboard" / "static"
APP_JS = _STATIC / "app.js"
STYLES_CSS = _STATIC / "styles.css"
HARNESS = Path(__file__).resolve().parent / "js" / "book_coverage_harness.cjs"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")

MIX = {"stream": 1204, "rest": 37, "fallback": {"not_connected": 12, "stale": 25}}


# ── backend: the counts reach the page ────────────────────────────────

def _wire_runtime(tmp_path, monkeypatch) -> Path:
    """A fake runtime dir wired into both the writer's and reader's resolvers."""
    runtime = tmp_path / "runtime"
    runtime.mkdir(exist_ok=True)
    monkeypatch.setattr(runtime_paths, "runtime_file",
                        lambda name, root=None: runtime / name)
    monkeypatch.setattr(srv, "resolve_runtime_file",
                        lambda name, root=None: runtime / name)
    return runtime


def _write_heartbeat(runtime: Path, name: str, db: Path, run_id: str,
                     mix=..., now: float | None = None) -> None:
    """A heartbeat file, with the mix the run recorded (or none at all)."""
    payload = {
        "pid": 4242,
        "run_id": run_id,
        "started_at": (now or time.time()) - 60.0,
        "minutes": 60.0,
        "interval": 5.0,
        "db_path": str(db),
        "heartbeat_ts": now or time.time(),
        "finished": False,
    }
    if mix is not ...:
        payload["book_source_mix"] = mix
    (runtime / name).write_text(json.dumps(payload), encoding="utf-8")


def test_the_run_reading_this_store_carries_its_book_mix(tmp_path, monkeypatch):
    # Arrange -- a rehearsal that read the socket and fell back sometimes.
    runtime = _wire_runtime(tmp_path, monkeypatch)
    db = tmp_path / "01_shadow.db"
    _write_heartbeat(runtime, "shadow_run_shadow-01.json", db, "shadow-01", MIX)
    now = time.time()

    # Act
    run = srv.read_shadow_run(str(db), now=now)

    # Assert -- the counts the run recorded, unchanged.
    assert run is not None
    assert run["book_source_mix"] == MIX


def test_the_run_listing_carries_the_mix_of_every_run(tmp_path, monkeypatch):
    # The switcher lists every rehearsal, so the listing is what the run card
    # reads: a run whose mix is missing here is a run the card cannot show.
    runtime = _wire_runtime(tmp_path, monkeypatch)
    db_a = tmp_path / "01_shadow_a.db"
    db_b = tmp_path / "02_shadow_b.db"
    now = time.time()
    _write_heartbeat(runtime, "shadow_run_shadow-01.json", db_a, "shadow-01", MIX, now)
    _write_heartbeat(runtime, "shadow_run_shadow-02.json", db_b, "shadow-02",
                     {"stream": 0, "rest": 480, "fallback": {}}, now)

    # Act
    runs = {r["run_id"]: r for r in srv.list_shadow_runs(str(db_a), now=now)}

    # Assert
    assert runs["shadow-01"]["book_source_mix"] == MIX
    assert runs["shadow-02"]["book_source_mix"] == {"stream": 0, "rest": 480,
                                                     "fallback": {}}


def test_a_heartbeat_without_the_field_reports_no_mix(tmp_path, monkeypatch):
    # A heartbeat written before the field existed says nothing about its
    # source. An empty dict would read as "0 stream, 0 REST" -- a claim about
    # a run that never made one.
    runtime = _wire_runtime(tmp_path, monkeypatch)
    db = tmp_path / "01_shadow.db"
    _write_heartbeat(runtime, "shadow_run_shadow-01.json", db, "shadow-01")
    now = time.time()

    run = srv.read_shadow_run(str(db), now=now)

    assert run is not None
    assert run["book_source_mix"] is None


def test_a_malformed_mix_is_never_surfaced(tmp_path, monkeypatch):
    # The field is ours, but the file is not: a hand-edited or truncated
    # heartbeat must not put junk in front of the operator.
    runtime = _wire_runtime(tmp_path, monkeypatch)
    db = tmp_path / "01_shadow.db"
    _write_heartbeat(runtime, "shadow_run_shadow-01.json", db, "shadow-01",
                     "stream: all of them")
    now = time.time()

    run = srv.read_shadow_run(str(db), now=now)

    assert run is not None
    assert run["book_source_mix"] is None


def test_the_producer_and_the_reader_agree_on_the_field(tmp_path, monkeypatch):
    # The writer's own name for the counts is the reader's name for them:
    # `write_shadow_heartbeat(book_sources=...)` -> `book_source_mix`.
    runtime = _wire_runtime(tmp_path, monkeypatch)
    db = tmp_path / "01_shadow.db"
    written = write_shadow_heartbeat(db_path=db, run_id="shadow-01",
                                     minutes=60.0, interval=5.0,
                                     started_at=time.time() - 60.0,
                                     book_sources=MIX)
    now = time.time()

    run = srv.read_shadow_run(str(db), now=now)

    assert written == runtime / "shadow_run_shadow-01.json"
    assert run is not None
    assert run["book_source_mix"] == MIX


# ── frontend: the run card shows it ───────────────────────────────────

@pytest.fixture(scope="module")
def card() -> dict:
    out = subprocess.run([shutil.which("node"), str(HARNESS), str(APP_JS)],
                         capture_output=True, text=True, check=True,
                         encoding="utf-8")
    return json.loads(out.stdout)


@requires_node
def test_the_run_card_shows_the_stream_and_rest_counts(card):
    row = card["rows"][0]
    assert row["found"] is True
    assert row["hasMixLine"] is True
    assert "1,204 stream" in row["text"]
    assert "37 REST" in row["text"]


@requires_node
def test_a_rest_only_run_reads_as_zero_stream(card):
    # The honest reading of a run that never reached the socket: zero stream
    # reads, and no implied coverage.
    row = card["rows"][1]
    assert row["hasMixLine"] is True
    assert "0 stream" in row["text"]
    assert "480 REST" in row["text"]


@requires_node
def test_a_run_with_no_recorded_mix_shows_no_coverage_line(card):
    # An old heartbeat records nothing, so the card claims nothing. It must
    # not read as zero coverage, which is a different claim.
    row = card["rows"][2]
    assert row["found"] is True
    assert row["hasMixLine"] is False
    assert row["text"] == ""
    assert "stream" not in row["text"]


@requires_node
def test_the_tooltip_names_why_the_rest_fallbacks_happened(card):
    # The counts say how much fell back; the reason says which failure it was.
    row = card["rows"][0]
    assert "not_connected 12" in row["title"]
    assert "stale 25" in row["title"]


@requires_node
def test_a_mix_that_recorded_no_reads_still_renders(card):
    # A run that read no books at all did record a mix. Zero is a reading.
    assert card["notes"]["zeroMix"]["text"] != ""
    assert "0 stream" in card["notes"]["zeroMix"]["text"]


@requires_node
def test_no_mix_at_all_renders_nothing(card):
    # The other half of the same rule: nothing recorded, nothing shown.
    assert card["notes"]["noMix"]["text"] == ""
    assert card["notes"]["junkMix"]["text"] == ""


@requires_node
def test_a_hand_edited_heartbeat_never_reaches_the_page(card):
    # The heartbeat file is not ours: a truncated or hand-edited one must not
    # put junk in front of the operator, and must not read as a measurement.
    assert card["notes"]["arrayMix"]["text"] == ""
    # A string fallback is not a breakdown of reasons: the counts still read,
    # and the tooltip claims no reasons it did not find.
    assert card["notes"]["stringFallback"]["text"] == "books: 5 stream · 1 REST"
    assert "(" not in card["notes"]["stringFallback"]["title"]
    # A negative count is not a measurement.
    assert card["notes"]["negativeMix"]["text"] == "books: 0 stream · 2 REST"


def test_the_coverage_line_stays_out_of_the_state_colour_vocabulary():
    # DESIGN.md: a saturated hue may only mean a state. The mix is a record of
    # what the run did, not a health signal, so it stays in the muted band.
    css = STYLES_CSS.read_text(encoding="utf-8")
    start = css.index(".db-run-mix {")
    rule = css[start:css.index("}", start)]

    assert "var(--text-muted)" in rule
    for hue in ("--signal", "--warn", "--loss", "--open",
                "--green-", "--amber-", "--red-", "--blue-"):
        assert hue not in rule
