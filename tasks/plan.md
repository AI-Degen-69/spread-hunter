# Plan — Issue #296: Port single-instance ownership lock to core_brain trader loop

Branch: `i296/port-single-instance-ownership-lock-to-corebrain` | Issue: #296

## Classification
Size: **Large** — new database table plus gates in two entry points (schema change per tier rule), though the change is additive and boring. Type: **Code** (`[Backend/Logic]`; skill `test-driven-development`).

## Spec (concise — embedded; root SPEC.md is product-level, issue spec lives here)
**Problem:** `reconcile_lock` (`order_registry.py:361`, `:844`) guards one reconcile call. Nothing owns the whole loop, so two writers on one `data/orders.db` (`order_registry.py:38`) silently sum independent inventories.

**Key design input (resolved from code, not assumed):** the supported stack is a *pair* on one DB — `scripts/spread-hunter-menu.ps1:258-259` launches `order_manager poll` (query/reconcile/sweep) beside `trader_loop --live --no-reconcile --no-sweep` (decide/execute), and `trader_loop.py:1304-1308` says so explicitly. A single blanket lock would break the supported stack. Therefore: **role-keyed slots** — one `instance_lock` table, rows keyed by role (`fleet` / `poll`); a second holder of the *same* role is refused, the designed pair coexists.

**Approach:** mirror the `reconcile_lock` pattern exactly (holder `pid:token`, `acquired_ts`, stale TTL, `BEGIN IMMEDIATE`, release only our own holder in `finally`). New `InstanceInUse(RuntimeError)` sibling — NOT a subclass of `ReconcileInProgress`, because `order_manager.py:2246` (and `:417`) catches `ReconcileInProgress` per cycle to skip, which must never swallow a startup refusal.

**Contracts:**
- `OrderRegistry.instance_lock(role, now_ms)` context manager + `_write_instance_lock(role, holder, acquired_ts)` test seam.
- `INSTANCE_LOCK_STALE_MS = 300_000` (same 5-min as `RECONCILE_LOCK_STALE_MS`, `order_registry.py:68`).
- Gates in `trader_loop.run()` (role `fleet`) and `order_manager.poll()` (role `poll`), both `once=True` and long-loop. Per-cycle heartbeat refresh of `acquired_ts`; a heartbeat that reports adoption stops the loop before it writes (eviction-stop).
- `trader_loop.main()` and the poll CLI entry map `InstanceInUse` → stderr message + exit 2.
- `run()` requires the slot whenever a real registry is present (fail closed, no duck-type fail-open). Read-only consumers (`dashboard/server.py`, `mode=ro` readers) untouched — they never take the lock.

**Out of scope:** one-shot verbs, lockfiles, dashboard changes, menu/supervisor changes, simulation code.

## CodeRabbit plan intake
No `coderabbitai` plan comment existed at plan time (only our `@coderabbitai plan` trigger) — nothing adopted, nothing rejected, nothing pending. Adopted: none. Rejected: none. Unverified: none from CodeRabbit.

## Resolved open question (needs-answers)
Q: Does the menu/supervisor already serialize starts, making the DB lock redundant? A (from code): menu has best-effort PID guards (`spread-hunter-menu.ps1:1649` "Bot stack is already running", `:1128` rehearsal guard) but no DB-level enforcement — any direct `python -m` invocation bypasses them. The lock is the hard guarantee; implemented regardless. Label `needs-answers` can be dropped at build.

## Depends graph
T1 → T2 → T3 (linear; T3's RED tests are written against T1's seam first per TDD, then turned green by T2).

## Tasks
1. `[Backend/Logic]` **DB slot (M)** — `core_brain/order_registry.py`: `instance_lock` table (`role TEXT PRIMARY KEY`-ish, `holder`, `acquired_ts`), `INSTANCE_LOCK_STALE_MS`, `InstanceInUse`, `instance_lock(role, now_ms)` + `_write_instance_lock()` seam. Files: `core_brain/order_registry.py`. Skill: `test-driven-development`. Depends on: —. Verify: T3 tests pass.
2. `[Backend/Logic]` **Gates (M)** — `trader_loop.run()`: acquire role `fleet` before the cycle loop, heartbeat per cycle, eviction-stop, release in `finally`; `main()` maps `InstanceInUse` → message + exit 2. `order_manager.poll()`: same with role `poll` at startup + poll CLI entry exit 2. Files: `core_brain/trader_loop.py`, `core_brain/order_manager.py`. Skill: `test-driven-development`. Depends on: T1. Verify: T3 + `tests/test_trader_loop.py` + `tests/test_order_registry.py` green.
3. `[Backend/Logic]` **Tests (M)** — `tests/test_instance_lock.py` (new, `tmp_path` convention per `tests/test_order_registry.py:62`): second same-role holder refused with holder+age; fleet+poll pair coexists on one DB; release on normal exit and exception; stale adoption after TTL; heartbeat keeps live holder fresh; eviction-stop halts before write; `ReconcileInProgress` per-cycle path unchanged; read-only smoke while a slot is held; exit-2 mapping for both CLIs. Prior art (reference only, unmergeable lineage): branch `feat/instance-lock-65`, commits `f29d951` + `b88dd28`. Skill: `test-driven-development`. Depends on: T1, T2. Verify: `python -m pytest tests/test_instance_lock.py tests/test_order_registry.py -q`.

Checkpoint after T2: slot + gates in place, pair coexists, second same-role refused (shown by T3 RED→GREEN).

## 💡 Improvement (adopted by default — architectural fit, evidence-based)
Role-keyed single table instead of copying the old single-slot design. Evidence: `scripts/spread-hunter-menu.ps1:258-259` — "query = `python -m core_brain.order_manager poll --interval 0.5`" beside "decide = `python -m core_brain.trader_loop --live --no-reconcile --no-sweep`" — the supported stack is two loops on one DB by design, so one blanket lock would refuse the legal pair. Adopted into the contracts above; drop only on explicit operator rejection.

## Do-not-touch (local runtime junk, untracked, never commit)
`archive/`, `books.db`, `hunter.db`, `live/`, `logs/`, `run/`, `spread-hunter/` — pre-existing local files newly visible under the new `.gitignore`. Stale pipeline leftovers of the old lineage are backed up at `C:\Users\Tiger\AppData\Local\Temp\opencode\stash-296-backup\` (old `tasks/`, `CONSTRAINTS.md`, `.collab/`, two `run/` files).
