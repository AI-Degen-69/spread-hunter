# The markout horizons — measuring the cost of being filled

This is an explanation, not a reference. `strategy/markout.py`'s module
docstring states the problem: the fleet reports rent confidently and says
nothing about the other half of

    EV/day = rent/day − expected loss from unpaired fills/day

These markets resolve in 2026–2027, so settlement P&L reads $0.00 for months
and cannot answer the question in any useful timeframe. Markout answers it
early: after we were filled, where did the price actually go? A maker who is
systematically filled just before the price moves against him is losing money
no matter how healthy the rent line looks. This document is about the
*horizons* — the four readings per fill, their columns, and the two ordering
traps their shape forces on everything downstream.

## The horizons and their columns

`strategy/config.py` holds one tuple:

    markout_horizons = (300.0, 3600.0, 21600.0, 900.0)    # 5m, 1h, 6h, 15m

Column *i* of the `markouts` table is written at horizon
`markout_horizons[i]` — `mid_h0` at 5m, `mid_h1` at 1h, `mid_h2` at 6h, and
`mid_h3` at 15m. Each is a mid measured against the fill's reference mid
(`ref_mid`), which excludes our own resting size: on the best markets we hold
a majority of the book, and a mid that included our own orders would measure
our own footprint and report it back as edge.

| column | horizon | role |
|---|---|---|
| mid_h0 | 5m | catches immediate adverse flow |
| mid_h1 | 1h | medium-term drift |
| mid_h2 | 6h | the shortest horizon on which a long-dated market plausibly repriced |
| mid_h3 | 15m | the exit counterfactual (Sessions 49–51) |

**The tuple is appended, never sorted.** A horizon maps to its column by
position; inserting the 900s in the middle would have relabeled every
existing `mid_h1`/`mid_h2` reading on the live database. The 15m read
therefore sits AFTER the 6h column while being SHORTER than it — column order
and horizon length diverge, and everything downstream must be duration-aware,
not index-aware.

## Two ordering traps that had to be fixed, not worked around

Both survived quietly while the tuple was monotonically increasing, and both
would have corrupted data the moment the 15m horizon arrived:

- **`_matured` returns drift longest-first by duration, not by column.** A
  fill with both the 6h and the 15m reading recorded must be judged on the 6h
  one — the 15m reading is the exit counterfactual, not the gate's evidence.
  The naive version returned the last column, which after the append would
  have let a 15m reading beat a matured 6h one in the quality gate.
- **`sample_due` marks a row done only when every horizon is recorded.** The
  15m matures BEFORE the 1h and 6h ones, so "done at the last tuple column"
  would have sealed the row at the 15m write and orphaned the 1h/6h readings
  forever. The fix counts "no other horizon column still unrecorded" instead.

Both iterate the row's own columns (`SELECT *`), so a pre-migration row
degrades to the horizons it has instead of erroring.

## The readers are schema-tolerant by design

`pooled_markout_neff` and `markout_stats` in `strategy/stats.py` — the
dashboard's read path — derive their longest-first index order from the
config *durations* (never hardcoded indices) and resolve the SELECTed columns
against the live schema via PRAGMA. The dashboard's read-only connection
cannot run a migration, so a not-yet-restarted fleet's 3-column database must
degrade to the 3-column read — not error. Verified: reading the live
unmigrated DB returns n=174, pooled −1.37¢/share, identical before and after
the change.

## The migration

`mid_h3 REAL` lives in both the fresh schema and `_MIGRATIONS`
(`strategy/store.py`) as an ALTER TABLE. The schema entry alone would apply
only to databases created after the change; the ALTER TABLE is what reaches
the existing `run/fleet.db` — on the fleet's next restart, and idempotently.
Until that restart, the pairs report reads `no_column` for every exit,
honestly, rather than a silent blank.

## The 15m horizon is the exit counterfactual

The 900s reading exists for one purpose: to record what Sessions 49–51
previously inferred. The exit question is "did selling now beat waiting 15
minutes?", and the answer needs a recorded mid 15 minutes after the fill.
Because the rule's exits fire at age ≈ 0, fill+900s **is** exit+15m for the
current exit population — the pairing is exact, and it is why the pair report
can join a close to its fill's markout row instead of building a counterfactual
by hand. Two boundaries are stated in the code and the report:

- a future exit at nonzero age inside the window reads fill+900s, not
  exit+900s — the exit-vs-wait arithmetic must subtract the exit's age;
- mid_h3 cannot be backfilled — exits recorded before the migration have no
  15m reading and never will.

How the recorded counterfactual is read is described in
[the pairs-rule EV report explanation](explanation-pairs-ev-report.md).
