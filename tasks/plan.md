# Plan — Issue #840: Live execution lane audit and reachability verification

Branch: `i840/live-execution-lane-audit` | Issue: #840

Size: **Standard** — 3 new files + 3 surgical edits, one architectural decision
(single-order exact-ID probe); no public API or schema change.
Task type: **Code + Security + Testing + Docs**.

## 0. CodeRabbit intake (read once, costed once)

- **Adopted:** two-phase shape (audit+parity first, probe second); champion =
  `prudent` + five per-field env overrides; exact-ID cancel instead of
  `cancel-all`; hold 2–4s (default 3s) so the 5s cancel limit is reachable;
  scratch `--db` for `decide`; rank-by-fresh-bid-depth market selection;
  exit-nonzero-on-venue-read-failure for `status`/`balance`.
- **Rejected:** new `champion` tournament preset (unneeded — loader order
  preset-then-override already expresses it); `cancel-all`/`cancel-market` as
  the cancel path (unsafe to unrelated orders); 5s hold (leaves no time for
  the cancel receipt); opt-in `--strict` flag (reachability must fail loud by
  default); fixing the unexplained divergences here (out of scope).
- **[UNVERIFIED] (verify from code before use):** exact
  `risk.queue_clear_block` call signature (follow
  `tests/test_queue_clear_gate.py`); `BookProvider` construction + `_rest_reader`
  error behavior; CLOB market-response field name for venue minimum size;
  Python key name for `not_canceled` in the cancel receipt; helper name of the
  matched-size alias reader in `single_buy_saver.py` (~lines 514–565);
  existence of a read-only `is_approved_for_all` command; existing markets test
  module for the two `fetch_pinned_market` tests.

## 1. Improvement proposal (evidence-based, adopted)

> **Cancel by exact order ID, never `cancel-all`.**
> Evidence — the ticket suggests *"immediately cancelled via `cancel-all` /
> `cancel-market`"*, but `order_manager.py` already cancels survivors by exact
> ID (`client.cancel_order(OrderPayload(orderID=...))`), and broad cancels
> would sweep unrelated operator orders. Classification: simplification /
> edge-case hardening → **adopted by default**, folded into Tasks 3–4. Rejection
> recorded: none — the broad-cancel path is rejected.

## 2. Dependency graph

- Task 1 (audit doc + parity tests) → Task 2 (exit codes reuses parity env
  helpers; both land before any order-sending code) → Task 3 (pre-flight +
  dry-run; no signing) → Task 4 (post/hold/cancel/evidence; needs Task 3's
  pre-flight + price/size gates).
- Checkpoints after Task 2 (read-only lane provably loud) and after Task 4
  (full probe proven with fakes + evidence redaction test).

## 3. Tasks (atomic vertical slices)

### Task 1 — Audit report + champion parity tests [Docs/Backend] — M [x]

- **Target files:** `docs/live-lane-audit.md` (new),
  `tests/test_champion_lane_parity.py` (new).
- **Build:** write the audit per SPEC §3–§4: planned simulation choices vs
  unexplained differences (rollback, cancel-response, readback, pair IDs,
  launcher preset loss, `BOT_*` inheritance, dropped telemetry, 0.99 live cap,
  reconciliation gaps, no client timeouts); what an accepted order proves /
  does not prove; `is_approved_for_all` command-or-gap; dry-run signed-client
  caveat; pre-flight command list with the `decide`-needs-scratch-DB and
  `fetch_live_balance`-hides-errors notes; file unexplained items as follow-up
  tickets, fix nothing. Parity tests (fail-without-change):
  `test_live_and_shadow_load_identical_champion_settings`
  (`config.load()` vs `shadow_run.shadow_cfg()`),
  `test_champion_values_are_effective` (offset on, 0.60, 2¢–4¢, deadband 0.06,
  `ABSOLUTE_DROP` + 0.045, gate on + 3 min),
  `test_field_override_beats_preset`, `test_shadow_bankroll_is_the_only_difference`,
  `test_queue_gate_blocks_four_minutes_allows_two`.
- **Depends on:** — (first).
- **Verify:** new parity file green; `git status` shows only the two new files.
- **Helper skill:** `test-driven-development`.

### Task 2 — `status` / `balance` fail loudly [Backend/Logic] — S [x]

- **Target files:** `core_brain/order_manager.py`, `tests/test_order_manager.py`.
- **Build:** in `status()`, keep the `ERROR` print, then exit non-zero when
  `get_open_orders` raises or `open_notional` fails/returns `None`. In
  `balance()`, keep the `ERROR` print, then exit non-zero when
  `get_balance_allowance` raises. Zero balance stays a warning, not a failure.
  Normal stdout byte-identical; `decide` untouched. New tests
  (fail-without-change): `test_status_exits_nonzero_when_open_orders_read_fails`,
  `test_status_exits_nonzero_when_open_notional_is_none`,
  `test_balance_exits_nonzero_when_venue_read_fails`,
  `test_balance_zero_is_a_warning_not_a_failure`.
- **Depends on:** Task 1.
- **Verify:** `python -m pytest -q tests/test_order_manager.py` green with old
  stdout tests unchanged.
- **Helper skill:** `test-driven-development`.

### Task 3 — `reach-probe` pre-flight + dry run [Backend/Logic] — M [x]

- **Target files:** `core_brain/reach_probe.py` (new, injectable client fn +
  `live` param per `order_execution.py` style), `core_brain/markets.py`
  (optional venue-min-size field, default `None`), `core_brain/order_manager.py`
  (`reach-probe` subcommand, `--live` with `default=SUPPRESS`; legacy `probe`
  + module docstring untouched), `tests/test_order_manager.py` (+ markets
  module if one exists).
- **Build:** inputs — optional condition ID, `--discount` 0.15, `--hold-sec`
  2–4 (default 3), `--db` default `order_registry.DEFAULT_DB_PATH`,
  `--live/--no-live`. Auto-select market by fresh top-3 bid-level dollar depth.
  Fail-closed pre-flight (all must pass): client builds, `get_open_orders`
  readable, zero account orders on the market, pinned market open+accepting,
  token is one of the two market tokens, fresh `get_fresh_book` best bid,
  `open_notional` not `None`. Price = best-bid − discount rounded down to tick
  (refuse below one tick). Size = max(venue-min or local 5-share default,
  `VENUE_MIN_NOTIONAL_USD` size); enforce `order_caps()` + fixed $2 ceiling
  (size × price; no CLI raise). `--no-live` prints plan + worst case, never
  signs. New tests (fail-without-change): the 8 `refuses_*` cases + dry-run-
  never-signs + deepest-bid selection + size/notional + the two pinned-min-size
  parse tests.
- **Depends on:** Task 2.
- **Verify:** `python -m pytest -q tests/test_order_manager.py tests/test_book_provider.py`
  green; manual `reach-probe --no-live` print reviewed (operator runs it).
- **Helper skill:** `test-driven-development`.

### Task 4 — Post, exact-ID cancel, verify, evidence [Backend/Security] — M [x]

- **Target files:** `core_brain/reach_probe.py`, `core_brain/order_manager.py`,
  `docs/agents/safety.md`, `tests/test_order_manager.py`.
- **Build:** pending row first (quote helper, new pair ID) → one GTC post-only
  buy via `post_orders` batch-of-one → `venue_order_id` + `get_order` token
  readback. No-ID/error → row stays pending, guarded `cancel-market` fallback
  (safe: pre-flight proved zero other orders), result `uncertain`. Hold, then
  `cancel_order(OrderPayload(orderID=...))` exact-ID only (never `cancel_all`);
  require ID in `canceled`, fail with reason if in `not_canceled`; measure
  accept-to-receipt. Read status + matched size via imported
  `single_buy_saver` helper (no copy); confirm absent from `get_open_orders`;
  unreadable matched size → `unverified`, never 0. PASS = accepted + in
  `canceled` + matched 0 + absent + ≤5s. Non-PASS prints exposure + undo
  (`cancel <id>` if resting, `exit` for filled shares, `poll --once`). Mark
  row cancelled only after verified cancel. Redacted JSON evidence under
  `runtime/` (git-ignored; confirm ignore): timestamps, IDs, price/size/tick,
  venue-min-or-unknown, worst-case $, order ID, canceled/not_canceled lists,
  status, matched size, timings, result; shortened wallet. Safety.md entry:
  `reach-probe --live` = opening-exposure, session approval + undo. New tests
  (fail-without-change): the 10 cases named in the CodeRabbit plan (pending-
  row-first, exactly-one-post-only-GTC, exact-ID-never-cancel-all,
  pass-requires-canceled, not_canceled-fails, matched>0-fails-with-undo,
  unreadable-is-unverified, >5s-fails, no-ID-pending+fallback,
  evidence-has-no-secrets).
- **Depends on:** Task 3.
- **Verify:** `python -m pytest -q tests/test_order_manager.py tests/test_order_manager_decide.py
  tests/test_shadow_guard.py` green; evidence redaction test asserts no secret
  fields. Live `--live` run is operator-only, never agent-run.
- **Helper skill:** `test-driven-development` + `security-and-hardening`.

## 4. Interface contracts

- `reach-probe [--condition-id CID] [--discount 0.15] [--hold-sec 3 (2–4)]
  [--db PATH] [--live | --no-live]` → exit 0 + `PASS` JSON summary only on the
  five-way conjunction; any other outcome exits non-zero with exposure + undo.
- Evidence JSON schema: `{ts_accept, ts_cancel_receipt, condition_id,
  token_id, price, size, tick_size, venue_min_size | "unknown",
  worst_case_usd, order_id, canceled[], not_canceled[], status, matched_size,
  hold_sec, accept_to_receipt_sec, result}` — no secret keys ever.
- `status`/`balance` contract change: stdout identical; exit code now 0 ⟺ venue
  read succeeded.

## 5. How to verify (operator, hands-on — no pytest/gh/git steps)

1. Precondition: no Trader loop or dashboard START running.
2. Run `status` and `balance` — funded address shown, exit 0; any venue-read
   ERROR now exits non-zero.
3. Run `decide --all --db <scratch-path>` — books + tick sizes print for
   graduated markets (scratch DB because `decide` writes rows).
4. Run `reach-probe --no-live` — printed price ≥15¢ below best bid,
   worst case ≤ $2, nothing signed.
5. With in-session approval only, run `reach-probe --live` — `PASS`, cancel
   receipt ≤ 5s, matched size 0; open the evidence file and confirm the order
   ID is in `canceled`. Undo if not PASS: `cancel <id>` if resting, `exit` for
   filled shares, then `poll --once`.
