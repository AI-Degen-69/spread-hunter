"""The closes-without-slugs repair: writer fix companion migration."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core_brain.shadow_guard import (
    ShadowSafetyViolation,
    assert_not_production_registry,
)
from scripts.backfill_close_slugs import backfill, main


def _store(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE quotes (ts REAL, condition_id TEXT,"
                 " market_slug TEXT, run_id TEXT)")
    conn.execute("CREATE TABLE closes (ts REAL, condition_id TEXT,"
                 " market_slug TEXT, run_id TEXT)")
    conn.executemany("INSERT INTO quotes VALUES (?,?,?,?)", [
        (100.0, "0xabc", "game-slug", "run-1"),
        (200.0, "0xabc", "game-slug", "run-1"),
    ])
    conn.executemany("INSERT INTO closes VALUES (?,?,?,?)", [
        (101.0, "0xabc", None, "run-1"),
        (102.0, "0xnostore", None, "run-1"),
    ])
    conn.commit()
    conn.close()


def test_backfill_repairs_slugs_from_quotes(tmp_path: Path):
    db = tmp_path / "tourney.db"
    _store(db)

    missing, still_null = backfill(db)

    assert missing == 2
    assert still_null == 1
    conn = sqlite3.connect(db)
    try:
        slug = conn.execute(
            "SELECT market_slug FROM closes WHERE condition_id = '0xabc'"
        ).fetchone()[0]
        assert slug == "game-slug"
        assert conn.execute(
            "SELECT market_slug FROM closes WHERE condition_id = '0xnostore'"
        ).fetchone()[0] is None
    finally:
        conn.close()


def test_backfill_refuses_the_production_registry():
    # The violation derives from BaseException, not Exception: a trader-loop
    # `except Exception` handler must never be able to swallow it.
    with pytest.raises(ShadowSafetyViolation):
        assert_not_production_registry("data/orders.db")
    assert main(["--db", "data/orders.db"]) == 2
