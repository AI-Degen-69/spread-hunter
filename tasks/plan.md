# Plan — Issue #333: Full 4-hour live-books ladder trial (paper only)

Branch: i333/full-4hour-live-books-ladder-trial | Issue: #333
Size: Small (one parameterized field + test + one committed write-up) · Type: Code + Research + Docs · Stack: Python + pytest
Execution order: tag fix + test → 4h live trial (last, live network) → committed write-up. Nothing spends, nothing writes to the venue.

## Session reconciliation (2026-10-01, Station II re-entry)

- **T1 is landed, not pending.** PR #334 merged `c0a8a9e`/`5a9b4a2` into `main` as
  `db08fba`; the work branch `i333/full-4hour-live-books-ladder-trial` is fully
  merged and stale on the remote. This branch is now the only place T3 lands.
- **T2 and T3 remain open — no code task remains for Station III to build.**
  T2 is a ~4h wall-clock live run (paper only, no signer, spends nothing) and T3
  is a write-up that must embed T2's numbers, so neither can be produced
  without the run happening first.
- **Probe baseline for T3 is recoverable and recorded here** (the probe JSON is
  gitignored and absent on this machine, so the numbers must be transcribed
  from [#323](https://github.com/AI-Degen-69/spread-hunter/issues/323)):
  ETH 5-min, 200 markets, both legs, resolutions stamped — **pair rate 75%**,
  verdict **shape 2 / exit_60 / strategy go**; hold-to-close mean −0.02 (CI
  crosses zero) vs exit_60 mean **+0.30 (CI 0.26–0.35)**. Caveats the write-up
  must carry: full fills assumed, 1-min tape fidelity, no depth, not
  risk-normalized.
- **T2 trial launched detached (2026-10-02 00:48):** running in background
  with `--minutes 240`, `--db data/NN_shadow_ladder_333.db`, `--out reports/ladder_live_books_333_run_4h.json`,
  `--issue-tag 333`. Running as background task `task-209`. Once it completes (~04:48 local time),
  T3 write-up can be generated and committed.

## CodeRabbit plan intake (read once; echo ignored)

- No `coderabbitai` comments on #333 (0 comments at plan time) — nothing to adopt, nothing to reject.
- No `[UNVERIFIED]` carried forward. All seams spot-checked against live code.

## Resolved from code (no operator time needed)

- Trial length → `--minutes 240` (~48 five-minute windows). Discovery via `discover_ladder_series` (`core_brain/markets.py:165`), reticked every 15s (`REDISCOVER_SEC`), so no window is skipped.
- Ladder budget → `--budget-usd 5` paper USD (1 share/rung at shape 2; `core_brain/quotes.py:516`). Trial invocation only; production defaults (`ladder_mode=False`, `ladder_budget_usd=0.0`) stay.
- Series scope → BTC+ETH 5-min (runner defaults); the probe verdict covers 5-min only.
- Report home → machine-local JSON stays under `reports/` (gitignored by design); the probe-vs-live write-up with embedded conservation numbers is committed to `docs/runs/` under the existing dated-run convention. The issue requires readability beyond the running machine, which `reports/` cannot satisfy.
- Seams verified verbatim: `run_trial(...)` (`scripts/ladder_live_books_trial.py:96`); hardcoded `"issue": 331` tags (`:183,:194`); `refuse_db` + populated-store guard (`:61,:69`); locked rehearsal schema (`scripts/ladder_shadow_rehearsal.py:build_report`); `make_ladder_decide` (`core_brain/ladder.py:37`); `route_quotes` switch (`core_brain/quotes.py:539-551`).
- Baseline at plan time: `tests/test_ladder_shadow.py` collects 4 tests (collect-only green; full focused run timed out at 30s locally, CI is the gate).

## Spec (embedded; Small work, no SPEC.md ceremony)

- Goal: run the #331 trial runner ~4h over live BTC/ETH 5-min books (paper only, no signer), then publish a probe-vs-live write-up where it can be read.
- Acceptance: (1) ~4h against live books, no signer, zero venue writes; (2) JSON + write-up readable beyond the running machine; (3) conservation holds, zero orphans (filled == merged + exited + settled + resting); (4) write-up answers whether live numbers change the probe verdict (shape 2 / exit_60).
- Out of scope: live orders, signer, screener, Dynamic Caps, dashboard UI, any go-live decision. MUST NOT touch: `data/orders.db`, screener modules, production `MakerConfig` defaults, dashboard code.

## Improvement proposal (adopted by default, simplification)

- Commit the probe-vs-live write-up (conservation numbers embedded) to `docs/runs/` instead of leaving everything under `reports/`, because `reports/` is gitignored and the issue explicitly requires the report to survive beyond the running machine. Dropped only on explicit operator rejection.

## Dependency graph

- T1 (issue-tag parameter + test) → CHECKPOINT 1 (focused tests green, offline) → T2 (4h live trial, live network, runs last) → T3 (committed write-up, embeds T2 numbers) → CHECKPOINT 2 (MD in git, JSON path recorded).
- T2 is wall-clock ~4h and cannot be shortened without breaking acceptance; T1/T3 are minutes each.

## Tasks

### T1 — [Backend/Logic] Parameterize the report issue tag (S)
Replace the two hardcoded `"issue": 331` tags in `run_trial` (empty-session + live-books branches) with an `issue` parameter (default 333, via `--issue-tag` CLI), so the #333 report does not misattribute itself to #331.
Target files: `scripts/ladder_live_books_trial.py`, one test in the `tests/test_ladder_shadow.py` family asserting the tag in both branches.
Depends on: —. Verification: new test RED without the change, GREEN after; focused ladder files green.

### T2 — [Research] Full ~4h live-books trial (M)
Run: `python scripts/ladder_live_books_trial.py --db data/NN_shadow_ladder_333.db --out reports/ladder_live_books_333_<stamp>.json --minutes 240` (paper only, no signer; ~4h, busy + quiet hours; fresh `--db` per trial — reruns on populated stores are refused).
Target files: `reports/ladder_live_books_333_<stamp>.json` (machine-local, gitignored by design).
Depends on: T1 (CHECKPOINT 1 green). Verification: operator hands-on — exit 0 with markets > 0, JSON conserves with zero orphans.

### T3 — [Docs] Probe-vs-live write-up in `docs/runs/` (S)
Commit `docs/runs/2026-10-0X-ladder-live-books-4h-trial.md`: live touch/fill rate vs probe 75% pair rate, oldest-first adherence, exit timing vs exit_60, conservation result with embedded numbers, verdict impact (shape 2 / exit_60). No go-live recommendation — separate issue.
Target files: `docs/runs/2026-10-0X-ladder-live-books-4h-trial.md` (+ JSON path recorded inside).
Depends on: T2. Verification: file in git with the three comparisons plus the conservation table.

## Checkpoints

- CHECKPOINT 1 (after T1): runner + conservation tests green offline; nothing has touched the network yet.
- CHECKPOINT 2 (after T3): 4h JSON machine-local + write-up committed to `docs/runs/`; operator reads the numbers.
