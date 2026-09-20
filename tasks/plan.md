# Plan: Issue #254 — Speed up Windows pytest CI job (~10 min → under ~4 min)

Size: **Small** (workflow + duration-balanced sharder; approach straightforward once slowest files are measured).
Task type: **Performance** (CI execution strategy; no product-code change).

## Context

On the `main` baseline at PR #253 the `tests` workflow (`.github/workflows/tests.yml:17-44`) ran the entire suite —
145 test files, 2193 tests, single process, no parallelism — on both
`ubuntu-latest` and `windows-latest`. Observed there: Ubuntu finishes in
~1-2 min, Windows takes ~9-14 min (9m28s, 12m13s, 14m18s on rerun), so the
Windows job dominates every babysit/merge wait. Same run also flaked once on the
timing-sensitive `stale` assertion (`tests/test_seed_preview_fixture.py:110`)
and passed on rerun — a symptom of the overloaded Windows runner, not a product
bug. `requirements-dev.txt` carries only `pytest` + `httpx`, and CONSTRAINTS
forbids new dependencies without approval, so the fix must come from job
structure, not new packages.

## Spec (embedded — Small, no SPEC.md ceremony)

Goal: cut Windows CI wall time to under ~4 min while the full suite still runs
green on both OSes.

Acceptance (from issue #254):
- [ ] Windows CI wall time measurably lower (target under ~4 min) on a representative PR run.
- [ ] Full suite still runs and passes on both Ubuntu and Windows (`gh pr checks` green).
- [ ] No test skipped, deleted, or weakened; no product-code change; no new dependency.

Out of scope (per issue): product code, trading logic, test assertions.

## Tasks

### Task 1 — Measure: per-file durations + baseline [Perf] [x]
- **Files:** none (read-only research).
- **Measured:** full suite locally 2192 passed, 1 skipped in ~156s; flat
  profile (slowest single test 3.7s, slowest file 7.9s) — no fast/slow split
  can reach the target, so Task 2 uses duration-balanced sharding instead.
  Per-file table saved to `scripts/ci/shard_durations.json` (145 files).
- **Skill:** performance-optimization
- **Verification:** done — junit XML aggregated per file; 2193 tests.

### Task 2 — Shard the workflow: ubuntu full + 4 windows shards [CI/Config]
- **Files:** `.github/workflows/tests.yml`, `scripts/ci/pytest_shard.py` (new),
  `scripts/ci/shard_durations.json` (new).
- **Build:** matrix `include`: ubuntu runs the full suite (`--shard all`),
  windows runs 4 duration-balanced shards from `pytest_shard.py`, which globs
  `tests/**/test_*.py` at runtime (new files always assigned somewhere) and
  greedy-partitions by the checked-in durations. `shell: bash` on the test
  step for identical word-splitting on both OSes; dropped the
  `pip install --upgrade pip` line (setup-python ships a modern pip).
  No new packages, no test-file edits.
- **Skill:** incremental-implementation
- **Verification:** YAML parses; collect-only per shard sums to exactly 2193
  (525+527+572+569) with zero overlap/omission; shard 2 executed locally:
  526 passed, 1 skipped in 38s.

### Task 3 — Verify on a real PR: green + faster [CI/Verify]
- **Files:** none (push + observe).
- **Build:** push the branch, open the PR, and compare both jobs' wall times
  against the ~9-14 min baseline. Post the timings as a PR comment.
- **Skill:** incremental-implementation
- **Verification:** `gh pr checks <n>` all green on both OSes;
  `gh run watch <run-id> --exit-status` green; Windows wall times recorded
  under ~4 min target.

## Improvement proposal (adopted by default)

Shard into duration-balanced jobs instead of adding `pytest-xdist`: the issue
allows xdist only as an example, CONSTRAINTS bans new dependencies without
approval, and `tests.yml` already uses a job matrix — so extra matrix entries
follow the repo's own idiom with zero approval gates and zero new flakiness
surface. Measurement forced one refinement: 4 balanced shards, not fast/slow,
because the profile is flat (slowest file 7.9s).
