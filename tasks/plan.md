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
- **T2 trial relaunched (2026-10-02 ~05:02):** first attempt wrote an
  empty-session report (`reports/ladder_live_books_333_20261002_0041.json`,
  0 markets) and its process is gone. Fresh store per the fresh-db rule:
  `--db data/NN_shadow_ladder_333_retry.db`,
  `--out reports/ladder_live_books_333_20261002_0500.json`, `--minutes 240`,
  `--issue-tag 333`, detached (PID 28720). **Superseded — that process is gone.**
- **T2 third launch is the live one (2026-10-02 05:19:38, PID 25984):** operator-launched
  detached via `Start-Process` with
  `--db data/NN_shadow_ladder_333_mine.db`,
  `--out reports/ladder_live_books_333_mine.json`, `--minutes 240`, `--issue-tag 333`.
  Completes ~09:19. Confirmed alive at 05:31 by live `cycle_intent` writes (cycle 115+).
- **T3 SCOPE NARROWED (2026-10-02, operator decision) — placement only, no fills.**
  Measured at 20 min in: 27 orders placed, **0 fills**, all 27 cancelled `not_quoted`.
  Mechanism, not luck: `--open-window-sec` defaults to 30, so the ladder is live only
  30s of every 300s market (`core_brain/quotes.py:496`) — hence ~80 orders/hour, not
  thousands; and every order rested a mean 17.0s behind a mean **394** shares of queue
  (max 1332, only 7/23 under 100). `core_brain/shadow_fills.py:credit_fills` credits a
  fill only when tape volume at the exact price consumes that queue first, so a fill
  needs >395 shares traded at exactly 0.48 or 0.49 within ~17s. Projected 4h total:
  **~320 placements, ~0-3 fills.** The trial stays running (paper only, no signer, costs
  nothing) because the placement half is sound. What this run therefore **cannot**
  answer is the probe-verdict question — fill path, merge, exit_60 vs hold-to-close,
  PnL. T3 is written as a placement-and-censoring report and must say so explicitly;
  any fill/PnL comparison to the probe baseline would be fabricated.

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

### T3 — [Docs] Live-books placement & censoring report in `docs/runs/` (S; rescoped)
Commit `docs/runs/2026-10-0X-ladder-live-books-4h-trial.md`. **Scope is placement and
censoring only** (see the T3 SCOPE NARROWED reconciliation above):
- placement volume and rate, `edge_vs_mid` distribution, realized `queue_ahead` depth
- cancel reasons and rest-time distribution, and the 30s open-window duty cycle
- conservation result with embedded numbers (zero orphans is the checkable claim)
- an explicit, prominent statement that **fills were ~0 and the fill/exit half of the
  probe comparison was not measured** — pair rate, exit_60 vs hold-to-close, PnL and
  the shape-2 verdict impact are all out of reach for this run.
No go-live recommendation — separate issue.
Target files: `docs/runs/2026-10-0X-ladder-live-books-4h-trial.md` (+ JSON path recorded inside).
Depends on: T2. Verification: file in git with the three comparisons plus the conservation table.

## Checkpoints

- CHECKPOINT 1 (after T1): runner + conservation tests green offline; nothing has touched the network yet.
- [x] CHECKPOINT 2 (after T3): 4h JSON machine-local + write-up committed to `docs/runs/`; operator reads the numbers.
