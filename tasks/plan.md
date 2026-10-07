# Plan — #401: Shadow tape recorded zero sellers at a level that filled live

Branch: i401/fix-shadow-tape-recorded-zero-sellers-at-a-level | Issue: #401

- Tier: **Standard** — ~5 files, internal module change, one architectural
  decision (fix the reader, keep the contract).
- Task type: **Debug + Code**.
- Stack: Python, pytest (`python -m pytest -q <file>` for focused runs).
- Skills: `debugging-and-error-recovery` (diagnose), `test-driven-development`
  (RED-first regressions), `incremental-implementation` (named helper).
- Spec: `SPEC.md`. Guardrails: `CONSTRAINTS.md`.
- CodeRabbit plan intake (read once; echo inside HTML comment ignored):
  - Adopted: 3-phase shape (diagnose → fix → regression); unconditional fix
    of the two proven defects (side-blind dedup key, single 500-row page);
    conditional maker-row attribution; contract + exact-price freeze;
    out-of-scope list; borrowed test assertions for T2/T4.
  - Rejected/trimmed: 10-task split merged into 4 atomic tasks (Rule 4 —
    over-split); "operator supplies the store" step dropped (see proposal).
  - `[UNVERIFIED]` at plan time: which of the 4 candidate causes (page
    overflow / dedup collision / sweep / mint) caused THIS incident; maker-row
    semantics of `takerOnly=false`. Both resolved from evidence in T1.
  - Drift found while verifying: incident store IS in checkout (plan said
    absent); `maker_orders` reader at `order_registry.py:2035-2036` (plan said
    "around 1998–2080" — close enough); `ladder_shadow_rehearsal.py` holds no
    `traded_fn` reference (T3 re-checks doubles that do).
- Skipped personae (recorded, not simulated): `code-explorer` — Standard tier,
  paths verified by direct read; `type-design-analyzer` — no new domain model,
  contract frozen by CONSTRAINTS.md.

## Interface lock

No new interfaces. Frozen: `traded_fn(condition_id, seen) -> {asset: {price:
volume}}`; `recent_trades(condition_id, seen, limit=500, taker_side="SELL")`
keeps its signature (pagination internal); exact-price 4dp matching kept.

## Improvement proposal (adopted — simplification, evidence-based)

> Read the incident store straight from this checkout instead of waiting for
> the operator to supply it.
> Evidence for: glob of this checkout returns
> `data/06_shadow_prudent_07-10_13-20.db`. Evidence against the old step:
> CodeRabbit wrote "The incident store is not available, so the source code
> alone cannot prove the cause." The file is available, so T1 opens it
> read-only in place. Adopted by default (simplification, no scope change).

## Tasks

### T1 [x] — Diagnose with read-only evidence [Debug] (S)
- Target files: `data/06_shadow_prudent_07-10_13-20.db` (read-only),
  `scripts/` (new diagnostic, only if kept — see below),
  `docs/runs/` (new note).
- Build: open the incident store with stdlib sqlite3 `file:...?mode=ro`;
  read DOWN token id, 0.26 `queue_marks` rows 10:20–10:37, order
  `pair-45eed98903c9`, run id; confirm timezone (epoch-sec vs epoch-ms).
  Classify the live fill's match type (operator fill lookup / on-chain hash;
  `maker_orders` reader at `order_registry.py:2035-2036`).
  Replay the public `/trades` tape (paginated, both `takerOnly` modes) and
  classify each row's drop point; record maker-row semantics verdict.
  Write `docs/runs/` note (format: `2026-09-29-shadow01-rescue-exits.md`):
  `recent_trades` is the reader, `recent_sell_flow` admission-only,
  supported cause, maker-semantics verdict. Keep the diagnostic script only
  if it stays small and read-only; else fold findings into the note and skip
  the file.
- Helper skill: `debugging-and-error-recovery`.
- Depends on: — (first).
- Verify: run note exists naming reader + drop point; `git status` shows no
  writes under `data/`; no rehearsal/trading command run.
- Sub-issue: #403 (T1), no blockers.

### T2 — Reader unit tests, RED first [Debug/Code] (S)
- Target files: `tests/test_recent_trades_taker_side.py` (extend
  `_FakeResponse` + `_SESSION.get` stub to answer by `offset`/`takerOnly`).
- Build: BUY/SELL identity collision (only SELL counts); missing-side row
  then repaired SELL (SELL counts); second page fetched on no-overlap;
  stop at all-seen page; page bound respected; repeat poll with same `seen`
  returns zero. All must FAIL on current code.
- Helper skill: `test-driven-development`.
- Depends on: T1.
- Verify: `python -m pytest -q tests/test_recent_trades_taker_side.py`
  shows the new tests failing, old tests passing.
- Checkpoint 1 (after T2): cause named in run note + reader tests red.
  One-line progress report, no approval pause.
- Sub-issue: #404 (T2), blocked by #403.

### T3 — Fix `recent_trades` [Code] (S)
- Target files: `core_brain/markets.py` only (`recent_trades`).
- Build: normalized `side` in the identity key; rejected rows keep their own
  `seen` keys; `taker_side=None` path preserved; docstring updated (new key;
  drop the stale "dropped when the window rolls" note). Bounded `offset`
  pagination after the `recent_sell_flow` pattern (short page / all-seen /
  bound stops; empty-`seen` bootstrap marks one page, credits nothing;
  failures still `{}`). Maker-row attribution ONLY if the T1 note confirms
  semantics; else record the model limit in the note. Touch nothing in
  `shadow_fills.py`, `shadow_exec.py`, `shadow_run.py`, `recent_sell_flow`.
- Helper skill: `incremental-implementation`.
- Depends on: T2.
- Verify: T2 suite green; `book_tape_recorder.py` + tape-using doubles
  (`ladder_shadow_rehearsal.py` et al.) re-checked against the contract.
- Sub-issue: #405 (T3), blocked by #404.

### T4 — End-to-end replay + nearby suites [Code] (M)
- Target files: `tests/test_shadow_run.py` (extend).
- Build: through the REAL `_default_traded_fn`, stub only
  `markets._SESSION.get` keyed by request params; reuse `FakeMarket`,
  `_cfg`, `_books`, registry fixture (`test_shadow_exec.py`) + queue-mark
  helpers (`test_queue_marks.py`). Prime history with no orders resting;
  DOWN 9 @ 0.26 behind a known queue; replay fresh 0.26 SELLs across two
  pages in the incident shape (old reader would miss). Assert
  `queue_marks.traded > 0`, queue drains by that volume, fill capped at 9
  when volume exceeds queue, repeat poll adds nothing. Boundary: 0.25
  SELLs leave 0.26 untouched. Mint/sweep maker case only if T3 shipped it.
- Helper skill: `test-driven-development`.
- Depends on: T3.
- Verify: `python -m pytest -q tests/test_shadow_run.py`
  `tests/test_recent_trades_taker_side.py` `tests/test_shadow_exec.py`
  `tests/test_shadow_fills.py` `tests/test_queue_marks.py`
  `tests/test_queue_clear_gate.py` — all green.
- Checkpoint 2 (after T4): replay green, no recount, boundaries hold.
- Sub-issue: #406 (T4), blocked by #405.

## Dependency graph

T1 → T2 → T3 → T4 (linear; each task needs the previous task's output:
cause verdict → failing tests → fix → replay proof).

## Rejections log

- (none yet — proposal above adopted; operator may reject before build.)
