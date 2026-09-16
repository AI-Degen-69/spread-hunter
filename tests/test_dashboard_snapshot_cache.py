"""One build per TTL, however many readers ask.

Every registry read in the process serialises on one lock, and a full
`/api/kpi` plus `/api/state` pass over a run-sized store costs more than a
second of it. Two open browser tabs asked for more work per second than the
lock could deliver: the request threadpool filled with readers waiting on each
other and the dashboard stopped answering anything, which the page reports as
`DATA STALE - dashboard lost contact with the engine`.
"""

import threading
import time

import pytest

import core_brain.kpi as kpi_module
import core_brain.registry_state as registry_state
import dashboard.server as server


@pytest.fixture(autouse=True)
def clean_snapshot_cache(monkeypatch):
    monkeypatch.setattr(server, "_snapshots", {}, raising=False)
    monkeypatch.setattr(server, "_snapshot_builders", {}, raising=False)


def test_concurrent_readers_share_one_build():
    # Arrange — a build slow enough that every reader arrives mid-flight.
    builds: list[int] = []
    started = threading.Barrier(8)

    def build():
        builds.append(1)
        time.sleep(0.2)
        return "snapshot"

    results: list[str] = []

    def reader():
        started.wait()
        results.append(server._cached_snapshot(("probe",), build))

    # Act
    threads = [threading.Thread(target=reader) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Assert
    assert len(builds) == 1, f"{len(builds)} builds for 8 concurrent readers"
    assert results == ["snapshot"] * 8


def test_a_fresh_snapshot_is_not_rebuilt(monkeypatch):
    # Arrange
    monkeypatch.setattr(server, "SNAPSHOT_TTL_SEC", 60.0, raising=False)
    builds: list[int] = []

    def build():
        builds.append(1)
        return len(builds)

    # Act
    first = server._cached_snapshot(("probe",), build)
    second = server._cached_snapshot(("probe",), build)

    # Assert
    assert (first, second) == (1, 1)
    assert len(builds) == 1


def test_state_endpoint_reads_the_store_once_per_ttl(monkeypatch, tmp_path):
    # Arrange
    calls: list[str] = []
    monkeypatch.setattr(registry_state, "summarize_state",
                        lambda db: calls.append(str(db)) or {"orders": []})
    monkeypatch.setattr(server, "_ACTIVE_DB_OVERRIDE", tmp_path / "orders.db")

    # Act
    for _ in range(5):
        server.get_state()

    # Assert
    assert len(calls) == 1, f"the store was read {len(calls)} times for 5 polls"


def test_kpi_endpoint_builds_the_report_once_per_ttl(monkeypatch, tmp_path):
    # Arrange
    calls: list[str] = []
    monkeypatch.setattr(kpi_module, "report",
                        lambda db_path, run_id=None: calls.append(str(db_path)) or {"ok": True})
    monkeypatch.setattr(server, "_ACTIVE_DB_OVERRIDE", tmp_path / "orders.db")

    # Act
    for _ in range(5):
        server.get_kpi()

    # Assert
    assert len(calls) == 1, f"the report was built {len(calls)} times for 5 polls"


def test_a_stale_snapshot_is_served_without_waiting(monkeypatch):
    """A reader never pays for a rebuild once any snapshot exists.

    A build costs several seconds of the registry lock -- longer than the TTL
    -- so making readers wait for a fresh one left a page load sitting for 40
    seconds behind requests an earlier load had abandoned.
    """
    # Arrange — one fast build to prime, then builds that take far too long.
    monkeypatch.setattr(server, "SNAPSHOT_TTL_SEC", 0.01)
    calls: list[int] = []

    def build():
        calls.append(1)
        if len(calls) > 1:
            time.sleep(1.0)
        return f"snapshot-{len(calls)}"

    assert server._cached_snapshot(("probe",), build) == "snapshot-1"
    time.sleep(0.05)

    # Act — the snapshot is stale now, and rebuilding it is slow.
    started = time.monotonic()
    served = server._cached_snapshot(("probe",), build)
    elapsed = time.monotonic() - started

    # Assert
    assert served == "snapshot-1", "a stale snapshot must be served as-is"
    assert elapsed < 0.2, f"a reader waited {elapsed:.2f}s for a background rebuild"


def test_the_background_refresh_replaces_the_stale_snapshot(monkeypatch):
    # Arrange
    monkeypatch.setattr(server, "SNAPSHOT_TTL_SEC", 0.01)
    calls: list[int] = []

    def build():
        calls.append(1)
        return f"snapshot-{len(calls)}"

    server._cached_snapshot(("probe",), build)
    time.sleep(0.05)

    # Act — this read triggers the refresh and returns the stale value.
    server._cached_snapshot(("probe",), build)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and len(calls) < 2:
        time.sleep(0.01)
    time.sleep(0.05)

    # Assert
    assert server._snapshots[("probe",)][1] == "snapshot-2"


def test_a_refresh_that_never_starts_does_not_freeze_the_key(monkeypatch):
    """A thread that fails to start must not leave the builder lock held.

    Nothing else releases it, so the key would serve one snapshot forever --
    silently, which is the failure mode this cache exists to avoid.
    """
    # Arrange
    monkeypatch.setattr(server, "SNAPSHOT_TTL_SEC", 0.01, raising=False)
    builds: list[int] = []

    def build():
        builds.append(1)
        return f"snapshot-{len(builds)}"

    server._cached_snapshot(("probe",), build)
    time.sleep(0.05)

    class Unstartable:
        def __init__(self, *a, **kw): pass
        def start(self): raise RuntimeError("can't start new thread")

    monkeypatch.setattr(server.threading, "Thread", Unstartable)

    # Act
    with pytest.raises(RuntimeError):
        server._cached_snapshot(("probe",), build)

    # Assert — the builder is free, so a later read can still refresh.
    builder = server._snapshot_builders[("probe",)]
    assert builder.acquire(blocking=False), "the builder lock was left held"
    builder.release()
