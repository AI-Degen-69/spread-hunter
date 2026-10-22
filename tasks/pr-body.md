Branch: i474/fill-model-calibration-cli | Issue: #474

# PR body — Step 4: fill-model calibration

## Summary

Adds `core_brain/fill_calibration.py`, a read-only CLI that answers one question: **how far
is the shadow fill model from what the venue actually did?**

It opens a shadow store and the live registry with `mode=ro`, takes one record per resting
maker bid (`side='BUY'` with a `quotes` row), buckets those records by price (0.10 bands) and
by queue-ahead (`kpi.py` boundaries), and prints the simulated fill rate beside the observed
fill rate with a signed gap (`shadow − live`) — twice: share fill fraction and order any-fill
rate.

- **Read-only by construction.** Both connections use `mode=ro`; `OrderRegistry` is never
  constructed on an input path; there is not one write statement in the module.
- **Honest when data is thin.** A gap is printed only when both sides clear `--min-orders`
  (default 10); otherwise the row reads `insufficient` and the counts still print.
- **Signed both ways.** A positive gap means shadow fills more than live. The model credits a
  fill to *every* order resting above a trade-through print (`shadow_fills.py:88-92`), so it
  can overshoot as easily as undershoot — the report does not claim pessimism.

## Scope note — the run itself is deferred

The issue's real deliverable is the measured gap, which requires **≥60 closes of live data**
from #471. This PR ships the instrument. Until #471 lands, the live registry has 0 fills, so
every gap honestly reads `--`. The run is a single command once the data exists.

## Files

| File | Change |
| --- | --- |
| `core_brain/fill_calibration.py` | new — loader, bucket math, text/JSON report, CLI |
| `tests/test_fill_calibration.py` | new — 24 tests over a hand-computed fixture |
| `CONSTRAINTS.md`, `SPEC.md`, `tasks/*` | planning artifacts |

Nothing existing is modified: `shadow_fills.py`, `kpi.py`, `order_registry.py`,
`run_scorer.py` and all current tests are untouched.

## Behaviour

```
$ python -m core_brain.fill_calibration --shadow-db <shadow.db> --live-db <orders.db>
fill-model calibration — positive gap = shadow fills more than live
shadow: <path>  status=OK  orders=4  run=*
live:   <path>  status=OK  orders=4  run=*

dimension  bucket     sh orders  sh share%  sh order%  lv orders  lv share%  lv order%  share gap  order gap  status
price      0.40-0.50         2      0.750      1.000          2      0.500      0.500     +0.250     +0.500  OK
…
queue      unknown           0        --         --            4      0.250      0.250        --         --  insufficient
```

`--json` emits the same numbers as a dict with `sign_rule` included. Exit `0` when both
stores load `OK`, `1` otherwise (`NO_DATA`, `UNREADABLE`, or a store with no cohort).

## How to verify (hands-on)

1. From the main checkout: `Copy-Item 'data\i608_tournament-1_the-accountant-1.db' "$env:TEMP\sh.db"; Copy-Item 'data\orders.db' "$env:TEMP\live.db"`
2. `python -m core_brain.fill_calibration --shadow-db "$env:TEMP\sh.db" --live-db "$env:TEMP\live.db"`
3. Look at the **gap** column: it carries a sign, and the footer states
   *positive gap = shadow fills more than live*. Shadow shows a real rate (~a third of orders
   fill); the live column reads `0.000` and gaps read `--`.
4. Repeat with `--json`; confirm the same `order_gap` / `share_gap` values.
5. Point `--shadow-db` at `data\stats_21-10_23-58_shadow-01.db` (a snapshot-only file): expect
   `UNREADABLE` naming the missing table — not a silent empty report. Then point `--live-db`
   at a missing path: `NO_DATA`, exit `1`, no file created.

Closes #474 (instrument); the measured-gap follow-up is tracked against #471.
