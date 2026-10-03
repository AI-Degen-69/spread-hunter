"""Tests for data retention policy, audit, and safe cleanup."""
from __future__ import annotations

import os
import time
from pathlib import Path
import pytest

from core_brain.data_retention import (
    DataRetentionPolicy,
    DataRetentionSafetyViolation,
    AuditAction,
    AuditItem,
    audit_storage,
    prune_storage,
    assert_not_protected_store,
    generate_inventory_markdown,
)
from core_brain.order_registry import DEFAULT_DB_PATH as PROD_DB_PATH


def test_production_registry_is_strictly_refused():
    """`data/orders.db` and its siblings must NEVER be eligible for deletion."""
    with pytest.raises(DataRetentionSafetyViolation):
        assert_not_protected_store(PROD_DB_PATH)

    with pytest.raises(DataRetentionSafetyViolation):
        assert_not_protected_store("data/orders.db")

    with pytest.raises(DataRetentionSafetyViolation):
        assert_not_protected_store(Path("data/orders.db-wal"))

    with pytest.raises(DataRetentionSafetyViolation):
        assert_not_protected_store("data/orders.db-shm")


def test_safety_violation_inherits_base_exception():
    """Ensure DataRetentionSafetyViolation is not swallowed by except Exception."""
    with pytest.raises(DataRetentionSafetyViolation):
        try:
            raise DataRetentionSafetyViolation("attempted to delete production registry")
        except Exception:  # noqa: BLE001
            pytest.fail("except Exception caught DataRetentionSafetyViolation")


def test_price_tape_is_excluded(tmp_path: Path):
    """`price_tape.db` must be classified as KEEP/EXCLUDED and not deleted."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    tape_file = data_dir / "price_tape.db"
    tape_file.write_text("dummy tape content")

    policy = DataRetentionPolicy(retention_days=14)
    items = audit_storage(base_dir=tmp_path, policy=policy)

    tape_items = [it for it in items if it.path.name == "price_tape.db"]
    assert len(tape_items) == 1
    assert tape_items[0].action == AuditAction.KEEP
    assert "price tape" in tape_items[0].reason.lower()


def test_user_protected_01_shadow_is_preserved(tmp_path: Path):
    """Files matching 01_shadow must be explicitly kept / protected from prune."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shadow_file = data_dir / "01_shadow_12-09_00-58.db"
    shadow_file.write_text("01 shadow rehearsal data")
    # Make it 30 days old
    old_time = time.time() - (30 * 86400)
    os.utime(shadow_file, (old_time, old_time))

    policy = DataRetentionPolicy(
        retention_days=14,
        user_protected_patterns=("01_shadow",),
    )
    items = audit_storage(base_dir=tmp_path, policy=policy)
    match = [it for it in items if it.path.name == "01_shadow_12-09_00-58.db"]
    assert len(match) == 1
    assert match[0].action == AuditAction.KEEP
    assert "user protected" in match[0].reason.lower() or "protected" in match[0].action.lower()


def test_retention_days_threshold(tmp_path: Path):
    """Old rehearsal stats (>14d) marked for delete; fresh (<14d) kept."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    fresh_file = data_dir / "stats_10-01_10-00_shadow-01.db"
    fresh_file.write_text("fresh")
    fresh_time = time.time() - (5 * 86400)
    os.utime(fresh_file, (fresh_time, fresh_time))

    old_file = data_dir / "stats_08-01_10-00_shadow-02.db"
    old_file.write_text("old")
    old_time = time.time() - (20 * 86400)
    os.utime(old_file, (old_time, old_time))

    policy = DataRetentionPolicy(retention_days=14, preserve_newest_per_family=False)
    items = audit_storage(base_dir=tmp_path, policy=policy)

    fresh_item = next(it for it in items if it.path.name == fresh_file.name)
    old_item = next(it for it in items if it.path.name == old_file.name)

    assert fresh_item.action == AuditAction.KEEP
    assert old_item.action == AuditAction.DELETE


def test_preserve_newest_per_family_even_if_old(tmp_path: Path):
    """If all rehearsal files are >14 days old, the newest one is preserved when configured."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    older = data_dir / "stats_07-01_shadow.db"
    older.write_text("older")
    os.utime(older, (time.time() - 30 * 86400, time.time() - 30 * 86400))

    newest_old = data_dir / "stats_08-01_shadow.db"
    newest_old.write_text("newest old")
    os.utime(newest_old, (time.time() - 20 * 86400, time.time() - 20 * 86400))

    policy = DataRetentionPolicy(retention_days=14, preserve_newest_per_family=True)
    items = audit_storage(base_dir=tmp_path, policy=policy)

    newest_item = next(it for it in items if it.path.name == newest_old.name)
    older_item = next(it for it in items if it.path.name == older.name)

    assert newest_item.action == AuditAction.KEEP
    assert "newest" in newest_item.reason.lower()
    assert older_item.action == AuditAction.DELETE


def test_orphan_wal_shm_detected_and_deleted(tmp_path: Path):
    """An orphan .db-wal without the base .db file should be deleted."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    orphan_wal = data_dir / "ghost_rehearsal.db-wal"
    orphan_wal.write_text("wal")

    policy = DataRetentionPolicy(retention_days=14)
    items = audit_storage(base_dir=tmp_path, policy=policy)

    orphan_item = next((it for it in items if it.path.name == orphan_wal.name), None)
    assert orphan_item is not None
    assert orphan_item.action == AuditAction.DELETE
    assert "orphan" in orphan_item.reason.lower()


def test_dry_run_deletes_nothing(tmp_path: Path):
    """prune_storage with dry_run=True removes 0 files."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    stale = data_dir / "stats_old.db"
    stale.write_text("data")
    os.utime(stale, (time.time() - 30 * 86400, time.time() - 30 * 86400))

    policy = DataRetentionPolicy(retention_days=14, preserve_newest_per_family=False)
    items = audit_storage(base_dir=tmp_path, policy=policy)

    result = prune_storage(items, dry_run=True)
    assert result.deleted_count == 0
    assert result.would_delete_count == 1
    assert stale.exists()


def test_live_prune_removes_eligible_files(tmp_path: Path):
    """prune_storage with dry_run=False deletes DELETE-marked files and keeps others."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    stale = data_dir / "stats_old.db"
    stale.write_text("data")
    os.utime(stale, (time.time() - 30 * 86400, time.time() - 30 * 86400))

    tape = data_dir / "price_tape.db"
    tape.write_text("tape")

    policy = DataRetentionPolicy(retention_days=14, preserve_newest_per_family=False)
    items = audit_storage(base_dir=tmp_path, policy=policy)

    result = prune_storage(items, dry_run=False)
    assert result.deleted_count >= 1
    assert not stale.exists()
    assert tape.exists()


def test_inventory_markdown_generation(tmp_path: Path):
    """generate_inventory_markdown produces formatted markdown tables."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    f = data_dir / "stats_test.db"
    f.write_text("abc")

    items = audit_storage(base_dir=tmp_path, policy=DataRetentionPolicy())
    md = generate_inventory_markdown(items)

    assert "# Data Storage Inventory & Retention Audit" in md
    assert "Reclaimable" in md
    assert "stats_test.db" in md
