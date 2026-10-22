Branch: i474/fill-model-calibration-cli | Issue: #474

# CONSTRAINTS.md — Step 4: fill-model calibration

Measurable boundaries for the fill-model calibration tool. Every line is checkable; a
violation blocks the PR.

## Read-only, on pain of a broken trust boundary

- Both input databases open through a `file:<abs>?mode=ro` URI with
  `sqlite3.connect(uri, uri=True)`, the pattern at `core_brain/run_scorer.py:128-131`.
- **Never construct `OrderRegistry` on an input path.** Its constructor creates and
  migrates tables — pointing it at `data/orders.db` would rewrite the production registry.
  `OrderRegistry` is allowed only in test fixtures, on a `tmp_path` file the test owns.
- **Never fall back to a read-write connection.** A query failure is a reported error
  (`UNREADABLE`), not a repair attempt.
- No `INSERT`/`UPDATE`/`DELETE`/`CREATE`/`ALTER`/`DROP` anywhere in
  `core_brain/fill_calibration.py`. No transaction is ever opened.
- `test_inputs_are_not_modified` must assert the SHA-256 of each input file is unchanged
  before and after a full load-and-report cycle.

## No writes to the venue, no money at risk

- The tool imports no client, signer, or order path. It reads two SQLite files and prints.
- No subcommand may place, cancel, or merge anything. `main()` returns an int; it never
  touches the network.

## Files

- **New:** `core_brain/fill_calibration.py`, `tests/test_fill_calibration.py`.
- **Must not be modified:** `core_brain/shadow_fills.py`, `core_brain/order_registry.py`,
  `core_brain/kpi.py`, `core_brain/run_scorer.py`, `core_brain/shadow_exec.py`,
  `core_brain/shadow_run.py`, `core_brain/trader_loop.py`, `core_brain/order_manager.py`,
  everything under `statistical_validation_run/`, and every existing test file.
- The `kpi.py` queue boundaries are **copied** into the new module, not imported and not
  changed. `kpi.py` stays the source of the words; the constants are duplicated because
  importing `kpi` drags in a report builder this tool must not depend on.

## Math contract

- Every rate is a fraction in `0..1`, never a percentage; the JSON output preserves those
  fractions. Text rendering is display-only: rates print as `%`, gaps as `pp`.
- **Gap = shadow − live.** Positive means the shadow model fills more than the venue did.
  The sign rule string `positive gap = shadow fills more than live` appears in both output
  modes.
- A rate whose denominator is zero is `None`, never `0.0`. A gap computed from a `None`
  rate, or from a bucket below `--min-orders` on either side, is `None` and renders `--`.
- Filled shares are summed from `fills.size`. `quotes.filled` is never read: attribution
  writes the whole order fill onto every duplicate quote row, so it double-counts.
- Filled shares are capped at `original_size`; an overfill sets `overfilled = True` and
  still contributes only the cap to the numerator.
- The run filter applies to `orders` only. A fill carrying a different `run_id` than its
  order still counts for that order.

## Zero regressions

- `python -m pytest -q tests/test_fill_calibration.py` green.
- Focused regression files green: `tests/test_shadow_fills.py`,
  `tests/test_order_registry.py`, `tests/test_quote_fill_attribution.py`,
  `tests/test_run_attribution.py`, `tests/test_run_scorer.py`,
  `tests/test_stat_validation_artifacts.py`.
- The full suite is the CI merge gate and is **not** run locally during the review stations.

## Anti-cheat

- No test may be skipped, xfailed, deleted, or weakened to reach green. No assertion is
  relaxed, and no `pytest.mark.skipif` is added to dodge a platform.
- No linter or config suppression. No new entry in `pyproject.toml`/`setup.cfg` to silence
  a warning this code introduced.
- **No `pytest.approx` anywhere.** Every expected value in the fixture is an exact binary
  fraction — 0.625 = 5/8, 0.375 = 3/8, 0.75, 0.5 — so equality is asserted directly. A
  tolerance would only be able to hide a wrong denominator, which is the one bug this report
  must not have.

## Dependencies

- **No new external dependency.** Standard library only: `argparse`, `dataclasses`,
  `json`, `math`, `sqlite3`, `pathlib`, `hashlib` (tests).
- No new module-level import from another `core_brain` module. This file imports nothing
  from the repo, which is what makes the read-only claim auditable in one glance.

## Performance

- A load of the largest real registry store on this machine
  (`i608_tournament-1_the-accountant-1.db`, 124 MB, 793 orders, 793 quotes, 246 fills)
  completes in under 5 seconds wall-clock; the cohort query itself measures **0.002–0.003 s**
  on every one of the ten tournament stores. No per-order query: one grouped query per database.
- Problem scale is thousands of orders. No pagination, no streaming, no caching layer.

## Deliberately out of scope (not regressions)

- No pass/fail threshold, gate verdict, or "calibrated / uncalibrated" verdict. Issue #470
  documents no threshold, so the tool reports numbers only. A future issue may add one.
- No time-window filter. Quote `ts` is seconds and order/fill timestamps are milliseconds
  (`core_brain/kpi.py:1719`); mixing them is a real bug, so the tool filters by nothing
  rather than by a unit it cannot verify.
- No writes to `data/orders.db`, and no change to how shadow fills are credited.
