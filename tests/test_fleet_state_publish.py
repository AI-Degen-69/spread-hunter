"""An empty fleet must publish `[]`, not a frozen snapshot of the last run.

`run/fleet_state.json` is written only after a COMPLETE sweep, and a fleet with
zero markets never completes one. Observed 2026-08-08: the universe emptied,
the fleet restarted with `markets: 0`, and the dashboard kept rendering the
six dead markets from the pre-restart file -- each row a frozen STALE/ERROR
from a book fetch that had been 404ing since the venue delisted the finished
events -- for the whole life of the new process.

The fix publishes the empty set on the empty-universe transition, so the page
clears the table instead of serving a file the fleet will never rewrite. These
tests pin the three properties that make the fix honest:

  * an empty fleet writes `[]`, and it overwrites whatever the previous run
    left on disk,
  * the write happens on the transition into emptiness, not once per idle
    second,
  * the dashboard renders an empty fleet as no markets rather than as an
    error or as the previous run's rows.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy import fleet  # noqa: E402
from strategy import stats  # noqa: E402

# The state reader binds `DB` and `RUN` at module load; this test patches them
# to a tmp dir so it never touches the live database or the real state file.
import server.fleet_dash as dash  # noqa: E402


def _stale_state_file(tmp_path) -> None:
    """A pre-restart snapshot of dead markets, exactly what the incident left."""
    stale = [{"cid": "0xdead", "title": "finished event", "_live": {
        "err": "book fetch: 404 Client Error: Not Found", "ts": 1.0}}]
    (tmp_path / "fleet_state.json").write_text(json.dumps(stale),
                                               encoding="utf-8")


def test_empty_fleet_publishes_an_empty_state_file(tmp_path, monkeypatch):
    """THE FIX. `_idle_empty` on the transition writes `[]`, not the old file."""
    monkeypatch.setattr(fleet, "RUN", tmp_path)
    _stale_state_file(tmp_path)

    logged = fleet._idle_empty([], fleet._Pulse(), False)

    assert logged is True
    out = json.loads((tmp_path / "fleet_state.json").read_text(encoding="utf-8"))
    assert out == []


def test_transition_into_emptiness_writes_once(tmp_path, monkeypatch):
    """One write per empty episode, not one per idle second.

    The warning and the publish ride the same transition. After the first
    `_idle_empty` the flag is True, and later calls must not touch the disk
    again -- the content is `[]` and cannot go stale while we wait.
    """
    writes = []
    monkeypatch.setattr(fleet, "RUN", tmp_path)
    monkeypatch.setattr(fleet, "_publish_state",
                        lambda states: writes.append(list(states)))

    logged = fleet._idle_empty([], fleet._Pulse(), False)
    assert logged is True
    # Steady-state idle iterations: still empty, still True, no more writes.
    assert fleet._idle_empty([], fleet._Pulse(), True) is True
    assert fleet._idle_empty([], fleet._Pulse(), True) is True

    assert len(writes) == 1
    assert writes[0] == []


def test_populated_fleet_publishes_its_own_specs(tmp_path, monkeypatch):
    """The populated path is unchanged: the sweep serialises the live set."""
    monkeypatch.setattr(fleet, "RUN", tmp_path)

    class _St:
        def __init__(self, spec):
            self.spec = spec

    fleet._publish_state([_St({"cid": "0xabc", "title": "live", "daily": 1.0})])

    out = json.loads((tmp_path / "fleet_state.json").read_text(encoding="utf-8"))
    assert out == [{"cid": "0xabc", "title": "live", "daily": 1.0}]


def test_write_failure_degrades_to_a_warning(tmp_path, monkeypatch):
    """A full disk must degrade the dashboard, not stop the loop.

    Same rule as every other sweep-end write: the try/except is the point.
    """
    monkeypatch.setattr(fleet, "RUN", tmp_path)

    def boom(*_args, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(fleet, "_atomic_write_json", boom)
    fleet._publish_state([])  # must not raise
    fleet._idle_empty([], fleet._Pulse(), False)  # must not raise


def test_dashboard_renders_an_empty_fleet_as_no_markets(tmp_path, monkeypatch):
    """End to end: what an empty fleet publishes is a cleared dashboard.

    Mirrors test_verified_ratio's DB-seeding pattern so `dash.fleet()` runs
    its full payload path rather than the missing-file early return.
    """
    monkeypatch.setenv("MAKER_DB", str(tmp_path / "dash.db"))

    monkeypatch.setattr(stats, "DB", tmp_path / "dash.db")
    monkeypatch.setattr(dash, "RUN", tmp_path)
    monkeypatch.setattr(fleet, "RUN", tmp_path)

    # The incident shape: a stale populated file on disk, then the fleet
    # restarts into an empty universe and publishes its true state.
    _stale_state_file(tmp_path)
    fleet._idle_empty([], fleet._Pulse(), False)

    payload = dash.fleet()
    assert "error" not in payload, payload
    assert payload["markets"] == []


def test_dashboard_still_renders_markets_after_an_empty_episode_recovers(
        tmp_path, monkeypatch):
    """The fix must not stick: markets returning are serialised again.

    After the empty publish, a sweep that finds markets writes the real set;
    the dashboard must render them, not stay on the cleared file.
    """
    monkeypatch.setattr(fleet, "RUN", tmp_path)
    monkeypatch.setenv("MAKER_DB", str(tmp_path / "dash.db"))
    monkeypatch.setattr(stats, "DB", tmp_path / "dash.db")
    monkeypatch.setattr(dash, "RUN", tmp_path)

    fleet._idle_empty([], fleet._Pulse(), False)

    class _St:
        def __init__(self, spec):
            self.spec = spec

    # The dashboard reads several spec fields directly (`min_size`, `daily`, ...)
    # so the fixture must carry the same fields a real spec carries, not a
    # minimal subset -- a sparse dict would KeyError for the wrong reason.
    spec = {"cid": "0xabc", "title": "back", "slug": "back-1",
            "daily": 2.0, "min_size": 5.0, "max_spread": 4.5, "tick": 0.01}
    fleet._publish_state([_St(spec)])
    payload = dash.fleet()
    # The dashboard's market rows carry `title`/`slug`, not the raw `cid`.
    assert [m["title"] for m in payload["markets"]] == ["back"]
