# PRIME → SUB — Stage 2 release, addendum to STAGE-2-DIRECTIVE.md

Your confirmation report is accepted in full. `08bb685`, 739 passed, all six skills resolve, no
disagreement on build order. Nothing in it needs correction.

`.claude/reviews/STAGE-2-DIRECTIVE.md` remains the specification and the acceptance bar. This file
adds constraints the confirmation surfaced and does not replace anything. Where the two disagree,
tell me rather than choosing.

## 1. Baseline locked

739 is now the fixed floor, MEASURED by you at `08bb685`. Acceptance arithmetic is `739 + N`, where
`N` is the count of new tests in `tests/test_order_registry.py`. No existing test file is edited,
renamed, or reordered. If the total is anything other than `739 + N`, stop and report the delta
before continuing — a moved baseline means something landed outside the boundary.

## 2. Reuse the existing SQLite conventions — do not invent new ones

`strategy/store.py:400-430` already establishes this repo's connection pattern. Read it before you
write `order_registry.py`. Carry over, with the reasoning intact:

1. `sqlite3.connect(path, timeout=BUSY_TIMEOUT_SEC)` — `store.py:387` sets 5.0 s. Same value.
2. `PRAGMA journal_mode=WAL`, in the same try/except shape as `store.py:416-421`. The poll loop
   writes continuously and you will want to read the registry while it runs; under the default
   rollback journal a reader blocks the writer for the busy timeout.
3. Schema creation idempotent and applied once per path, as `_schema_ready` does at `store.py:409`.

Three deliberate departures:

4. **A separate database path.** `run/live.db`, resolved independently. Do **not** route it through
   `_cfg.db_path()` — that returns the fleet/simulator database and mixing them is what
   `AGENTS.md:73` warns against. A test pointing at a temporary path must get a fresh schema.
5. **`PRAGMA foreign_keys=ON`, per connection, every connection.** SQLite defaults this OFF, and
   `store.py` never sets it — I grepped the file for `foreign_keys` and there is no hit. Without it
   the `fills.order_id → orders.id` foreign key in architecture section 2 is decorative: an orphan
   fill row inserts cleanly and `size_matched` silently sums rows belonging to no order. This is the
   single most likely way Stage 2 passes its tests and is still wrong. It is a per-connection pragma,
   not a per-file one, so it belongs in the connection helper, not the schema.
6. **`BEGIN IMMEDIATE` on any path that reads then writes.** `reconcile_orders()` reads registry
   state, compares against the venue response, then writes transitions. Under autocommit two
   overlapping reconciles can both read `open` and both write a transition. Take the write lock at
   the start of the read.

## 3. Schema constraints

1. **There is no `size_matched` column in `orders`.** Invariant 2 of the directive says the value is
   derived; a column that exists will eventually be written to, and the invariant then holds only by
   convention. Expose it as a query helper or a `VIEW` over `SUM(fills.size)`. If you believe a
   column is unavoidable, stop and tell me why rather than adding one.
2. **Timestamps are integer epoch milliseconds, UTC.** No local time, no ISO strings, no `TEXT`
   dates. Session 65 measured real clock skew between this host and the venue in this repo; string
   timestamps make that skew unanalysable and comparisons lexicographic.
3. `orders` stores `original_size` as posted. Remaining size is derived, never stored.
4. The orphan-adoption match window of directive 3.2.4 is a **named module constant with a stated
   default**, not a literal buried in a comparison. Report the default you chose and the reasoning
   in one sentence. I will accept or move it; I will not accept an unexplained number.

## 4. Test hardening — three ways these tests pass while proving nothing

Directive section 4 stands. These three need a specific shape:

1. **Test 3, restart equivalence.** The restart must reconstruct the registry object from disk —
   close the connection, drop the object, open a new one against the same file. If the test reuses
   an in-memory instance or an `:memory:` database across the "restart", it asserts nothing about
   durability, which is the entire reason this test exists.
2. **Test 7, backoff.** Inject the sleep and the clock; assert on the sequence of delays and the
   60 s cap. No real sleeping — a test suite that actually waits out an exponential backoff is a
   test suite people stop running.
3. **No network in any test.** The CLOB client is mocked at the boundary, including `get_trades` and
   `get_open_orders`. A test that reaches the venue is a test that fails on an airplane and passes
   for the wrong reason on a bad day.

## 5. Checkpoint — pause once, at the schema

Build 3.1 and stop. Report to me:

- the `CREATE TABLE` statements exactly as written,
- the connection helper in full,
- the three invariant tests of directive 3.1, failing first then passing, both pasted.

Then wait. Everything downstream keys off this schema; a wrong column here is a rewrite of 3.2 and
3.3, whereas a wrong poll cadence is a one-line change. This is the only pause I am asking for —
after I confirm the schema, build 3.2, 3.3 and the remaining tests through to acceptance without
stopping, and report once at the end in the shape of directive section 6.

## 6. On your last run

Everything asked for arrived, correctly tagged, with real output. One efficiency note, not a
correction: you spent roughly twenty-five turns polling for a 143 s pytest run. Run the suite
blocking and wait for it. During implementation run `python -m pytest tests/test_order_registry.py -q`
— seconds, not minutes — and reserve the full suite for the checkpoint and for acceptance.

## 7. Boundary — unchanged

`strategy/`, `tests/`, `scripts/`, `research/` on `session-66-live-exec-loop`. No `--live`
invocation, ever, in this phase. No edits to `.env`, `server/`, `run/`, `strategy/merge.py`
arithmetic, `sweep.py`, `fills.py`, or any `config.py` value. Do not touch `AGENTS.md` or
`.claude/` — both are mine this session and `AGENTS.md` is dirty in the tree.

Stage 2 remains read-only against the venue: it places no order, cancels nothing, sends nothing.
