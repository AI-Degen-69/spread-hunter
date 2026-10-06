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


def test_non_orphan_wal_is_kept_as_sibling(tmp_path: Path):
    """When the base .db exists, the .db-wal file is kept as sqlite_sibling."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    base_db = data_dir / "active_run.db"
    base_db.write_text("sqlite base")
    wal_file = data_dir / "active_run.db-wal"
    wal_file.write_text("sqlite wal")

    policy = DataRetentionPolicy(retention_days=14)
    items = audit_storage(base_dir=tmp_path, policy=policy)

    wal_item = next((it for it in items if it.path.name == wal_file.name), None)
    assert wal_item is not None
    assert wal_item.action == AuditAction.KEEP
    assert wal_item.family == "sqlite_sibling"


def test_pruning_parent_db_also_cleans_siblings(tmp_path: Path):
    """Deleting a stale .db also cleans up its -wal and -shm files if present."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    stale_db = data_dir / "stats_old.db"
    stale_db.write_text("db")
    stale_wal = data_dir / "stats_old.db-wal"
    stale_wal.write_text("wal")
    stale_shm = data_dir / "stats_old.db-shm"
    stale_shm.write_text("shm")

    os.utime(stale_db, (time.time() - 30 * 86400, time.time() - 30 * 86400))
    os.utime(stale_wal, (time.time() - 30 * 86400, time.time() - 30 * 86400))
    os.utime(stale_shm, (time.time() - 30 * 86400, time.time() - 30 * 86400))

    policy = DataRetentionPolicy(retention_days=14, preserve_newest_per_family=False)
    items = audit_storage(base_dir=tmp_path, policy=policy)

    result = prune_storage(items, dry_run=False)
    assert result.deleted_count >= 1
    assert not stale_db.exists()
    assert not stale_wal.exists()
    assert not stale_shm.exists()


def test_cli_main_dry_run_and_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    """Test CLI main() execution for dry-run and json modes."""
    import json

    from core_brain.data_retention import main

    # The CLI is anchored at the repo root: a foreign cwd must not leak
    # into the audit (T1, #365) — the sandbox below must stay invisible.
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "stats_sample.db").write_text("sample")

    exit_code = main(["--audit"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "SPREAD-HUNTER DATA STORAGE AUDIT" in captured.out
    assert "stats_sample.db" not in captured.out

    exit_code_json = main(["--json"])
    assert exit_code_json == 0
    captured_json = capsys.readouterr()
    parsed = json.loads(captured_json.out)
    assert isinstance(parsed, list)
    assert all("stats_sample.db" != row["name"] for row in parsed)

    exit_code_prune = main(["--prune", "--dry-run"])
    assert exit_code_prune == 0
    captured_prune = capsys.readouterr()
    assert "[DRY-RUN]" in captured_prune.out
    assert "Would delete" in captured_prune.out


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


def test_audit_defaults_to_repo_root_not_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`audit_storage()` with no base_dir must audit the repo root (T1, #365)."""
    from core_brain.runtime_paths import LIVE_ROOT

    monkeypatch.chdir(tmp_path)
    items = audit_storage()

    assert len(items) > 0
    assert all(str(LIVE_ROOT) in str(it.path) for it in items)


def test_cli_audit_identical_from_foreign_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """CLI audit from an unrelated cwd must match the repo-root audit (T1, #365)."""
    from core_brain.data_retention import main

    exit_root = main(["--audit"])
    assert exit_root == 0
    out_root = capsys.readouterr().out

    monkeypatch.chdir(tmp_path)
    exit_foreign = main(["--audit"])
    assert exit_foreign == 0
    out_foreign = capsys.readouterr().out

    assert out_root == out_foreign


def _make_stale(path: Path, days: float = 30.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("stale rehearsal data")
    old_time = time.time() - (days * 86400)
    os.utime(path, (old_time, old_time))
    return path


def test_nested_runtime_leaf_classified_per_leaf(tmp_path: Path):
    """Stale files inside runtime/runNNN/ must be reported reclaimable (T2, #365)."""
    stale = _make_stale(tmp_path / "runtime" / "run145" / "stale_rehearsal.db")
    fresh = tmp_path / "runtime" / "run145" / "fresh_rehearsal.db"
    fresh.parent.mkdir(parents=True, exist_ok=True)
    fresh.write_text("fresh")

    policy = DataRetentionPolicy(retention_days=14, preserve_newest_per_family=False)
    items = audit_storage(base_dir=tmp_path, policy=policy)

    stale_item = next(it for it in items if it.path == stale)
    assert stale_item.action == AuditAction.DELETE
    fresh_item = next(it for it in items if it.path == fresh)
    assert fresh_item.action == AuditAction.KEEP


def test_nested_runtime_dir_never_delete_target(tmp_path: Path):
    """Directories themselves must never be deletion targets (T2, #365)."""
    _make_stale(tmp_path / "runtime" / "run145" / "stale_rehearsal.db")
    # Age the per-run directory itself: the old aggregate sweep keyed the
    # verdict off the directory mtime.
    run_dir = tmp_path / "runtime" / "run145"
    old_time = time.time() - (30 * 86400)
    os.utime(run_dir, (old_time, old_time))

    policy = DataRetentionPolicy(retention_days=14, preserve_newest_per_family=False)
    items = audit_storage(base_dir=tmp_path, policy=policy)

    dir_deletes = [it for it in items if it.action == AuditAction.DELETE and it.path.is_dir()]
    assert dir_deletes == []


def test_nested_protected_registry_stays_protected(tmp_path: Path):
    """An orders.db nested under runtime/ must stay protected (T2, #365)."""
    nested_registry = _make_stale(tmp_path / "runtime" / "run145" / "orders.db")

    policy = DataRetentionPolicy(retention_days=14, preserve_newest_per_family=False)
    items = audit_storage(base_dir=tmp_path, policy=policy)

    match = next(it for it in items if it.path == nested_registry)
    assert match.action == AuditAction.PROTECTED


def test_prune_refuses_forged_price_tape_delete(tmp_path: Path):
    """A forged DELETE item for price_tape.db must raise, file preserved (T3, #365)."""

    tape = tmp_path / "price_tape.db"
    tape.write_text("tape")
    forged = AuditItem(
        path=tape,
        family="price_tape",
        action=AuditAction.DELETE,
        reason="forged",
        size_bytes=tape.stat().st_size,
        mtime=time.time(),
    )
    with pytest.raises(DataRetentionSafetyViolation):
        prune_storage([forged], dry_run=False)
    assert tape.exists()


def test_prune_refuses_dir_with_protected_descendant(tmp_path: Path):
    run_dir = tmp_path / "runtime" / "run145"
    run_dir.mkdir(parents=True)
    registry = run_dir / "orders.db"
    registry.write_text("registry")
    forged = AuditItem(
        path=run_dir,
        family="runtime_state",
        action=AuditAction.DELETE,
        reason="forged",
        size_bytes=registry.stat().st_size,
        mtime=time.time(),
    )
    with pytest.raises(DataRetentionSafetyViolation):
        prune_storage([forged], dry_run=False)
    assert registry.exists()
    assert run_dir.exists()


def test_runtime_walk_does_not_follow_dir_symlinks(tmp_path: Path):
    """A symlinked dir under runtime/ is kept, never descended (review fix, #365)."""
    outside = tmp_path / "outside"
    outside.mkdir()
    escape = outside / "stale_escape.db"
    escape.write_text("escape")
    old_time = time.time() - (30 * 86400)
    os.utime(escape, (old_time, old_time))

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    link = runtime_dir / "run999link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation needs privileges on this platform")

    policy = DataRetentionPolicy(retention_days=14, preserve_newest_per_family=False)
    items = audit_storage(base_dir=tmp_path, policy=policy)

    names = [it.path.name for it in items]
    assert "stale_escape.db" not in names
    link_items = [it for it in items if it.path == link]
    assert len(link_items) == 1
    assert link_items[0].action == AuditAction.KEEP

