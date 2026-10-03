# SPEC: Issue #345 — Audit and prune stale local stores (retention policy & safe cleanup)

## Goal
Establish an auditable, dry-run-first data retention policy and automated tool for `spread-hunter` local working stores. Classify ~7.3 GB of accumulated rehearsal databases, runtime logs, reports, and archive files into `keep` / `archive` / `delete`, protect the live production registry (`data/orders.db*`) and `data/price_tape.db*`, provide a PowerShell menu entry point, and commit an inventory under `docs/`.

## Acceptance Criteria
- [ ] A dry-run audit classifies every candidate file into `keep` / `archive` / `delete` with the specific reason, store family, file size in bytes, and prints total reclaimable disk space.
- [ ] Refusal guards strictly forbid touching or deleting `data/orders.db` and any `-wal`/`-shm` sibling, and refuse any store currently open or locked by a running process.
- [ ] `data/price_tape.db*` is explicitly excluded from deletion.
- [ ] Retention policy applies per family:
  - Rehearsal shadow stats (`data/stats_*.db*`, `data/*_shadow*`): default 14 days, with the newest store per family always retained.
  - `runtime/` execution dirs & logs: default 14 days.
  - `reports/*_statistics_report*`: default 14 days.
  - `data/archive/*`: default 14 days.
  - Stale orphan `-wal`/`-shm` files (whose parent `.db` is missing).
- [ ] A dry-run-by-default prune command is available via CLI (`python -m core_brain.data_retention`) and integrated into `scripts/spread-hunter-menu.ps1`.
- [ ] Initial audit inventory is documented and committed under `docs/data_inventory.md`.
- [ ] `docs/agents/architecture.md` is updated with the retention policy.
- [ ] Focused tests pass: `python -m pytest -q tests/test_data_retention.py`
- [ ] End-to-end verification: `.\scripts\spread-hunter-menu.ps1 status` remains functional and live registry is intact.

## Scope
### In scope
- New module `core_brain/data_retention.py` with `audit_storage()`, `prune_storage()`, `DataRetentionPolicy`, `AuditItem`, and CLI.
- New unit test suite `tests/test_data_retention.py`.
- Updating `scripts/spread-hunter-menu.ps1` to integrate with `core_brain.data_retention`.
- Updating `docs/agents/architecture.md`.
- Generating `docs/data_inventory.md`.

### Out of scope
- Modifying, moving, or deleting `data/orders.db` (the production registry).
- Trimming or deleting `data/price_tape.db`.
- Deleting any git-tracked files or source code.
- Database vacuuming / migration changes.
