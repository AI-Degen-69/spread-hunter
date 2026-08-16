# PRIME → SUB — Stage 2: the order registry and fill detection

Read `.claude/reviews/STAGE-2-4-ARCHITECTURE.md` in full first. It is the reviewed design and the
source of truth for this stage. This directive is the build order and the acceptance bar; where the
two appear to disagree, the architecture document wins and you tell me.

Branch `session-66-live-exec-loop`, currently at `bd1f235`. Pull before you start.

## 0. Standing rules — these carry across every stage

1. **A value you report must come from a command you ran in that same report.** Paste the command
   and its real stdout. Never transcribe a hash, address, selector, count or timing from memory or
   from a summary, including when you are confident it is right. A correct value with fabricated
   evidence is indistinguishable from a wrong one.
2. **Return the artifact that was asked for, or one sentence saying why you did not.** Never
   substitute a different artifact under the same heading. "That tool does not exist in my
   environment" is always an acceptable answer.
3. **A guard behaves identically in preview and in execution.** Anything deciding whether an
   operation is safe runs before the branch that returns early.
4. **State what you did not verify.** Tag every figure MEASURED / DERIVED / ASSUMED, and MEASURED
   means the output is in front of you.

## 1. Write boundary

You may edit `strategy/`, `tests/`, `scripts/`, `research/`. Forbidden without exception: **no
`--live` invocation, ever, in this phase**; no edits to `.env`, `server/`, `run/`; no change to
`strategy/merge.py`'s arithmetic, `sweep.py`, `fills.py`, or any `config.py` value.

Stage 2 is read-only against the venue. It places no order, cancels nothing, sends nothing.

## 2. Skills

| Step | Skill |
| --- | --- |
| Implement | `superpowers:test-driven-development` — red before green, show both |
| Stuck | `superpowers:systematic-debugging`, then `investigate` |
| Gate before reporting | `superpowers:verification-before-completion` |
| Review the diff | `review` — paste raw output, or say plainly that it did not run |
| Receiving my corrections | `superpowers:receiving-code-review` |

`ecc:*` and `compound-engineering:*` are not in your environment; I run those lenses.

## 3. Build order

### 3.1 `strategy/order_registry.py` — a new `run/live.db`

**Not `run/fleet.db`.** That file holds simulator output and `AGENTS.md:73` warns against mixing.

Two tables, per architecture document section 2: `orders` (local uuid4 primary key, nullable unique
`order_id`, `max_pair_cost_at_post`, `pair_id`) and `fills` (venue `trade_id` primary key, FK to
`orders.id`).

Three invariants, and every one of them has a test:

1. **The row is written before the order is sent, keyed by a local uuid.** `order_id` is attached
   afterwards from the response. This is the whole reason the key is local: the venue assigns
   `order_id`, so keying on it would make the invariant unsatisfiable.
2. **`size_matched` is derived**, `SUM(size)` over `fills`. Never written directly, never copied
   from a snapshot. Stage 4 sizes real money from this number.
3. **Writes are atomic and fail closed**, matching `_atomic_write_json` and the post-R10
   `_log_order`: a failed write raises rather than returning, and nothing proceeds on a failure.

### 3.2 `reconcile_orders()`

1. `get_open_orders()` once. A registry row marked `open` that is absent from the response is no
   longer resting.
2. Disambiguate filled from cancelled with `get_trades(TradeParams(maker_address=...,
   after=last_polled_ts - 60))`, deduped on `trade_id`. **A row moves to `filled` only on trade
   evidence, never on absence alone.**
3. The 60-second overlap is deliberate — do not "optimise" it to an exact boundary. Our clock and
   the venue's differ, Session 65 measured that skew in this repo, and `after`'s inclusivity is
   unverified. Overlap plus an idempotent dedupe key has neither failure mode.
4. **Orphan adoption.** A venue order with no local row must be matched on
   `(token_id, price, original_size, posted_ts +/- window)` and bound to a `pending` row, or
   recorded as unattributed. Without this a timed-out placement is invisible forever.
5. Partial fills are first-class. `size_matched < original_size` is the normal maker case.

### 3.3 The `poll` subcommand

Default 5 s cadence, configurable. Reconcile once on start, before anything else. Exponential
backoff to a 60 s cap on `429` or 5xx, logging every transition — never a bare retry loop. Rate
limits are not a constraint: CLOB allows 9,000 requests per 10 s and Data API `/trades` allows 200;
a 5 s cadence uses 2.

**Operability is a requirement, not polish** — architecture document section 6. This is the first
process the Owner leaves running. A status line per cycle; an append-only event log with one line
per state transition; a heartbeat every cycle so absence is the alarm; `SIGTERM` and
`KeyboardInterrupt` complete the cycle and exit cleanly under the Stage 0 pattern. "Nothing
happened" must be distinguishable from "it died."

## 4. Tests — `tests/test_order_registry.py`, new file

1. Each registry invariant in 3.1, one test each.
2. A replayed fill sequence produces the correct row transitions.
3. **A mid-sequence restart produces the same end state as an uninterrupted run.** This is the test
   the stage exists for.
4. A trade landing in the boundary second is counted exactly once.
5. A duplicate `trade_id` across two polls does not double-count.
6. An orphan venue order is adopted, and an unmatchable one is recorded as unattributed.
7. A `429` produces backoff, not a crash.
8. Absence from `get_open_orders` without trade evidence does **not** mark a row `filled`.
9. Every existing test still passes unmodified — 739 at `bd1f235`.

## 5. Acceptance

1. `python -m pytest tests/ -q` green; report the count against the 739 baseline.
2. Test 3 (restart equivalence) and test 8 (no fill without evidence) demonstrated explicitly in
   the report, failing-first then passing.
3. `python -m strategy.live_exec poll --once` runs against an empty registry, touches the network
   read-only, and prints its status line. Paste the transcript.
4. `review` raw output, or a plain statement it did not run.
5. Files: `strategy/order_registry.py`, `strategy/live_exec.py`, `tests/test_order_registry.py`,
   `research/`. Nothing else.
6. Commit to `session-66-live-exec-loop` when green.

**Out of scope:** Stage 3 (stop-loss), Stage 4 (completion), Stage 4.5 (the manual live order),
Stage 5 (quoting). Do not scaffold toward them.

## 6. Report shape

    ## STAGE 2 REPORT
    ### 0. Approach          three lines - what you added, in what order
    ### 1. Schema            the CREATE TABLE statements as written
    ### 2. Diff              git diff --stat + full text of the new files
    ### 3. Tests             failing-first | passing | pytest tests/ -q summary
    ### 4. Poll transcript   verbatim stdout
    ### 5. Review            `review` raw output | not-fixed + why
    ### 5b. Self-analysis    yours, labelled as yours
    ### 6. Blockers

Every figure tagged MEASURED / DERIVED / ASSUMED. Do not report the stage complete until
`superpowers:verification-before-completion` passes.
