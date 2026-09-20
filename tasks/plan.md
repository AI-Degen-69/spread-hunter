# Plan: Issue #254 — Speed up Windows pytest CI job (~10 min → under ~4 min)

Size: **Small** (one workflow file; approach is straightforward once the slowest test files are measured).
Task type: **Performance** (CI execution strategy; no product-code change).

## Context

The `tests` workflow (`.github/workflows/tests.yml:17-44`) runs the entire suite —
143 test files, 2193 tests, single process, no parallelism — on both
`ubuntu-latest` and `windows-latest`. Observed on PR #253: Ubuntu finishes in
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

### Task 1 — Measure: slowest test files + baseline [Perf]
- **Files:** none (read-only research).
- **Build:** run the full suite locally with durations
  (`python -m pytest -q --durations=20`) and record total wall time plus the
  top slowest test files. These become the slow-job candidate list for Task 2.
- **Skill:** performance-optimization
- **Verification:** durations table quoted in the PR body or issue comment; all
  143 files collected (2193 tests).

### Task 2 — Split the workflow into fast + slow parallel jobs [CI/Config]
- **Files:** `.github/workflows/tests.yml` (and `pytest.ini` only if a shared
  flag is needed).
- **Build:** two `pytest` jobs from the same matrix (`pytest-fast`,
  `pytest-slow`) on both OSes. The slow job runs exactly the Task 1 slow-file
  list; the fast job runs everything else (`--ignore=` per slow file). Both
  jobs together cover all 143 files with zero overlap and zero omission.
  No new packages, no test-file edits.
- **Skill:** incremental-implementation
- **Verification:** YAML parses (`python -c "import yaml,..."` or equivalent);
  a collection check proves fast + slow test counts sum to 2193 with no
  duplicates (`pytest --collect-only -q` per job file list).

### Task 3 — Verify on a real PR: green + faster [CI/Verify]
- **Files:** none (push + observe).
- **Build:** push the branch, open the PR, and compare both jobs' wall times
  against the ~9-14 min baseline. Post the timings as a PR comment.
- **Skill:** incremental-implementation
- **Verification:** `gh pr checks <n>` all green on both OSes;
  `gh run watch <run-id> --exit-status` green; Windows wall times recorded
  under ~4 min target.

## Improvement proposal (adopted by default)

Split into fast/slow parallel jobs instead of adding `pytest-xdist`: the issue
allows xdist only as an example, CONSTRAINTS bans new dependencies without
approval, and `tests.yml` already uses a job matrix — so a second job follows
the repo's own idiom with zero approval gates and zero new flakiness surface.
