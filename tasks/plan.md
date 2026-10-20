Branch: i718/settle-bare-rebate-type | Issue: #718

# Plan — #718 Settle the bare REBATE activity type in the PnL cross-check

- Size: **Small** — one module (`core_brain/pnl_crosscheck.py`) plus its test
  file; a single vocabulary decision, no logic change.
- Task type: **Code** (money-path defect fix; TDD, risk-first).
- Labels: `ready-for-agent` only — no `quick-fix` label, so no lane divert
  (Step 0C skipped per rule 1).
- Base: `origin/main` @ `4e12ce6` (HEAD == origin/main at branch time; `main`
  itself is checked out in another worktree, so the branch was cut from the
  same SHA instead of switching).

## CodeRabbit plan intake (read once, verified against the tree)

- Adopted: remove bare `REBATE` from `CREDIT_TYPES` (docs-based decision);
  comment rewrite citing only the `/activity` endpoint docs; 3-task shape
  (decision + preserve-behavior + acceptance); literal-set request-vocabulary
  test; `UNJUDGED`-never-dropped safety argument.
- Rejected: nothing material — the plan's scope discipline (2 files only,
  ledger untouched) matches the issue.
- Verified seams (all exist verbatim): `CREDIT_TYPES` / `ACTIVITY_TYPES` at
  `core_brain/pnl_crosscheck.py:55-60`; `replay_rows()` (~203-220),
  `_cash_delta()` (~172-200), `replay_cashflow(..., types=ACTIVITY_TYPES)`
  (~270); test fixtures `_trade` / `_event` and the captured-request
  monkeypatch pattern (`tests/test_pnl_crosscheck.py:78-101`); existing
  credit test at `:55`.
- `[UNVERIFIED]` from code: none left open — venue evidence came from the
  live docs page (see Spec), read-only, no venue call from this repo.

## Spec (embedded — Small tier, no SPEC.md)

- Goal: the two lists agree about bare `REBATE`, with the reason in the
  comment above them.
- Decision: **remove `REBATE` from `CREDIT_TYPES`** (keep in nothing else).
  Evidence, read-only from the official docs page
  `https://docs.polymarket.com/api-reference/core/get-user-activity`: the
  `type` request filter and the response `type` enum list exactly `TRADE,
  SPLIT, MERGE, REDEEM, REWARD, CONVERSION, DEPOSIT, WITHDRAWAL, YIELD,
  MAKER_REBATE, TAKER_REBATE, REFERRAL_REWARD` — **no bare `REBATE`**.
  Combined with the operator's standing directive ("any rebate related stuff
  not being used for other things should be gone"), dead vocabulary goes.
- Safety: an unexpected bare `REBATE` row becomes `unreadable`, the replay
  `complete is False`, verdict `UNJUDGED` — loud, never a false pass.
- Edge cases: `" rebate "` (whitespace/case variant — the classifier
  strips/uppercases) is equally unknown; a bare `REBATE` inside another
  type's walk poisons only that walk to incomplete; negative-amount credits
  keep the existing absolute-value rule.
- Out of scope: every other type, KPI report, fee math, `--inclusive`
  behaviour, credit-counting rule, the #708 ledger.

## Interface contracts (frozen before build)

- `CREDIT_TYPES = ("MAKER_REBATE", "REWARD", "REFERRAL_REWARD", "CONVERSION")`
  (order kept); `ACTIVITY_TYPES` byte-identical to today.
- `replay_rows(rows) -> (trading, credits, seen, unreadable)`: signature and
  counting rule unchanged.
- `replay_cashflow(..., types=ACTIVITY_TYPES, ...)`: default unchanged.
- Subset invariant, pinned by test: `set(CREDIT_TYPES) <= set(ACTIVITY_TYPES)`.

## Improvement proposal (adopt-by-default, hardening)

- Pin the subset invariant `set(CREDIT_TYPES) <= set(ACTIVITY_TYPES)` as a
  guard test so a future type can never re-introduce this drift silently.
- Evidence (issue Acceptance criteria, verbatim): "The two lists agree about
  the bare `REBATE` type". A point-check on `REBATE` alone proves today; the
  subset guard proves the criterion for every future type too. Adopted into
  T1 below.

## Dependency graph & tasks (risk-first, 3 tasks)

- T1 (tests first) unblocks T2 (fix) unblocks T3 (acceptance).

- [x] **T1 (S) [Backend/Logic]** — Regression + preserve-behavior tests in
  `tests/test_pnl_crosscheck.py` (target files: that file only).
  `test_bare_rebate_is_not_a_known_type` (`replay_rows([_trade("SELL", 5.0),
  _event("REBATE", 1.0)])` == `(5.0, 0.0, 2, 1)` + `" rebate "` variant);
  `test_bare_rebate_in_feed_makes_replay_incomplete` (captured-request
  pattern, `types=("REWARD",)`, fake page `[{"type": "REBATE",
  "usdcSize": 1.0}]` → `credits == 0.0`, `unreadable == 1`,
  `complete is False`, `pnl_inclusive == pnl`);
  `test_credit_types_are_all_requested` (`"REBATE" not in CREDIT_TYPES`,
  `set(CREDIT_TYPES) <= set(ACTIVITY_TYPES)`);
  `test_remaining_credit_types_still_count_as_credits` (param over the 4
  survivors, ±2.0 → `(0.0, 2.0, 1, 0)`); `test_default_request_vocabulary`
  (literal 8-type set, one request each, no `REBATE`). Depends on: none.
  Verify: new decision tests FAIL on old code (RED), preserve tests pass.

- [x] **T2 (S) [Backend/Logic]** — The fix in `core_brain/pnl_crosscheck.py`
  (target files: that file only). Remove `"REBATE"` from `CREDIT_TYPES`;
  rewrite the comment above both lists (roles of each list, lists agree,
  bare `REBATE` absent on purpose + docs link, unexpected row →
  unreadable/`UNJUDGED`, credits-apart/`pnl_inclusive`-adds-back kept).
  Depends on: T1. Verify: focused suite green.

- [x] **T3 (XS) [Backend/Logic]** — Acceptance + operator verify block (43 passed; `python -B` replay snippet: REBATE → `(0.0, 0.0, 1, 1)`, all 4 survivors → `(0.0, 2.0, 1, 0)`)
  (target files: none — run only). `python -m pytest -q
  tests/test_pnl_crosscheck.py tests/test_venue_fees.py` green; draft the
  ≤5-step hands-on How-to-verify (comment read + `python -B` replay snippet,
  no pytest). Depends on: T2. Verify: command green, rerun after fixes.

## Checkpoints

- After T1: RED shown (decision tests fail, preserve tests pass) — one line.
- After T2–T3: focused suites green — hand off to Station IV.
