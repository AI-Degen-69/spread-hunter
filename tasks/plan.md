# Plan — #506 [CONFIGURE] tsc type-check gate fails for lack of module config

Branch: i506/tsc-type-check-gate-fails-for-lack-of-module-config | Issue: #506
Size: Small | Type: Docs + Code(test) [Docs]
Stack: Node (server.ts bridge) + pytest | Labels: ready-for-agent
Note: supersedes the #493 plan below in this file's history (kept in git); CodeRabbit plan reply: adopted in part, see intake note.

## CodeRabbit intake note
Adopted: nodenext flags on the command line (tsc ignores tsconfig.json when a file is named); keep history docs verbatim + resolution note; static guard test; no server.ts edits; new type errors beyond the module setting become follow-ups.
Rejected: editing `CONSTRAINTS.md:25` and `tasks/plan.md:96` — neither file contains the gate (verified: `tsc --noEmit` appears nowhere in either; the only repo hit is the historical N1 row in `docs/issues/478-noticed-but-not-touching.md:8`). Editing those lines would invent content.
[UNVERIFIED]: nothing — every seam below verified verbatim in the tree.

## Spec (embedded; Small tier, no SPEC.md)
Goal: future plans copy a bridge type-check gate that exits 0 instead of inheriting TS1343.
- The gate command is `npx tsc --noEmit --module nodenext --moduleResolution nodenext server.ts`.
- Why flags, not tsconfig: tsc ignores `tsconfig.json` when a file is named on the command line, so the module setting must ride on the invocation. `nodenext` follows Node's own rules and reads `"type": "module"` from `package.json` (verified present).
- Out of scope (issue text): fixing non-module type errors (follow-ups if real); `dashboard/server.py`, `data/orders.db`, `core_brain/*`; no `tsconfig.json` added; no runtime change to `server.ts`.
- Files that must NOT change: `server.ts`, `package.json`, CI workflow, `CONSTRAINTS.md` gate sections (no gate there to fix), historical N1 row text.

## Improvement proposal (adopted, simplification)
Do not touch per-issue `tasks/plan.md` as a gate source — it is scratch owned by whatever issue is active (currently `#493`, 71 lines, no tsc text). Evidence, verbatim: `# Plan — #493 [FIX] Sentry and offense loops...` vs CodeRabbit's `Update tasks/plan.md:96 with the same command`. The single shared source is `docs/agents/verifying.md`; the plan pins the gate there only.

## Interface contracts
No runtime interfaces. Test contract for `tests/test_tsc_gate_config.py`:
- `GATE_CMD = "npx tsc --noEmit --module nodenext --moduleResolution nodenext server.ts"` (single source in the test; docs must contain it verbatim).
- Asserts: gate text in `docs/agents/verifying.md`; bare `npx tsc --noEmit server.ts` NOT documented as a gate (regex excludes the flagged form); `package.json` `"type" == "module"`; `server.ts` contains `import.meta` (the reason flags exist).

## Dependency graph
T1 (prove commands) → T2 (docs edits; needs the proven command text) → T3 (guard test; needs the exact text T2 writes). Linear.

## Tasks
- [x] T1 (XS, [Docs]): re-run old + flagged tsc commands — DONE: old exits 2 (TS1343 @251,111), flagged exits 0 with 0 output bytes, no emitted files, record exit evidence. Depends on: none. Verify: `npx tsc --noEmit server.ts` shows TS1343 @251; flagged form silent exit 0, no emitted files (`git status --porcelain` clean of js/cjs artifacts). Skill: none.
- [x] T2 (S, [Docs]): add one bridge type-check line — DONE: gate line in verifying.md §1, N1 resolution note appended (row verbatim) + one-sentence why to `docs/agents/verifying.md` §1; append N1 resolution note under the N1 row in `docs/issues/478-noticed-but-not-touching.md` (row text untouched). Depends on: T1. Verify: `grep -F "$GATE_CMD" docs/agents/verifying.md`. Skill: `documentation-and-adrs`.
- [ ] T3 (S, [Code]): new `tests/test_tsc_gate_config.py` per contract above. Depends on: T2. Verify: `python -m pytest -q tests/test_tsc_gate_config.py` green; RED check: test fails on pre-change tree (verifying.md without the line). Skill: `test-driven-development`.
Checkpoint after T1: command evidence recorded. Checkpoint after T3: docs + guard green.

## Open questions resolved from code
- `needs-answers`: none on this issue. No operator questions.
- typescript/@types/node present in devDependencies and installed (`node_modules/.bin/tsc` exists) — no install, no lockfile change.
- No `tsconfig.json` in root (verified absent) — none added per scope.
