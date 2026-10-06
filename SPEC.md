# SPEC: Issue #365 — Prune stale local stores and close the two audit gaps

## Goal
Run the existing storage-retention tooling against the live `data/`, `runtime/`
and `reports/` folders to reclaim stale rehearsal databases, runtime state and
generated reports, and close the two gaps that let stale files survive the audit
(cwd-dependent scan root, top-level-only runtime sweep) — without touching the
production registry.

## Acceptance Criteria
- [ ] `audit_storage()` resolves its base directory from the repository root
      constant (`LIVE_ROOT`), not `Path.cwd()`; running the CLI from `scripts/`
      and from the repo root produce the same audit (modulo documented
      `PYTHONPATH` launch note).
- [ ] The runtime sweep classifies files inside nested per-run folders
      (`runtime/runNNN/...` and legacy `run/` equivalents) per-leaf with each
      leaf's own mtime; a stale nested database is reported reclaimable;
      directories themselves are never deletion targets.
- [ ] `data/orders.db` (+ `-wal`/`-shm`) and `data/price_tape.db` remain
      non-deletable; `assert_not_protected_store()` still raises for them, and
      nested-runtime scanning plus `prune_storage()` deletion-time refusals
      cannot bypass that protection (including via forged items, `rmtree`
      descendants, or WAL/SHM siblings).
- [ ] New tests in `tests/test_data_retention.py` cover cwd-independence and
      nested runtime scanning; each fails before its change and passes after.
- [ ] A dry-run prune on the operator tree is reviewed, then executed by the
      operator (`--no-dry-run` is operator-gated, never agent-run), and
      `docs/data_inventory.md` is regenerated from the resulting audit.
- [ ] `python -m pytest -q tests/test_data_retention.py` passes.

## Scope
### In scope
- Anchoring the audit at the repository root via existing `LIVE_ROOT`.
- Recursive per-leaf runtime/`run/` sweep with the same protection precedence
  as `data/` files; symlink directories not followed.
- Deletion-time refusal hardening in `prune_storage()`.
- Focused regression tests + inventory regeneration.
### Out of scope
- Deleting/editing anything in `data/` except through `prune_storage()` policy.
- Quoting, sizing, or strategy code changes.
- Scheduled/automatic prune job (follow-up issue instead).
- Git history rewriting or `git gc`.
- New retention action set (`ARCHIVE` stays unused by the CLI), count-based
  rules, or retention-window changes (14-day default kept).
