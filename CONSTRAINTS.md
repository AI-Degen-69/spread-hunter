# Constraints: Issue #345 — Audit and prune stale local stores (retention policy & safe cleanup)

## Quality & Tests
- **Zero regressions**: `tests/test_production_registry_guard.py`, `tests/test_shadow_guard.py`, `tests/test_single_registry_path.py` stay 100% green. Full-repo suite stays with GitHub CI on push.
- **Focused test suite**: `tests/test_data_retention.py` covering:
  - Production registry refusal (`data/orders.db`, `data/orders.db-wal`, `data/orders.db-shm`, relative paths, URI forms, case-insensitivity on Windows).
  - `data/price_tape.db` exclusion.
  - Dry-run mode verification (assert zero files deleted when `dry_run=True`).
  - Age threshold boundaries (< 14 days kept, > 14 days deleted).
  - Newest store preservation (the newest store in a family is always kept even if older than retention limit).
  - Orphan `-wal`/`-shm` detection and cleanup.
  - Process lock/safety checks.
- **Anti-cheat**: Strictly forbid skipping tests, deleting assertions, or suppressing linters. Tests must use `tmp_path` fixtures; no test deletes or modifies files outside `tmp_path`.
- **No new external dependencies**: Standard library only (`pathlib`, `os`, `shutil`, `argparse`, `dataclasses`, `datetime`, `json`).

## Behaviour Boundaries
- **Production Registry & Active Run Inviolability**: `data/orders.db` and its `-wal`/`-shm` files are NEVER deleted, moved, or overwritten under any flag or option. Active user-protected stores such as `01_shadow_12-09_00-58.db` (and anything matching `01_shadow*`) are explicitly protected from deletion.
- **Fail-Safe Deny-by-Default**: Any unresolved path, unidentifiable store, or path matching `orders.db` is rejected.
- **Dry-run by default**: Any execution without explicit `--no-dry-run` or confirmation prompt only prints the audit report.
- **Local uncommitted state only**: Never delete git-tracked files.
