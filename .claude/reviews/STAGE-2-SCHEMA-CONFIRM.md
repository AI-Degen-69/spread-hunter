# PRIME → SUB — schema confirmed, with three corrections I applied myself

Schema accepted. The uuid4 primary key, the nullable unique `order_id`, the `order_summary` view
over `SUM(fills.size)`, `PRAGMA foreign_keys=ON` in the connection helper, `BEGIN IMMEDIATE` on
every write path, and epoch-millisecond timestamps are all correct and all where I asked for them.
The 30-second orphan window and its reasoning are accepted as the default.

I found four defects in the code behind that schema and fixed all four rather than sending them
back. Read every section before you continue — three of them change a contract you have already
written tests against.

**New baseline: 745 passed**, MEASURED by me at the end of this file. That is 739 + 6: your three
invariant tests plus three I added. The `739 + N` arithmetic from the release note now runs from 745.

## I COMPLETED THIS MYSELF — four fixes in `strategy/order_registry.py`

### Fix 1 — `record_fill` raised on a duplicate `trade_id`. It now dedupes and returns a bool.

This is the one that mattered. You wrote, at `order_registry.py:253-254`:

> `def record_fill(self, fill: FillRecord) -> None:`
> `"""Insert a fill record. Fails if order_uuid does not exist or trade_id is duplicate."""`

and locked it in at `tests/test_order_registry.py:147-149`:

> `# 5. Duplicate trade_id is rejected by primary key`
> `with pytest.raises(sqlite3.IntegrityError):`
> `    registry.record_fill(fill1)`

Directive 3.2.2 has you re-read trades with `after=last_polled_ts - 60`, and 3.2.3 tells you not to
optimise that overlap away. A 60-second overlap on a 5-second cadence means every poll after the
first re-presents roughly twelve trades you have already recorded. Under your contract the second
poll cycle raises `IntegrityError` and the loop dies — on a process whose entire purpose is to run
unattended while the Owner is away. Directive 4.5 asks for a test proving a duplicate `trade_id`
does not double-count; you wrote a test proving it explodes instead.

`INSERT OR IGNORE` is not the fix either: it swallows foreign key violations too, and the orphan
fill that constraint exists to catch would vanish silently.

**GOOD** — the shape this needed, now at `order_registry.py:253-284`:

> ```python
> def record_fill(self, fill: FillRecord) -> bool:
>     """Record a fill idempotently. True if inserted, False if already present."""
>     with self._conn() as conn:
>         conn.execute("BEGIN IMMEDIATE")
>         already = conn.execute(
>             "SELECT 1 FROM fills WHERE trade_id = ?", (fill.trade_id,)
>         ).fetchone()
>         if already is not None:
>             conn.rollback()
>             return False
>         conn.execute(
>             "INSERT INTO fills (trade_id, order_uuid, size, price, venue_ts)"
>             " VALUES (?, ?, ?, ?, ?)",
>             (fill.trade_id, fill.order_uuid, fill.size, fill.price, fill.venue_ts),
>         )
>         conn.commit()
>         return True
> ```

The existence check is race-free because `BEGIN IMMEDIATE` already holds the write lock. A fill
referencing an unknown order still raises — that is the foreign key doing its job.

**Forward rule:** when a directive tells you a duplicate is expected, the code path for that
duplicate is a normal return value, never an exception. An exception is for a state that should not
occur.

### Fix 2 — `attach_venue_order_id` committed silently when no row matched.

You wrote, at `order_registry.py:200-220`:

> `conn.execute("UPDATE orders SET order_id = ?, status = ? WHERE id = ?", ...)`
> `conn.commit()`

No `rowcount` check. Called with a `local_id` that does not exist, this updates zero rows, commits,
and returns `None` — indistinguishable from success. At that moment the venue has accepted an order
and we hold its id, and nothing in the registry references it. It rests until it fills, invisible to
every reconcile pass. Directive 3.2.4 exists specifically so a timed-out placement is never
invisible forever; a silent zero-row UPDATE reintroduces that hole on the write side, where
reconcile cannot see it.

**GOOD** — now at `order_registry.py:216-224`:

> ```python
> if cur.rowcount != 1:
>     conn.rollback()
>     raise KeyError(
>         f"attach_venue_order_id: no order row {local_id!r} for venue "
>         f"order {venue_order_id!r}; {cur.rowcount} rows matched"
>     )
> conn.commit()
> ```

**Forward rule:** every UPDATE or DELETE that must affect exactly one row asserts `rowcount`, and
the error message names both keys. "Fail closed" applies to a write that quietly does nothing, not
only to a write that errors.

### Fix 3 — `_conn` relied on `close()` to discard an open transaction.

Your context manager had `try: yield conn` / `finally: conn.close()` with no rollback. It happens to
work — `sqlite3.Connection.close()` discards uncommitted changes — but invariant 3 is that writes
fail closed, and it was holding by a driver side effect rather than by construction. Anyone adding a
second statement inside one of those transactions would not notice the difference until it mattered.
Explicit `rollback()` on the exception path, at `order_registry.py:158-168`.

### Fix 4 — my own addition, not your omission: `CHECK` constraints and `SIZE_EPS`.

I did not ask for these, so this is not a correction.

`status TEXT NOT NULL` accepted any string. `get_active_orders` filters
`status IN ('pending', 'open', 'partial')`, so one typo — `'fillled'`, or `'OPEN'` echoed from a
venue response — writes a row that no reconcile pass will ever select. That is a resting order with
real money and nothing tracking it. Both `status` and `side` now carry `CHECK` constraints, with the
permitted values also exported as `ORDER_STATUSES`.

`SIZE_EPS = 1e-9` is now in the module because Stage 3 will ask "is this order full" by comparing
`size_matched` against `original_size`. Forty fills of 2.5 sum to 100.0 in decimal and to something
slightly under it in binary; an exact `>=` leaves a fully-filled order marked `partial` forever.
`tests/test_order_registry.py::test_size_matched_float_accumulation_needs_epsilon` demonstrates it.
Use `SIZE_EPS` for every size comparison in 3.2 and 3.3. Do not compare sizes with `==`.

## One process correction — red for the wrong reason

You wrote, under "Failing-First Output (RED)":

> `E   ModuleNotFoundError: No module named 'strategy.order_registry'`
> `!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!`

A collection error is not a failing test. It proves the module is absent, which you already knew.
It does not prove that any of the three tests exercises the invariant it is named after — a test
whose body is `assert True` produces exactly the same red. The whole value of red-before-green is
that the red comes from the assertion you are about to satisfy, and that value was not collected
here.

**GOOD** — what the RED step should have shown: the module present with stub methods that accept
their arguments and do nothing, so the failure is the assertion itself.

> ```
> FAILED tests/test_order_registry.py::test_invariant_1_local_uuid_before_order_id
>   assert fetched is not None
> E   AssertionError: assert None is not None
> FAILED tests/test_order_registry.py::test_invariant_2_size_matched_derived_from_fills
>   assert registry.get_size_matched(local_id) == 40.0
> E   assert 0.0 == 40.0
> FAILED tests/test_order_registry.py::test_invariant_3_atomic_write_fails_closed
>   assert registry.get_order(order_id_2) is None
> E   Failed: DID NOT RAISE <class 'sqlite3.IntegrityError'>
> 3 failed in 1.2s
> ```

**Forward rule:** red means an assertion failed. If the paste shows an ImportError, a collection
error, or a syntax error, you have not reached red yet — stub the interface and run again.

## Verification, MEASURED by me

    python -m pytest tests/test_order_registry.py -q
    ......                                                                   [100%]
    6 passed in 2.44s

    python -m pytest tests/ -q
    745 passed, 1 warning in 93.00s (0:01:32)

745 = 739 + 6. Your three invariant tests plus `test_attach_venue_order_id_fails_closed_on_missing_row`,
`test_status_check_constraint_rejects_unknown_status`, and
`test_size_matched_float_accumulation_needs_epsilon`.

Nothing is committed. `strategy/order_registry.py` and `tests/test_order_registry.py` are untracked
in your working tree with my fixes already in them. Pull nothing; the files are on disk. Read both
before you extend them — do not work from the versions you pasted me.

## Directive — build 3.2 and 3.3, then report once

The checkpoint is closed. Run to acceptance without stopping.

1. **`reconcile_orders()`**, directive 3.2 unchanged. `record_fill` returning `False` is now your
   dedupe signal — count those returns and report the duplicate rate from the poll transcript,
   because it is direct evidence the 60-second overlap does what 3.2.3 claims. A row moves to
   `filled` only on trade evidence. Use `SIZE_EPS` for the `size_matched` versus `original_size`
   comparison, never `==` and never a bare `>=`.
2. **Orphan adoption** binds to a `pending` row or writes `status='unattributed'` — that value is in
   the `CHECK` list, use it rather than inventing another. Where adoption fails, the `KeyError` from
   `attach_venue_order_id` is the signal to record unattributed rather than crash the loop; catch it
   at the call site and log the transition.
3. **The `poll` subcommand**, directive 3.3 unchanged: 5 s default, reconcile once on start,
   exponential backoff to a 60 s cap on 429 or 5xx with every transition logged, per-cycle status
   line, append-only event log, heartbeat, clean `SIGTERM` and `KeyboardInterrupt` exit.
4. **Remaining tests**, directive section 4 items 2 through 9. Item 5 is already satisfied by my
   `record_fill` change — extend it to two poll cycles rather than rewriting it. Restart equivalence
   reopens from disk. Backoff injects sleep and clock, no real waiting. No test touches the network.
5. **Acceptance**: `745 + N`. Report the count and the delta. Then `review`, raw output or a plain
   statement it did not run.

One more thing, mechanical: `with get_connection(path) as conn:` in your tests does not close the
connection — `sqlite3.Connection.__exit__` commits or rolls back the transaction and leaves the
handle open. It is harmless at three tests and it will not be at thirty, on Windows, where an open
handle blocks `tmp_path` teardown. Wrap with `contextlib.closing`, or close explicitly.

Boundary unchanged: `strategy/`, `tests/`, `scripts/`, `research/` on `session-66-live-exec-loop`.
No `--live`, ever, in this phase. Stage 2 stays read-only against the venue. Commit when green, and
report in the shape of directive section 6.
