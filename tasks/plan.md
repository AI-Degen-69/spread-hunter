# Plan — #493 [FIX] Sentry and offense loops each price what they inspect from their own book read

Branch: i493/sentry-and-offense-loops-each-price-what-they-insp | Issue: #493
Size: Standard | Type: Debug + Code [Backend/Logic]
Stack: Python, pytest, SQLite
Labels: bug, ready-for-agent
Note: supersedes the #488 plan below in this file's history (kept in git); CodeRabbit plan reply: none arrived (only our request comment of 03:13Z).

## CodeRabbit intake note
What was adopted: nothing (no reply). Rejected: n/a. [UNVERIFIED]: nothing — all seams below verified verbatim in the tree.

## Improvement proposal (adopted, simplification/hardening)
Pure-fresh-for-sentry would drop the shadow-01 adverse-selection guard the
snapshot was built for. Evidence, verbatim from
`core_brain/shadow_exec.py:970` docstring: "shadow-01 showed h1 markout drift
of -17c to -37c on 4 of 12 exits." Adopted instead: age-bound snapshot —
fresh snapshots (seconds old) keep the guard, stale ones (>60s) fall back to
the fresh book. One condition, both biases dead.

## Interface contracts
- `ShadowExecutionClient._sell_into_bids(token_id, shares, *, floor, fresh=False)`
  signature unchanged by this round (keeps the `fresh` flag from #491;
  hard-stop exits keep calling with `fresh=True` -- see
  `tests/test_hard_stop_fresh_exit.py`); behavior gains: snapshot older than
  `MAX_SNAPSHOT_AGE_SEC = 60.0` is treated as absent (fresh-book path).
- `read_last_token_snapshot` unchanged (still returns newest row for run).
- `queue_marks.ts` is epoch seconds; age = `self._now_fn() - snap["ts"]`.
- Files that must NOT change: `order_manager.py`, `order_registry.py`,
  `trader_loop.py`, `single_buy_saver.py`, `data/orders.db`.

## Dependency graph
- Task 1 (RED test) — unblocks Task 2.
- Task 2 (age-bound fix) — depends on Task 1.
- Task 3 (existing snapshot-case ts updates + full focused regression) — depends on Task 2.
- Task 4 (commit, push, PR) — depends on Task 3.

## Tasks

### [ ] Task 1 — RED: stale-snapshot test fails [Size: S]
- Domain: [Backend/Logic]. Skill: test-driven-development.
- File: `tests/test_exit_price_realism.py`.
- Add `test_rehearsed_sell_ignores_a_stale_snapshot_and_uses_the_fresh_book`:
  `write_queue_mark` with `ts=time.time() - 901`, `best_bid=0.54`,
  `best_bid_size=8.0`; fresh book bids `{0.33: 20.0}`; floor `0.31`;
  assert `filled == 5.0`, `avg == 0.33` (mirrors the i492 NBA leg).
- Verify it FAILS on the current tree (books 0.54).
- Depends on: nothing.

### [ ] Task 2 — GREEN: age-bound snapshot in `_sell_into_bids` [Size: S]
- Domain: [Backend/Logic].
- File: `core_brain/shadow_exec.py:59` (constant), `:970` (method + docstring).
- Add `MAX_SNAPSHOT_AGE_SEC = 60.0` next to `HARD_STOP_SELL_REASONS`;
  in `_sell_into_bids`, skip the snapshot ladder when
  `self._now_fn() - float(snap["ts"]) > MAX_SNAPSHOT_AGE_SEC`;
  update the docstring (snapshot = prior rotation, seconds old).
- Depends on: Task 1.

### [ ] Task 3 — Existing snapshot cases go now-relative + regression [Size: S]
- Domain: [Backend/Logic].
- File: `tests/test_exit_price_realism.py` (`ts=1_000.0` -> `ts=time.time()`
  in the four snapshot tests; price/size assertions UNCHANGED).
- Run: `python -m pytest -q tests/test_exit_price_realism.py tests/test_hard_stop_fresh_exit.py tests/test_aged_out_rescue.py tests/test_risk_guardian.py tests/test_settlement_sweeper.py tests/test_shadow_exec.py` — all green.
- Depends on: Task 2.

### [ ] Task 4 — Commit, push, open PR [Size: XS]
- One commit on `i493/...`, push, `gh pr create` for #493. No merge (Station IV).
- Depends on: Task 3.

## Checkpoints
- After Task 2: RED test flips GREEN; focused file green.
- After Task 3: all six suites green.
