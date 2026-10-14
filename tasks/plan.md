# Plan — #524 Rescue closes never record a fee so taker cost reads unmeasured

Branch: i524/fix-rescue-closes-never-record-a-fee-so-taker-cost | Issue: #524

Size: **Small** — one function plus its writer in one production file, tests in one
test file; straightforward now that every seam is read. Task type: **Code**.
Domain tag for all tasks: `[Backend/Logic]`. Helper skills: `test-driven-development`,
`incremental-implementation`. Verification mode: pytest runners (logic, no UI).

## Intake — CodeRabbit plan (adopted / rejected / resolved)

- Adopted: task skeleton (shadow fee → live policy → friction proof), affected files
  (`core_brain/single_buy_saver.py` + `tests/test_single_buy_saver.py`), exact test
  assertions, regression file list, hands-on verify sketch.
- Rejected: `scripts/seed_rescue_fixture.py` and the `docs/issues/514-*.md` ledger edit —
  existing helpers (`registry` fixture, `_one_sided_pair`, `FakeClient`) already seed a
  throwaway store and force a real exit; GitHub is the canonical tracker. No
  `order_registry.py` change (INSERT at `:1602` already writes `close.fee`).
- Resolved from live code (was `[UNVERIFIED]`): shadow branch is
  `if hasattr(client, "_sell_into_bids")` with `reason=` passed only there (`:978-981`);
  `_record_exit_close` has no `fee` kwarg (`:694-699`); `CloseRecord.fee` defaults `None`
  and persists (`order_registry.py:296,769,1602`); NULL = unmeasured, `0.0` = measured
  (`statistical_analytics.py:606-623,708-763`); `exit_naked_leg` is the alias (`:2074`);
  resp shapes read via `resp.get("price"/"size")` with floor/requested fallback
  (`:650-691`); CONSTRAINTS concern cleared — the #517 file is branch-scoped and does
  not bind this branch.

## Spec (embedded — Small tier)

- Goal: rescue closes persist an explicit fee so the RESCUE FRICTION card's taker-cost
  drag ratio reads measured instead of "unmeasured".
- Acceptance 1: a shadow rescue close (full and partial fill, both methods) saves
  `fee == 0.0`, never `None`.
- Acceptance 2: under the adopted shadow-only policy, a live rescue close still saves
  `fee is None` (deliberately unmeasured); the friction card reads measured-zero on
  shadow stores.
- Out of scope: fee schema/migration, TCDR formula, merge/resolution writers, fee-regime
  sources, sell behavior, any new script, `data/orders.db`, live commands, START.

## Frozen interface contract

- `ZERO_FEE = 0.0` in `core_brain/single_buy_saver.py` + comment naming the source and
  which closes receive it (shadow rescues; live taker fees unknown at sell time → NULL).
- `_record_exit_close(..., fee: Optional[float] = None)`, passed into `CloseRecord(fee=...)`.
- `exit_single_buy` passes `fee=ZERO_FEE` only inside the existing `_sell_into_bids` branch.

## Fee-policy decision (adopted default)

Shadow-only (CodeRabbit Option 2). Evidence, verbatim: "an unmeasured fee and a zero fee
are different claims" (`venue_fees.py:77`) against "the taker rate stepped 0.03 → 0.05 →
0.07 afterwards" (`venue_fees.py:14`) and `fee_rate: float = 0.07  # taker fee rate`
(`config.py:1115`). Recording `0.0` on live rescue sells would print a measured zero the
venue may have charged. Owner may override to all-rescues before Station IV; that is a
planning change recorded here first.

## Improvement proposal

None beyond the adopted plan — the change is already minimal (one constant, one kwarg,
one call-site argument, reuse of the existing branch check and helpers). Recorded so it
does not resurface.

## Tasks

### T1 [M] Shadow rescue closes save an explicit zero fee [Backend/Logic]
Files: `core_brain/single_buy_saver.py`, `tests/test_single_buy_saver.py`. Depends on: —
- Test-first: add `ShadowFakeClient(FakeClient)` with `_sell_into_bids` marker and
  `create_and_post_market_order(order_args, reason=None, ...)` returning
  `{"success": True, "orderID": ..., "price": <bid>, "size": <filled>}`.
- `test_shadow_rescue_exit_records_an_explicit_zero_fee`, parametrized over
  `("single_buy_exit", "ladder_exit")`: seed `_one_sided_pair(registry)`, call
  `exit_single_buy(..., max_pair_cost=MAX_PAIR_COST, live=True, reason="hard_stop",
  method=method)`; assert `action == "exited"`, one close with that method,
  `fee is not None and fee == 0.0`. Confirm RED first.
- `test_partial_shadow_rescue_exit_records_zero_fee`: `ShadowFakeClient` with small
  `size` in resp against held 10.0; assert `shares < 10` and `fee == 0.0`.
- Code: `ZERO_FEE` + comment, `fee` kwarg on `_record_exit_close` → `CloseRecord`,
  `fee=ZERO_FEE` at the shadow call site only.
- Verify: `pytest -q tests/test_single_buy_saver.py` (new cases green, rest unbroken).

### T2 [S] Live rescue close keeps fee unmeasured (policy pin) [Backend/Logic]
Files: `tests/test_single_buy_saver.py` (test only — no production change under the
adopted policy). Depends on: T1.
- Test-first: `test_live_rescue_exit_keeps_fee_unmeasured` with plain `FakeClient`;
  assert saved close `fee is None`. Confirms RED-then-GREEN trivially (documents the rule).
- If owner overrides to all-rescues: pass `fee=ZERO_FEE` on the live branch instead and
  rename test to `test_live_rescue_exit_records_the_regime_zero_fee` asserting `0.0`.
- Verify: `pytest -q tests/test_single_buy_saver.py -k "live_rescue"`.

### T3 [S] Executed rescue close reads as measured zero on the friction card [Backend/Logic]
Files: `tests/test_single_buy_saver.py` (test only expected). Depends on: T1.
- Test-first: `test_executed_rescue_close_reads_as_measured_zero_friction` — shadow exit
  via `ShadowFakeClient`, pass `registry.get_all_closes()` to
  `rescue_friction_summary`; assert `fee_measured_count == 1`, `mean_tcdr == 0.0`, and
  `realized_pnl != 0` (zero PNL yields `mean_tcdr None`). If it fails, fix T1, not the formula.
- Verify: `pytest -q tests/test_single_buy_saver.py tests/test_rescue_friction_analytics.py`
  plus extras `tests/test_rc_fixes.py tests/test_shadow_exec.py`.

Checkpoint after T1: shadow fee persisted end-to-end (constant → writer → saved close).
Checkpoint after T3: card reads measured zero; ready for Station IIIB review.

## How to verify (hands-on, for the report)

1. Terminal 1: `python -m dashboard.server --db data\rescue_fee_01.db --port 8799`.
2. Terminal 2: `python -m core_brain.shadow_run --minutes 5 --interval 1 --db data\rescue_fee_01.db --run-id rescue-fee-01`.
3. Open `http://127.0.0.1:8799`; confirm SHADOW badge + throwaway store (production name = failure).
4. After a rescue appears, read-only `closes` query → rescue rows show `fee 0.0`, not NULL
   (no rescue fired = inconclusive, not passed).
5. Performance & Analytics → RESCUE FRICTION → 0% with measured-fee count > 0, not "unmeasured".
