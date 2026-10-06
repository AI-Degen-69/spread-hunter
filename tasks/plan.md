Branch: i365/prune-stale-local-stores-in-data-runtime-and-repor | Issue: #365

# Plan — #365 Prune stale local stores, close the two audit gaps

- **Size tier:** Standard — 2–3 files of internal module changes
  (`core_brain/data_retention.py`, `tests/test_data_retention.py`,
  `docs/data_inventory.md` regen), one architectural decision per gap.
- **Task type:** Code (default) + Safety (registry/tape protection invariants).
- **Stack:** Python, pytest (`python -m pytest -q tests/test_data_retention.py`).
- **Quick-fix lane:** not offered — issue carries no `quick-fix` label
  (only `ready-for-agent`); gate skipped per protocol.
- **SPEC:** per-issue `SPEC.md` updated for #365 (goals, acceptance, edge
  cases, out-of-scope). **CONSTRAINTS:** `CONSTRAINTS.md` updated for #365.

## CodeRabbit plan intake (3-line note)
- **Adopted:** 3-task skeleton (root anchor → per-leaf runtime scan →
  prune-time refusals + inventory regen); `LIVE_ROOT` anchor;
  per-leaf nested items with own mtime; prune-time refusals
  (price-tape names, `rmtree` descendants, WAL/SHM siblings); focused
  fail-before/pass-after tests; operator-gated real prune.
- **Rejected:** empty-directory removal after prune (new deletion behaviour,
  out of scope); new launcher/`sys.path` bootstrapping (ticket scope is
  scan-root fix + `PYTHONPATH` note only).
- **[UNVERIFIED]:** exact nested fixture names under live `runtime/runNNN/`
  (inspect live tree during build); symlink frequency in the wild (handled
  conservatively regardless).

## Open questions — resolved from code + issue defaults (no operator ask)
1. **Retention window** (14 vs 7 vs count-based) → keep existing 14-day
   threshold; no count rule (issue default assumption; `DataRetentionPolicy`
   unchanged).
2. **Archiving vs deleting** (`AuditAction.ARCHIVE` unused by CLI) → no
   action-set change; execute existing delete policy (issue default).
3. **Whether `reports/` is pruned at all** → yes, reports are regenerable
   output and in scope (issue default).
4. **CLI from `scripts/`** → fix scan root only; document that launches from
   `scripts/` need `PYTHONPATH=<repo-root>` (matches ticket scope
   "reuse the existing root-path constant convention").

## Verified seams (grounded, no guesswork)
- `core_brain/data_retention.py:178-179` — `if base_dir is None:
  base_dir = Path.cwd()` (gap 1, verified).
- `core_brain/data_retention.py:341-378` — runtime/`run/` sweep uses
  top-level `iterdir()` only; dirs become single aggregate DELETE/KEEP items
  via `rglob` size sum + dir mtime (gap 2, verified).
- `core_brain/data_retention.py:425-466` — `prune_storage()` guards only via
  `assert_not_protected_store()` (orders.db names); dirs go to
  `shutil.rmtree()` with no per-descendant check; `.db` delete auto-cleans
  `-wal`/`-shm` siblings with no protection check (hardening surface,
  verified).
- `core_brain/runtime_paths.py:28` — `LIVE_ROOT =
  Path(__file__).resolve().parent.parent` (anchor constant exists; same
  pattern already used by `statistics_report.DEFAULT_REPORT_DIR =
  LIVE_ROOT / "reports"` and `RUNTIME_DIR`/`LEGACY_RUNTIME_DIR`).
- `data_retention.py` currently imports NO `LIVE_ROOT` (grep: zero hits) —
  the fix wires the existing constant, defines nothing new.
- Files that must NOT be modified: `data/orders.db` (+wal/shm), quoting/
  sizing/strategy code (`core_brain/trader_loop.py`, `scoring/`,
  `core_brain/config.py`), git history.

## Interface contracts (locked before build)
- `audit_storage(base_dir=None, ...)` — default changes `Path.cwd()` →
  `LIVE_ROOT`; explicit `base_dir` override preserved (test isolation +
  menu's explicit root keep working). Return type `list[AuditItem]`
  unchanged.
- `prune_storage(items, dry_run=True, force=False)` — signature unchanged;
  new deletion-time refusals raise `DataRetentionSafetyViolation`
  (a `BaseException`, deliberately uncatchable by `except Exception`).
- CLI flags unchanged (`--days` default 14, `--dry-run` default, `--prune`,
  `--audit`, `--json`, `--output-inventory`).
- `DataRetentionPolicy` fields unchanged (14d, protected/excluded/user
  patterns, `preserve_newest_per_family`).

## Improvement proposal (adopted by default — simplification)
Reuse the existing `core_brain/runtime_paths.LIVE_ROOT` constant as the audit
anchor instead of defining any new root constant in `data_retention.py`.
Evidence, verbatim: architecture doc — "Every writer names an absolute
destination anchored to the repo, never a path relative to the cwd";
`runtime_paths.py` — "LIVE_ROOT = Path(__file__).resolve().parent.parent";
`statistics_report.py` — "DEFAULT_REPORT_DIR = LIVE_ROOT / "reports"".
No scope expansion; folded into T1.

## Dependency graph (risk-first order)
- T1 (root anchor) — no dependencies; riskiest assumption (all paths
  downstream) → first.
- T2 (nested scan) — Depends on: T1 (same function, avoids conflicts; needs
  anchored root for the walk).
- T3 (prune hardening + inventory) — Depends on: T1, T2 (refusals cover
  items both sweeps produce; inventory regen reflects final audit).

## Tasks

### T1 — Anchor the audit at the repo root [Backend/Logic] (size M) [x]
- **Target files:** `core_brain/data_retention.py` (`audit_storage()` default
  + `main()` call path), `tests/test_data_retention.py`.
- **Build:** import `LIVE_ROOT` from `core_brain.runtime_paths`; default
  `base_dir` to `LIVE_ROOT` when `None`; keep explicit-`base_dir` behavior
  identical; add `PYTHONPATH` operator note for `scripts/` launches.
- **Helper skill:** `test-driven-development`.
- **Depends on:** — (first).
- **Verification:** new tests — (a) `audit_storage()` with `monkeypatch.
  chdir()` elsewhere still audits the repo-anchored tree (or identical
  tmp-root via explicit anchor semantics), (b) CLI `main(["--audit"])` after
  `chdir(scripts-dir)` reports the same item set as from root; each fails
  before the change; `python -m pytest -q tests/test_data_retention.py`
  green after.
- **Checkpoint:** after T1 — audit is cwd-independent, existing suite green.

### T2 — Recursive per-leaf nested runtime scan [Backend/Logic] (size M) [x]
- **Target files:** `core_brain/data_retention.py` (runtime/`run/` sweep),
  `tests/test_data_retention.py`.
- **Build:** replace aggregate directory items with per-leaf file items
  (recursive walk, each leaf own mtime + same protection precedence as
  `data/`); directories never DELETE targets; directory symlinks not
  followed (leaf → KEEP "symlink not followed"); empty dirs left in place.
- **Helper skill:** `test-driven-development`.
- **Depends on:** T1.
- **Verification:** new tests — (a) stale nested
  `runtime/run145/*.db` leaf reported DELETE/reclaimable, (b) fresh nested
  leaf KEEP, (c) nested `orders.db`-named leaf refused/protected, (d) dir
  itself never DELETE, (e) foreign-run exclusion unaffected; each fails
  before; full `tests/test_data_retention.py` green after.

### T3 — Harden prune refusals + regenerate inventory [Safety/Docs] (size S)
- **Target files:** `core_brain/data_retention.py` (`prune_storage()`),
  `docs/data_inventory.md` (regen output only).
- **Build:** deletion-time refusals in `prune_storage()` for price-tape
  filenames, `rmtree` descendants resolving to protected/excluded names,
  and WAL/SHM siblings of protected bases — all raise
  `DataRetentionSafetyViolation`; sibling auto-cleanup skips protected
  bases; then dry-run audit on the operator tree and regen
  `docs/data_inventory.md` via `--output-inventory`. **Real `--no-dry-run`
  prune is operator-gated and NOT run by the agent.**
- **Helper skill:** `test-driven-development`.
- **Depends on:** T1, T2.
- **Verification:** new tests — forged DELETE item for `price_tape.db` and
  for nested `orders.db` each raise on `prune_storage(dry_run=False)`;
  dry-run changes nothing on disk; targeted suite green; regenerated
  `docs/data_inventory.md` header timestamp is fresh and totals match a
  second audit run.
- **Checkpoint:** after T3 — plan complete, ready for Station III build.

## What stays out (rejection record)
- Empty-dir removal, new launcher/`sys.path` hacks, `ARCHIVE` action
  wiring, count-based retention, schedule/automation, `git gc` — all
  rejected per issue scope/CodeRabbit Choice 5; schedule goes to a
  follow-up issue if wanted.
