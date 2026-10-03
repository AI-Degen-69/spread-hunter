# Plan: Issue #345 — Audit and prune stale local stores: 7.3 GB of dead shadow/legacy databases with no retention policy

Branch: `i345/audit-and-prune-stale-local-stores` | Issue: #345

## Intake & Context Notes
- **Adopted from issue**: 14-day retention default, dry-run by default, refusal of `data/orders.db*`, exclusion of `data/price_tape.db*`, newest store per family preserved.
- **Codebase seams verified**: `core_brain/shadow_guard.py` (registry refusal pattern), `core_brain/order_registry.py:DEFAULT_DB_PATH`, `scripts/spread-hunter-menu.ps1:Clear-RuntimeState` (legacy cleanup extension).
- **One Improvement Proposal**: Connect `scripts/spread-hunter-menu.ps1` cleanup routines directly to `core_brain.data_retention` so PowerShell and Python share identical safety guards and classification logic rather than diverging regexes.

## Task Breakdown

- [x] **Task 1 [Backend/Logic] [M]**: Core Data Retention & Guard Module
  - **Files**: `core_brain/data_retention.py`, `tests/test_data_retention.py`
  - **Description**:
    - Implement `DataRetentionPolicy` (configurable retention days per family, protected paths, exclusion lists, preserve newest count).
    - Implement `assert_not_protected_store()` using absolute path resolution and normalized checking mirroring `shadow_guard.py` to refuse `data/orders.db` and siblings.
    - Implement file scanners and classifiers: `classify_file()`, `audit_storage()`, and `prune_storage()`.
    - Support families: `rehearsal_stats` (`stats_*.db*`, `*shadow*`), `runtime_state` (`runtime/*`), `reports` (`reports/*_statistics_report*`, `reports/stat_*`), `archive` (`data/archive/*`), `orphan_wal_shm`.
    - Add CLI interface (`python -m core_brain.data_retention --audit`, `--prune`, `--dry-run`, `--days`, `--json`, `--output-inventory`).
  - **Depends on**: None
  - **Verification**: `python -m pytest -q tests/test_data_retention.py tests/test_production_registry_guard.py tests/test_shadow_guard.py`

- [x] **Task 2 [Docs/Reporting] [S]**: Architecture Documentation & Initial Audit Inventory
  - **Files**: `docs/agents/architecture.md`, `docs/data_inventory.md`
  - **Description**:
    - Update `docs/agents/architecture.md` with explicit storage retention rules, family lifecycles, and cleanup instructions.
    - Generate `docs/data_inventory.md` via `python -m core_brain.data_retention --audit --output-inventory docs/data_inventory.md` capturing the baseline audit breakdown and reclaimable space.
  - **Depends on**: Task 1
  - **Verification**: Verify `docs/data_inventory.md` is populated and `docs/agents/architecture.md` accurately documents the retention policy.

- [x] **Task 3 [Script/UX] [S]**: Operator PowerShell Menu Integration
  - **Files**: `scripts/spread-hunter-menu.ps1`
  - **Description**:
    - Update `Clear-RuntimeState` or add an interactive data retention option in `scripts/spread-hunter-menu.ps1`.
    - Invoke `python -m core_brain.data_retention --audit` for dry-run preview, display reclaimable size and summary, and prompt for confirmation before running `--prune --no-dry-run`.
  - **Depends on**: Task 1
  - **Verification**: Execute dry-run invocation and confirm `.\scripts\spread-hunter-menu.ps1 status` remains functional and live registry is untouched.
