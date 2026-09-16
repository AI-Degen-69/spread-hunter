"""Opening a registry must not need the SQLite write lock.

`OrderRegistry()` opens through `init_db`, which repairs quote/fill
attribution. That repair was an unconditional UPDATE, and a no-op UPDATE still
takes the write lock. The dashboard builds a registry on every `/api/kpi`
poll, so each poll queued behind the live loop's writes for up to
`BUSY_TIMEOUT_SEC` while holding the module lock, until the request threadpool
was full and the dashboard stopped answering anything at all.
"""

import sqlite3
import time

import pytest

import core_brain.order_registry as order_registry
from core_brain.order_registry import OrderRegistry

# Short enough that a regression shows up as a fast failure rather than a
# five-second stall, wide enough that a lock-free open is unambiguous.
BLOCKED_TIMEOUT_SEC = 2.0
LOCK_FREE_CEILING_SEC = 1.0


@pytest.fixture()
def write_locked_db(tmp_path, monkeypatch):
    """An initialised database with its write lock held by someone else."""
    monkeypatch.setattr(order_registry, "BUSY_TIMEOUT_SEC", BLOCKED_TIMEOUT_SEC)
    db = tmp_path / "orders.db"
    OrderRegistry(db_path=db)

    blocker = sqlite3.connect(db, timeout=0)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        yield db
    finally:
        blocker.rollback()
        blocker.close()


def test_opening_an_attributed_registry_does_not_wait_for_the_write_lock(write_locked_db):
    # Arrange — nothing to repair, and the write lock is held elsewhere.

    # Act
    started = time.monotonic()
    OrderRegistry(db_path=write_locked_db)
    elapsed = time.monotonic() - started

    # Assert
    assert elapsed < LOCK_FREE_CEILING_SEC, (
        f"opening a registry waited {elapsed:.2f}s on the write lock; "
        "it must not write when there is nothing to repair")


def test_repeated_opens_stay_lock_free(write_locked_db):
    # Arrange — the dashboard opens a registry on every poll.

    # Act
    started = time.monotonic()
    for _ in range(5):
        OrderRegistry(db_path=write_locked_db)
    elapsed = time.monotonic() - started

    # Assert
    assert elapsed < LOCK_FREE_CEILING_SEC, (
        f"five opens took {elapsed:.2f}s against a held write lock")
