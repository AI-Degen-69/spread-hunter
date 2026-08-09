"""The liveness indicator must measure the LOOP, not the sweep.

The dashboard reported "Fleet heartbeat is stale (3m26s old). Displayed figures
are historical, not live." against a fleet that was trading normally. The cause
was the signal itself: the only thing the page could see was
`run/fleet_state.json`, written once per COMPLETE sweep, so the indicator was
really measuring sweep duration. A healthy 20-market sweep is 50-70s and one
slow venue takes it past the 120s threshold.

The fix separates the two. `strategy.fleet` stamps an in-memory pulse once per
market visit and a background thread publishes it every 10s. The tests below
pin the three properties that make that honest:

  * a slow sweep no longer reads as dead,
  * a WEDGED loop still reads as dead even though the writer thread is alive,
  * the loop's own clock is what gets published, never the writer's.
"""
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.fleet_dash import STALE_AFTER_SEC, _heartbeat, _pulse  # noqa: E402
from strategy import fleet  # noqa: E402

NOW = 1_780_000_000.0


def test_slow_sweep_is_not_reported_dead():
    """The bug, stated as a test.

    Sweep finished 3m26s ago (the observed figure) but the loop pulsed two
    seconds ago. Before the pulse existed this was STALE.
    """
    ts, age, stale, src = _heartbeat(
        NOW, live_ts=NOW - 206.0, state_mtime=NOW - 206.0,
        pulse={"loop_ts": NOW - 2.0})
    assert not stale
    assert src == "loop"
    assert age < 3.0
    assert ts == NOW - 2.0


def test_wedged_loop_is_still_reported_dead():
    """The property the fix must not trade away.

    The writer thread is alive and the file is fresh -- `written_ts` is two
    seconds old -- but the loop has not advanced in five minutes. A heartbeat
    that published the WRITER's clock would call this fleet healthy, which is
    precisely the failure the indicator exists to catch.
    """
    _, age, stale, _ = _heartbeat(
        NOW, live_ts=0.0, state_mtime=0.0,
        pulse={"loop_ts": NOW - 300.0, "written_ts": NOW - 2.0})
    assert stale
    assert age > STALE_AFTER_SEC


def test_no_pulse_falls_back_to_the_state_file():
    """A fleet too old to publish a pulse must keep working as before."""
    _, _, stale, src = _heartbeat(NOW, live_ts=NOW - 10.0,
                                  state_mtime=NOW - 10.0, pulse={})
    assert not stale
    assert src == "sweep"


def test_nothing_at_all_is_stale_not_healthy():
    _, age, stale, src = _heartbeat(NOW, 0.0, 0.0, {})
    assert stale and age is None and src == "none"


def test_pulse_publishes_the_loop_clock_not_the_writers(tmp_path, monkeypatch):
    """`loop_ts` is the loop's stamp; `written_ts` is the thread's."""
    monkeypatch.setattr(fleet, "PULSE_FILE", tmp_path / "fleet_pulse.json")
    p = fleet._Pulse()
    p.touch("BTC above 100k", 20)
    touched = p.snapshot()["loop_ts"]

    time.sleep(0.05)
    fleet._write_pulse(p)
    out = json.loads((tmp_path / "fleet_pulse.json").read_text(encoding="utf-8"))

    assert out["loop_ts"] == touched
    assert out["written_ts"] > out["loop_ts"]
    assert out["market"] == "BTC above 100k"
    assert out["markets"] == 20
    assert out["iterations"] == 1


def test_pulse_writer_thread_keeps_running_after_a_write_failure(monkeypatch):
    """A heartbeat that cannot be written must not kill the thread.

    The writer is the only thing publishing liveness; if one bad write ended
    it, the fleet would go permanently STALE for a transient disk error.
    """
    calls = []

    def boom(_pulse_obj):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk full")

    monkeypatch.setattr(fleet, "_write_pulse", boom)
    stop = threading.Event()
    t = threading.Thread(target=fleet._pulse_writer,
                         args=(fleet._Pulse(), stop, 0.01), daemon=True)
    t.start()
    time.sleep(0.1)
    stop.set()
    t.join(timeout=1.0)

    assert not t.is_alive()
    assert len(calls) > 1, "thread stopped after the first failed write"


def test_dashboard_reads_a_published_pulse(tmp_path, monkeypatch):
    """End to end: what fleet writes is what the dashboard parses."""
    import server.fleet_dash as dash

    monkeypatch.setattr(fleet, "PULSE_FILE", tmp_path / "fleet_pulse.json")
    monkeypatch.setattr(dash, "RUN", tmp_path)

    p = fleet._Pulse()
    p.touch("ETH above 5k", 12)
    p.sweep_done()
    fleet._write_pulse(p)

    got = _pulse()
    assert got["markets"] == 12
    assert got["sweeps"] == 1
    assert got["loop_ts"] > 0


def test_unreadable_pulse_degrades_to_empty(tmp_path, monkeypatch):
    """Garbage on disk must not raise out of the endpoint."""
    import server.fleet_dash as dash

    monkeypatch.setattr(dash, "RUN", tmp_path)
    (tmp_path / "fleet_pulse.json").write_text("{truncated", encoding="utf-8")
    assert _pulse() == {}


def test_idle_pass_does_not_grow_the_sweep_clock():
    """An empty universe must not report a phantom 49-minute sweep.

    Observed 2026-08-08: the fleet idling with zero markets was banner-text
    "Fleet is live, but a full sweep is taking 49m35s", because the pulse's
    in-progress clock measures from `_sweep_start`, which `sweep_done()` is
    the only thing that rolls -- and an empty fleet never completes a sweep.
    `_idle_empty` now calls `pulse.idle()`, so one pass over zero markets
    rolls the clock exactly as one pass over six markets does.
    """
    p = fleet._Pulse()
    p.touch("", 0)
    p.idle()

    got = p.snapshot()
    # The clock rolled: elapsed is seconds, not the 3005s since boot the bug
    # reported. Allow a generous bound for slow CI machines.
    assert got["sweep_elapsed"] < 5.0
    # And no sweep was recorded: an empty pass measured nothing.
    assert got["sweep_sec"] is None
    assert got["sweeps"] == 0


def test_idle_rolls_the_clock_even_after_boot_time_passes():
    """THE FIX, at the observed scale: elapsed stays small after an hour idle.

    Before the fix, `sweep_elapsed` was `now - boot`, so a fleet that booted
    at 14:30 and was still empty at 15:20 reported a 3005-second sweep. After
    `idle()` the clock measures from the last IDLE PASS, not from boot.
    """
    p = fleet._Pulse()
    p.touch("", 0)
    # Simulate the boot-time anchor the bug measured from: an hour ago.
    p._sweep_start = time.time() - 3600.0
    p.idle()

    got = p.snapshot()
    assert got["sweep_elapsed"] < 5.0
    assert got["sweep_sec"] is None


def test_empty_fleet_sweep_age_stays_small_on_the_dashboard(tmp_path, monkeypatch):
    """End to end: the idle pass publishes a small sweep_age, not 3005s.

    Pins the full chain the operator sees: `_idle_empty` -> pulse snapshot ->
    dashboard `_sweep_duration`. A stale, growing elapsed would re-trigger the
    "full sweep is taking 49m" banner on every page load.
    """
    import server.fleet_dash as dash

    monkeypatch.setattr(dash, "RUN", tmp_path)
    monkeypatch.setattr(fleet, "RUN", tmp_path)
    monkeypatch.setenv("MAKER_DB", str(tmp_path / "dash.db"))
    monkeypatch.setattr(dash, "DB", tmp_path / "dash.db")

    p = fleet._Pulse()
    fleet._idle_empty([], p, False)
    snapshot = p.snapshot()

    age = dash._sweep_duration(snapshot, time.time(), 0.0)
    assert age is not None
    assert age < 120.0, f"phantom sweep clock survived: {age:.1f}s"
    # And the fleet still reads alive -- idling is not staleness.
    assert not dash._heartbeat(
        time.time(), snapshot["loop_ts"], snapshot["loop_ts"], snapshot)[2]
