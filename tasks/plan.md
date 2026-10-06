# Plan — Issue #390: hold resting orders through transient no-intent cycles

Branch: i390/hold-resting-orders-through-transient-no-intent-cy | Issue: #390
Size: Standard — 1–2 prod files + tests, one design decision (refusal taxonomy + grace rule shape).
Type: [Backend/Logic] + [Debug]. Stack: Python 3.12, pytest. Spec embedded below;
no root SPEC.md exists in this repo (prior spec lives in docs/archive/; same precedent as #386).

## Spec (embedded)
Goal: a placed pair is judged once, at placement. A cycle where `decide` runs but
refuses intents (wide book, pair-cost boundary, mid band) must HOLD resting orders —
no cancel, no submit — instead of wiping them with `not_quoted` and re-posting next cycle.
- `_visit_one` distinguishes visited-but-refused (`decide` ran, returned `[]` intents)
  from dropped-from-rotation (market absent from `current_markets` — the existing
  `_cancel_dropped_markets` path, unchanged, still cancels via `market_dropped`).
- Visited-but-refused with a TRANSIENT reason → hold: no cancel, no submit, result
  stays DECLINED with the refusal `why`, plus a `[HOLDING]` log line.
- Visited-but-refused with a TERMINAL reason → cancel now (current behavior): the
  settled/decided book must not be held. Terminal set (resolved from code, see below):
  decided market (`mid outside [0.20,0.80]`), `t_remaining` elapsed, `market exited`,
  unfunded/zero-allocation. Everything else is transient-with-grace.
- Grace/timeout: per-market consecutive-refused-cycle counter (in-memory in `run()`,
  keyed by condition id). Past `REFUSED_HOLD_GRACE_CYCLES` refused cycles the hold
  expires and the orders cancel via `not_quoted`. Counter resets on any quoted cycle
  or submit; a process restart resets counters (safe direction: holds, never wipes).
- Out of scope: placement gates themselves (`quotes.py` decision logic unchanged —
  the classifier only READS `why` strings), post-fill management, `orders.db`, live runs.

## CodeRabbit plan intake (3-line note)
- Adopted: nothing — the issue carries 0 comments, so there is no CodeRabbit plan.
- Rejected: nothing (no plan to reject).
- [UNVERIFIED]: nothing — every cited path/symbol below was read live in this session.

## Open questions (resolved from code — none asked of the operator)
- No `needs-answers` label and no Open-questions section. The one real question —
  which refusals are transient vs terminal — resolves from `quotes.py`: the decided-market
  block documents the cancel-on-empty-intents contract for settled books, while the
  wide-book / completable-cap / reward-window blocks describe flicker, not abandonment.
- `code-explorer` persona skipped (not needed): the blast radius is two fully-read
  files (`trader_loop.py` `plan_orders`/`_visit_one`/`run`, `quotes.py` refusal strings).
- `type-design-analyzer` persona (present on disk) applied to the contract below:
  enum over bool (empty intents currently means two things — visited-refused and
  dropped — and a bool cannot name the terminal case); grace counter owned by `run()`,
  not the `VenueSeam` (the seam is venue ports, not loop memory); threshold as a named
  constant. Illegal states unrepresentable: hold-vs-cancel is an explicit outcome, never
  inferred from `intents == []` inside `plan_orders`.

## Interface contracts (locked before logic)
- New `VisitOutcome` enum in `core_brain/trader_loop.py`: `QUOTED` / `REFUSED_TRANSIENT` /
  `REFUSED_TERMINAL`. `DROPPED` stays where it is (`_cancel_dropped_markets`, untouched).
- Pure helper `_classify_refusal(why: str)` maps decide's `why` to terminal/transient;
  unit-testable in isolation.
- `plan_orders` gains an explicit outcome parameter whose DEFAULT preserves legacy
  semantics (empty intents → `not_quoted` cancel), so all existing positional callers
  and tests keep compiling and passing unchanged.
- `_visit_one` computes the outcome via the classifier and threads the grace count in;
  `run()` owns the per-cid counter dict and updates it from cycle results.
- Status vocabulary UNCHANGED (dashboard compat): held cycles return DECLINED with
  `cancelled=0, submitted=0`. No new `LiveFleetResult.status`, no new `CANCEL_*` reason
  for the hold path (nothing is cancelled); grace expiry reuses `CANCEL_NOT_QUOTED`.

## Improvement proposal (adopted, edge-case hardening)
- Classify refusals into transient vs terminal instead of blanket-holding every
  empty-intent cycle — otherwise a settled market's resting orders would be held
  through grace instead of cancelled now. Evidence (verbatim, `core_brain/quotes.py`):
  "When both sides block here, plan_orders() sees empty intents and cancels all OPEN
  orders for this market — the safe path for a settled book (Polymarket also cancels
  at settlement, but we don't wait for it)."

## Tasks
Dependency graph: T1 (tests) <- T2 (classifier + hold wiring) <- T3 (grace + expiry) <- T4 (shadow proof).

### [x] T1 — RED tests: refused-hold, terminal-cancel, grace-expiry [Backend/Logic] (M)
Target: tests/test_trader_loop.py (new section).
- Refused-visited holds: `decide` returns `([], "<transient why>")` with open orders →
  `run(once)` yields DECLINED, cancel_fn and submit_fn never called.
- Terminal cancels now: `decide` returns `([], "UP: mid 0.950 outside [0.20,0.80] -- decided market; ...")`
  with open orders → cancel_fn called (existing `not_quoted` behavior pinned).
- Token rotation still cancels: one-leg intent for a new token + open order on an old
  token → old order cancelled (pins the `_replacement_seam` behavior, no regression).
- Grace expiry: N consecutive refused cycles → hold × (N-1), cancel on Nth (threshold
  via the named constant, not a literal).
Helper skill: test-driven-development. Depends on: none.
Verify: new tests fail on current code (hold cases cancel today), then
`pytest -q tests/test_trader_loop.py` green after T2/T3.
Checkpoint: contract proven by tests (T1+T2).

### [x] T2 — GREEN: refusal classifier + hold wiring [Backend/Logic] (M)
Target: core_brain/trader_loop.py (`_classify_refusal`, `VisitOutcome`, `plan_orders`
outcome param with legacy default, `_visit_one` outcome threading; `quotes.py` untouched).
Transient hold returns no-cancel/no-submit; terminal routes to the existing `not_quoted`
path byte-for-byte. `[HOLDING]` log line on held cycles.
Helper skills: test-driven-development, debugging-and-error-recovery. Depends on: T1.
Verify: `pytest -q tests/test_trader_loop.py` (T1 hold/terminal/rotation tests green;
grace-expiry test still red until T3).
Checkpoint: hold proven, expiry pending (T2).

### [x] T3 — GREEN: grace counter + expiry [Backend/Logic] (S)
Target: core_brain/trader_loop.py (`run()` owns per-cid refused-cycle counts,
`REFUSED_HOLD_GRACE_CYCLES` named constant; reset on quoted/submit; restart resets).
Expiry cancels via the existing `not_quoted` path — no new cancel machinery.
Helper skill: test-driven-development. Depends on: T2.
Verify: `pytest -q tests/test_trader_loop.py` fully green incl. grace-expiry test.

### [x] T4 — Shadow proof: hold across a refused cycle, cancel past grace [Backend/Logic] (S)
Target: tests/test_shadow_run.py (new test): two-cycle rehearsal (refuse with transient
why, then quote) holds resting across the refused cycle without cancel/submit; a second
variant refused past grace cancels. No prod code (verification only unless it exposes
a wiring miss).
Helper skill: test-driven-development. Depends on: T3.
Verify: `pytest -q tests/test_trader_loop.py tests/test_shadow_run.py`.
Checkpoint: end-to-end behavior proven.

## Guardrails
- Focused suites `tests/test_trader_loop.py` + `tests/test_shadow_run.py` green after
  every change; new behavior has tests that fail without it (RED first).
- No new dependencies, no config-value changes, `data/orders.db` read-only, no live runs.
- `quotes.py` decision logic and `_cancel_dropped_markets` untouched.
- No sub-issue ceremony (3–4 coupled tasks on one branch; same precedent as #386).

## Notes (session memory — do not lose)
- Prior `tasks/plan.md` header was Issue #386 (different issue/branch); this file now
  plans #390. Nothing from #386 carries over.
- Auto-picked #390: it is the only open issue; labels `["ready-for-agent"]`, no
  `quick-fix` label → Step 0C divert not evaluated, straight to planning.
- Claimed via `gh issue edit 390 --add-assignee "@me"` (PowerShell needs the quotes).
