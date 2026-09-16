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


def test_a_stale_snapshot_is_rebuilt(monkeypatch):
    # Arrange
    monkeypatch.setattr(server, "SNAPSHOT_TTL_SEC", 0.01, raising=False)
    builds: list[int] = []

    def build():
        builds.append(1)
        return len(builds)

    # Act
    first = server._cached_snapshot(("probe",), build)
    time.sleep(0.05)
    second = server._cached_snapshot(("probe",), build)

    # Assert
    assert (first, second) == (1, 2)


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
