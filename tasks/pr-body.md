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

Real output, run on a copy of `data/01_shadow_21-10_11-39.db` (1,252 cohort orders) against a
copy of `data/orders.db` (14 cohort orders, 0 fills):

```
Fill calibration: simulated (shadow) vs venue (live)

  shadow OK         n=1252  C:\Users\...\shadow_copy.db
  live   OK         n=14    C:\Users\...\live_copy.db

  minimum orders per side for a gap: 5

  overall
  bucket     side       n    posted    filled  share%  order%
  all        shadow  1252    9728.0    3095.0   31.8%   32.5%
  all        live      14      96.0       0.0    0.0%    0.0%
  gap                                          +31.8pp  +32.5pp   OK

  by price
    0.40-0.50
      0.40-0.50  shadow   212    1075.0     344.0   32.0%   31.6%
      0.40-0.50  live       6      30.0       0.0    0.0%    0.0%
    gap                                          +32.0pp  +31.6pp   OK
    0.10-0.20
      0.10-0.20  shadow   137    1364.0     270.0   19.8%   19.7%
      0.10-0.20  live       1      10.0       0.0    0.0%    0.0%
    gap                                              --      --   insufficient

  by queue ahead
    unknown
      unknown    shadow     0       0.0       0.0      --      --
      unknown    live      14      96.0       0.0    0.0%    0.0%
    gap                                              --      --   insufficient

  positive gap = shadow fills more than live
  NO_DATA / UNREADABLE inputs are reported, never repaired.
  A positive gap is not automatically model error: shadow_credit_fills
  credits every order resting above a print, so it can over-fill too.
```

`--json` emits the same numbers as a dict with `sign_rule` included. Exit `0` only when both
stores load `OK`; `1` for `NO_DATA`, `UNREADABLE`, or a store with no cohort — the report still
prints, so a readable side is never lost to a broken one.

Two things this first real run already shows, both printed rather than papered over:

- The live side is a denominator with no numerator: 14 orders, 96 posted shares, **0 fills**.
  The overall gap is therefore one-sided until #471 produces live closes.
- **Live carries no queue reading at all** (0 of 14 quote rows). Every live order lands in
  `unknown`, so the queue dimension cannot be calibrated against live data yet.

## How to verify (hands-on)

1. From the main checkout: `Copy-Item 'data\01_shadow_21-10_11-39.db' "$env:TEMP\sh.db"; Copy-Item 'data\orders.db' "$env:TEMP\live.db"`
2. `python -m core_brain.fill_calibration --shadow-db "$env:TEMP\sh.db" --live-db "$env:TEMP\live.db"`
3. Read the **gap** column: it carries a sign, and the footer states
   *positive gap = shadow fills more than live*. Shadow shows a real rate (~a third of orders
   fill); live reads `0.0%` and thin buckets read `--` with `insufficient`.
4. Repeat with `--json`; confirm the same `order_gap` / `share_gap` values.
5. Point `--shadow-db` at `data\stats_21-10_23-58_shadow-01.db` (a snapshot-only file): expect
   `UNREADABLE` naming the missing table — not a silent empty report. Then point `--live-db`
   at a missing path: `NO_DATA`, exit `1`, no file created.

Closes #474 (instrument); the measured-gap follow-up is tracked against #471.
