"""Data retention policy, storage audit, and safe cleanup for spread-hunter.

Classifies local working stores, rehearsal shadow databases, runtime state,
and reports according to an auditable retention policy.

Strict safety invariants:
1. `data/orders.db` (and `-wal`/`-shm`) is THE production registry. It is
   strictly refused by `assert_not_protected_store()` and can never be deleted.
2. `data/price_tape.db` is excluded from deletion.
3. User-protected patterns (e.g. `01_shadow`) are preserved.
4. Dry-run is the default for all audit and prune operations.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import urllib.parse
from typing import Sequence

from core_brain.order_registry import DEFAULT_DB_PATH as PROD_DB_PATH


class DataRetentionSafetyViolation(BaseException):
    """Raised when data retention operations encounter or target protected files."""


class AuditAction(str, Enum):
    KEEP = "keep"
    ARCHIVE = "archive"
    DELETE = "delete"
    PROTECTED = "protected"


@dataclass(frozen=True)
class DataRetentionPolicy:
    """Configurable retention thresholds and protection rules."""

    retention_days: int = 14
    protected_filenames: tuple[str, ...] = ("orders.db", "orders.db-wal", "orders.db-shm")
    excluded_filenames: tuple[str, ...] = ("price_tape.db", "price_tape.db-wal", "price_tape.db-shm")
    user_protected_patterns: tuple[str, ...] = (r"(^|[/\\])01_shadow([_.-]|$)",)
    preserve_newest_per_family: bool = True


@dataclass
class AuditItem:
    """A single audited file or directory."""

    path: Path
    family: str
    action: AuditAction
    reason: str
    size_bytes: int
    mtime: float

    @property
    def age_days(self) -> float:
        return max(0.0, (time.time() - self.mtime) / 86400.0)

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024.0 * 1024.0)


@dataclass
class PruneResult:
    """Outcome of a prune operation."""

    dry_run: bool = True
    deleted_count: int = 0
    deleted_bytes: int = 0
    would_delete_count: int = 0
    would_delete_bytes: int = 0
    kept_count: int = 0
    kept_bytes: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def deleted_mb(self) -> float:
        return self.deleted_bytes / (1024.0 * 1024.0)

    @property
    def would_delete_mb(self) -> float:
        return self.would_delete_bytes / (1024.0 * 1024.0)


def _uri_to_path(raw: str) -> str | None:
    """Resolve a SQLite file: URI to a local file path."""
    parsed = urllib.parse.urlparse(raw)
    if parsed.netloc not in ("", "localhost"):
        return None
    query = urllib.parse.parse_qs(parsed.query)
    if "memory" in query.get("mode", ()):
        return None
    path = urllib.parse.unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return path or None


def is_protected_registry_path(path: Path | str) -> bool:
    """Return True if path points to data/orders.db or its -wal/-shm siblings."""
    if not isinstance(path, (str, os.PathLike)):
        return False
    raw = os.fspath(path)
    if not raw or raw == ":memory:":
        return False
    if raw.startswith("file:"):
        resolved_uri = _uri_to_path(raw)
        if resolved_uri is None:
            return False
        raw = resolved_uri

    name = Path(raw).name.lower()
    if name in ("orders.db", "orders.db-wal", "orders.db-shm"):
        return True

    candidates = [raw]
    if "\\" in raw:
        candidates.append(raw.replace("\\", "/"))

    try:
        prod_target = PROD_DB_PATH.resolve()
    except (OSError, ValueError, RuntimeError):
        return True

    for cand in candidates:
        try:
            cand_p = Path(cand).resolve()
            if cand_p == prod_target or cand_p.name.lower() in ("orders.db", "orders.db-wal", "orders.db-shm"):
                return True
            # Also check if base db is orders.db for -wal/-shm
            if cand_p.name.lower().startswith("orders.db"):
                return True
        except (OSError, ValueError, RuntimeError):
            return True
    return False


def assert_not_protected_store(path: Path | str) -> None:
    """Raise DataRetentionSafetyViolation if path is the production registry."""
    if is_protected_registry_path(path):
        raise DataRetentionSafetyViolation(
            f"Target {path!r} is the protected production registry {PROD_DB_PATH}. "
            "Deletion or modification of production registry files is strictly forbidden."
        )


def _matches_shadow_family(name: str) -> bool:
    """Identify if filename belongs to shadow or rehearsal statistics family."""
    lower = name.lower()
    if lower.startswith("stats_") and ".db" in lower:
        return True
    if "shadow" in lower and ".db" in lower:
        return True
    if lower.startswith("overnight_validation_") and ".db" in lower:
        return True
    if re.match(r"^\d{1,2}_shadow_", lower):
        return True
    return False


def audit_storage(
    base_dir: Path | None = None,
    policy: DataRetentionPolicy | None = None,
    now: float | None = None,
) -> list[AuditItem]:
    """Scan and audit storage items against the retention policy."""
    if base_dir is None:
        base_dir = Path.cwd()
    if policy is None:
        policy = DataRetentionPolicy()
    if now is None:
        now = time.time()

    items: list[AuditItem] = []
    data_dir = base_dir / "data"
    runtime_dir = base_dir / "runtime"
    legacy_run_dir = base_dir / "run"
    reports_dir = base_dir / "reports"

    # 1. Audit data/ directory
    if data_dir.exists() and data_dir.is_dir():
        for entry in data_dir.iterdir():
            if entry.is_file():
                name = entry.name
                stat = entry.stat()
                size = stat.st_size
                mtime = stat.st_mtime
                age_days = max(0.0, (now - mtime) / 86400.0)

                # Production registry
                if is_protected_registry_path(entry):
                    items.append(
                        AuditItem(
                            path=entry,
                            family="production_registry",
                            action=AuditAction.PROTECTED,
                            reason="Production registry (strictly protected)",
                            size_bytes=size,
                            mtime=mtime,
                        )
                    )
                    continue

                # Excluded price tape
                if name.lower() in [f.lower() for f in policy.excluded_filenames]:
                    items.append(
                        AuditItem(
                            path=entry,
                            family="price_tape",
                            action=AuditAction.KEEP,
                            reason="Price tape store (excluded from cleanup)",
                            size_bytes=size,
                            mtime=mtime,
                        )
                    )
                    continue

                # User-protected patterns (e.g. 01_shadow)
                if any(re.search(pat, name, re.IGNORECASE) for pat in policy.user_protected_patterns):
                    items.append(
                        AuditItem(
                            path=entry,
                            family="user_protected",
                            action=AuditAction.KEEP,
                            reason=f"User protected pattern match ({name})",
                            size_bytes=size,
                            mtime=mtime,
                        )
                    )
                    continue

                # Orphan WAL / SHM
                if name.endswith("-wal") or name.endswith("-shm"):
                    base_name = re.sub(r"-(wal|shm)$", "", name)
                    base_file = data_dir / base_name
                    if not base_file.exists():
                        items.append(
                            AuditItem(
                                path=entry,
                                family="orphan_wal_shm",
                                action=AuditAction.DELETE,
                                reason=f"Orphan SQLite WAL/SHM file (base {base_name} missing)",
                                size_bytes=size,
                                mtime=mtime,
                            )
                        )
                        continue

                # Rehearsal / shadow stats
                if _matches_shadow_family(name):
                    items.append(
                        AuditItem(
                            path=entry,
                            family="rehearsal_stats",
                            action=AuditAction.KEEP if age_days <= policy.retention_days else AuditAction.DELETE,
                            reason=(
                                f"Within {policy.retention_days}d retention window ({age_days:.1f}d old)"
                                if age_days <= policy.retention_days
                                else f"Exceeds {policy.retention_days}d retention window ({age_days:.1f}d old)"
                            ),
                            size_bytes=size,
                            mtime=mtime,
                        )
                    )
                    continue

                # Other generic file in data/
                items.append(
                    AuditItem(
                        path=entry,
                        family="data_other",
                        action=AuditAction.KEEP,
                        reason="Unrecognized data store (kept by default)",
                        size_bytes=size,
                        mtime=mtime,
                    )
                )

            elif entry.is_dir():
                if entry.name == "archive":
                    for arch_entry in entry.iterdir():
                        if arch_entry.is_file():
                            stat = arch_entry.stat()
                            age_days = max(0.0, (now - stat.st_mtime) / 86400.0)
                            items.append(
                                AuditItem(
                                    path=arch_entry,
                                    family="archive",
                                    action=AuditAction.KEEP if age_days <= policy.retention_days else AuditAction.DELETE,
                                    reason=(
                                        f"Within {policy.retention_days}d retention window ({age_days:.1f}d old)"
                                        if age_days <= policy.retention_days
                                        else f"Archived file exceeds {policy.retention_days}d retention window ({age_days:.1f}d old)"
                                    ),
                                    size_bytes=stat.st_size,
                                    mtime=stat.st_mtime,
                                )
                            )
                elif entry.name.startswith("shadow_stat_"):
                    # Directory of shadow stats
                    stat = entry.stat()
                    age_days = max(0.0, (now - stat.st_mtime) / 86400.0)
                    total_size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
                    items.append(
                        AuditItem(
                            path=entry,
                            family="rehearsal_stats",
                            action=AuditAction.KEEP if age_days <= policy.retention_days else AuditAction.DELETE,
                            reason=(
                                f"Shadow stat directory within {policy.retention_days}d ({age_days:.1f}d old)"
                                if age_days <= policy.retention_days
                                else f"Shadow stat directory exceeds {policy.retention_days}d ({age_days:.1f}d old)"
                            ),
                            size_bytes=total_size,
                            mtime=stat.st_mtime,
                        )
                    )

    # 2. Audit runtime/ and legacy run/
    for rdir, family_label in [(runtime_dir, "runtime_state"), (legacy_run_dir, "legacy_run")]:
        if rdir.exists() and rdir.is_dir():
            for entry in rdir.iterdir():
                stat = entry.stat()
                age_days = max(0.0, (now - stat.st_mtime) / 86400.0)
                if entry.is_file():
                    items.append(
                        AuditItem(
                            path=entry,
                            family=family_label,
                            action=AuditAction.KEEP if age_days <= policy.retention_days else AuditAction.DELETE,
                            reason=(
                                f"Runtime file within {policy.retention_days}d ({age_days:.1f}d old)"
                                if age_days <= policy.retention_days
                                else f"Runtime file exceeds {policy.retention_days}d ({age_days:.1f}d old)"
                            ),
                            size_bytes=stat.st_size,
                            mtime=stat.st_mtime,
                        )
                    )
                elif entry.is_dir():
                    total_size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
                    items.append(
                        AuditItem(
                            path=entry,
                            family=family_label,
                            action=AuditAction.KEEP if age_days <= policy.retention_days else AuditAction.DELETE,
                            reason=(
                                f"Runtime dir within {policy.retention_days}d ({age_days:.1f}d old)"
                                if age_days <= policy.retention_days
                                else f"Runtime dir exceeds {policy.retention_days}d ({age_days:.1f}d old)"
                            ),
                            size_bytes=total_size,
                            mtime=stat.st_mtime,
                        )
                    )

    # 3. Audit reports/
    if reports_dir.exists() and reports_dir.is_dir():
        for entry in reports_dir.iterdir():
            if entry.is_file() and ("statistics_report" in entry.name or entry.name.startswith("stat_")):
                stat = entry.stat()
                age_days = max(0.0, (now - stat.st_mtime) / 86400.0)
                items.append(
                    AuditItem(
                        path=entry,
                        family="reports",
                        action=AuditAction.KEEP if age_days <= policy.retention_days else AuditAction.DELETE,
                        reason=(
                            f"Report within {policy.retention_days}d ({age_days:.1f}d old)"
                            if age_days <= policy.retention_days
                            else f"Report exceeds {policy.retention_days}d ({age_days:.1f}d old)"
                        ),
                        size_bytes=stat.st_size,
                        mtime=stat.st_mtime,
                    )
                )

    # 4. Handle preserve_newest_per_family for families where everything would be deleted
    if policy.preserve_newest_per_family:
        family_groups: dict[str, list[AuditItem]] = {}
        for it in items:
            family_groups.setdefault(it.family, []).append(it)

        for fam, group in family_groups.items():
            if fam in ("rehearsal_stats", "archive", "reports"):
                # If all items in this family are marked DELETE, keep the newest one
                if all(it.action == AuditAction.DELETE for it in group) and len(group) > 0:
                    newest = max(group, key=lambda x: x.mtime)
                    # Mutate newest item to KEEP
                    idx = items.index(newest)
                    items[idx] = AuditItem(
                        path=newest.path,
                        family=newest.family,
                        action=AuditAction.KEEP,
                        reason=f"Newest store in family (preserved despite age > {policy.retention_days}d)",
                        size_bytes=newest.size_bytes,
                        mtime=newest.mtime,
                    )

    return sorted(items, key=lambda x: (x.family, x.path.name))


def prune_storage(
    audit_items: Sequence[AuditItem],
    dry_run: bool = True,
    force: bool = False,
) -> PruneResult:
    """Safely delete files marked with AuditAction.DELETE."""
    result = PruneResult(dry_run=dry_run)

    for item in audit_items:
        if item.action == AuditAction.DELETE:
            # Triple check production registry guard
            assert_not_protected_store(item.path)

            if dry_run:
                # In dry run, we record what would be deleted without touching disk
                result.would_delete_count += 1
                result.would_delete_bytes += item.size_bytes
            else:
                try:
                    if item.path.is_dir():
                        shutil.rmtree(item.path)
                    elif item.path.exists():
                        item.path.unlink()
                    result.deleted_count += 1
                    result.deleted_bytes += item.size_bytes
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(f"Failed to delete {item.path}: {exc}")
        else:
            result.kept_count += 1
            result.kept_bytes += item.size_bytes

    return result


def generate_inventory_markdown(audit_items: Sequence[AuditItem], policy: DataRetentionPolicy | None = None) -> str:
    """Generate clean Markdown documentation of audited files."""
    if policy is None:
        policy = DataRetentionPolicy()

    total_size = sum(it.size_bytes for it in audit_items)
    delete_items = [it for it in audit_items if it.action == AuditAction.DELETE]
    reclaimable_size = sum(it.size_bytes for it in delete_items)
    keep_items = [it for it in audit_items if it.action == AuditAction.KEEP]
    protected_items = [it for it in audit_items if it.action == AuditAction.PROTECTED]

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        "# Data Storage Inventory & Retention Audit",
        "",
        f"**Audit Timestamp**: `{now_str}`",
        f"**Retention Policy**: `{policy.retention_days}` days threshold (newest preserved per family)",
        "",
        "## Executive Summary",
        "",
        "| Metric | Count | Size (MB) | Size (GB) |",
        "| --- | --- | --- | --- |",
        f"| **Total Evaluated** | {len(audit_items)} | {total_size / (1024*1024):.2f} MB | {total_size / (1024*1024*1024):.3f} GB |",
        f"| **Reclaimable (Delete)** | {len(delete_items)} | {reclaimable_size / (1024*1024):.2f} MB | {reclaimable_size / (1024*1024*1024):.3f} GB |",
        f"| **Retained (Keep)** | {len(keep_items)} | {sum(i.size_bytes for i in keep_items) / (1024*1024):.2f} MB | {sum(i.size_bytes for i in keep_items) / (1024*1024*1024):.3f} GB |",
        f"| **Protected Registry** | {len(protected_items)} | {sum(i.size_bytes for i in protected_items) / (1024*1024):.2f} MB | {sum(i.size_bytes for i in protected_items) / (1024*1024*1024):.3f} GB |",
        "",
        "## Storage Classification by Family",
        "",
        "| Family | File / Path | Action | Size | Age (days) | Rationale |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    for it in audit_items:
        action_badge = {
            AuditAction.DELETE: "🔴 `DELETE`",
            AuditAction.KEEP: "🟢 `KEEP`",
            AuditAction.PROTECTED: "🛡️ `PROTECTED`",
            AuditAction.ARCHIVE: "📦 `ARCHIVE`",
        }.get(it.action, f"`{it.action.value}`")

        size_str = f"{it.size_mb:.2f} MB" if it.size_mb >= 1.0 else f"{it.size_bytes / 1024.0:.1f} KB"
        lines.append(
            f"| `{it.family}` | `{it.path.name}` | {action_badge} | {size_str} | {it.age_days:.1f}d | {it.reason} |"
        )

    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for data retention audit and prune."""
    parser = argparse.ArgumentParser(description="Audit and prune stale spread-hunter local data stores.")
    parser.add_argument("--audit", action="store_true", default=True, help="Run read-only storage audit (default)")
    parser.add_argument("--prune", action="store_true", help="Execute cleanup of eligible files")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=True, help="Simulate prune without deleting (default)")
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false", help="Perform real irreversible deletion")
    parser.add_argument("--days", type=int, default=14, help="Retention threshold in days (default: 14)")
    parser.add_argument("--output-inventory", type=str, default="", help="Write inventory markdown to file path")
    parser.add_argument("--json", dest="json_output", action="store_true", help="Print audit results as JSON")

    args = parser.parse_args(argv)

    policy = DataRetentionPolicy(
        retention_days=args.days,
        user_protected_patterns=("01_shadow",),
    )
    items = audit_storage(policy=policy)

    if args.output_inventory:
        out_path = Path(args.output_inventory)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        md = generate_inventory_markdown(items, policy=policy)
        out_path.write_text(md, encoding="utf-8")
        print(f"Inventory saved to {out_path}")

    if args.json_output:
        data = [
            {
                "path": str(it.path),
                "name": it.path.name,
                "family": it.family,
                "action": it.action.value,
                "reason": it.reason,
                "size_bytes": it.size_bytes,
                "size_mb": round(it.size_mb, 2),
                "age_days": round(it.age_days, 1),
            }
            for it in items
        ]
        print(json.dumps(data, indent=2))
        return 0

    total_bytes = sum(it.size_bytes for it in items)
    reclaim_bytes = sum(it.size_bytes for it in items if it.action == AuditAction.DELETE)

    print("================================================================================")
    print("SPREAD-HUNTER DATA STORAGE AUDIT")
    print(f"Retention Window: {policy.retention_days} days | Protected patterns: {policy.user_protected_patterns}")
    print(f"Total Files Audited: {len(items)} | Total Size: {total_bytes / (1024*1024*1024):.3f} GB")
    print(f"Reclaimable Files: {len([i for i in items if i.action == AuditAction.DELETE])} | Reclaimable Size: {reclaim_bytes / (1024*1024*1024):.3f} GB")
    print("================================================================================")

    if args.prune:
        if args.dry_run:
            print("[DRY-RUN] Simulating prune... (no files deleted)")
            res = prune_storage(items, dry_run=True)
            print(f"[DRY-RUN] Would delete {res.deleted_count} files ({res.deleted_mb:.2f} MB).")
        else:
            print("[LIVE] Executing prune of eligible stale stores...")
            res = prune_storage(items, dry_run=False)
            print(f"[LIVE] Pruned {res.deleted_count} files ({res.deleted_mb:.2f} MB).")
            if res.errors:
                print(f"[ERROR] Encountered {len(res.errors)} error(s):")
                for err in res.errors:
                    print(f"  - {err}")
                return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
