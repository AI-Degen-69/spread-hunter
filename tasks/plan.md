# Plan: Issue #230 — Stamp resolutions onto the recorded price tape without re-recording it

## Spec (concise — Small tier, embedded per Station II)
- **Goal:** `python -m core_brain.price_tape --resolve-only` runs `refresh_resolutions()` on its own and exits, stamping the backlog (183 markets, 0 resolved as of 2026-09-15) without recording any tick.
- **Acceptance:**
  1. `_main(["--db", <path>, "--resolve-only"])` returns `0`.
  2. Printed output reports both stamped count and pending count (style matches `--status` branch).
  3. Clean binary market gets `up_wins` stamped; ambiguous (`0.5/0.5`) and still-open markets stay `None`.
  4. `store.summary().ticks == 0` after the run — no tick recorded.
- **Out of scope:** changing `refresh_resolutions()` logic, touching `poll_once`/`backfill`, `--analyse`, `/tape` page, `data/orders.db`.
- **Size:** Small — one flag + one branch in `core_brain/price_tape.py`, one test in `tests/test_price_tape.py`.
- **Type:** Code (Backend/Logic).
- **Stack:** Python, `pytest`. CLI test style follows `tests/test_stat_gate.py` (call entry point with arg list, check return code, read output via `capsys`).

## API / interface contract (locked before logic)
- Flag: `--resolve-only`, `action="store_true"`, flat style like `--status`/`--analyse`. Help text states it stamps resolutions and records no ticks.
- Branch order in `_main()`: after `store = TapeStore(args.db)`, next to `--status`/`--analyse`, before `session = _new_session()` polling section.
- Branch body: `before = len(store.tracked_tokens())` → `session = _new_session()` → `stamped = refresh_resolutions(store, session=session)` → `pending = len(store.tracked_tokens())` → single summary `print(...)` → `return 0`.
- `refresh_resolutions()` signature unchanged; `analyse()` / `replace_findings` untouched.

## Tasks
### T1 [Backend/Logic] — Register the `--resolve-only` flag
- **Files:** `core_brain/price_tape.py` (`_main`, ~line 773-780).
- **Build:** add `parser.add_argument("--resolve-only", action="store_true", help=...)` in flat style.
- **Skill for build:** `test-driven-development` (red: `--help` lacks flag / unknown arg; green: parses).
- **Verify:** `python -m core_brain.price_tape --help` shows the flag; targeted `pytest tests/test_price_tape.py -q`.

### T2 [Backend/Logic] — Add the early-return resolve-only branch
- **Files:** `core_brain/price_tape.py` (`_main`, between `--analyse` branch and `session = _new_session()`).
- **Build:** tracked-before → session → `refresh_resolutions()` → tracked-after as pending → one summary line → `return 0`. No `poll_once`/`backfill` calls.
- **Skill for build:** `test-driven-development`.
- **Verify:** manual dry check on a temp copy is FORBIDDEN on live data — use only `tmp_path` tests; targeted `pytest tests/test_price_tape.py -q`.

### T3 [Backend/Test] — End-to-end CLI test for `--resolve-only`
- **Files:** `tests/test_price_tape.py` (reuse `_store`, `_market`, `_row`, `_Session` helpers; import `_main`).
- **Build:** seed 3 markets (clean-resolving, still-open, ambiguous); monkeypatch `core_brain.price_tape._new_session` to fake `/markets` rows (`["1","0"]` clean, `["0.5","0.5"]` ambiguous, no row for open); call `_main(["--db", str(db), "--resolve-only"])`; assert rc `0`, output has stamped+pending, `up_wins` True/None/None, `summary().ticks == 0`.
- **Skill for build:** `test-driven-development` (test first — must fail before T1/T2, pass after).
- **Verify:** `python -m pytest tests/test_price_tape.py -q`, then full `python -m pytest -q` (agent-run gate).

## Improvement pass (proposed — NOT folded in silently)
- **💡 Compute `pending` by re-reading `store.tracked_tokens()` after the pass** (instead of `before - stamped` arithmetic): covers open + ambiguous uniformly from one source of truth, immune to off-by-one if venue rows shift mid-pass. Adopt / defer / drop at build.
