# #798 close-cost-basis audit — out-of-scope notes

Findings observed while building the audit that belong to other owners. Nothing
here is a repair made by the audit; the audit reads and reports only.

## 1. Re-run after #795 merges (acceptance evidence)

Owner: agent (next issue pass). When: as soon as #795 lands on `main`.
Re-run `python scripts/audit_close_cost_basis.py --db data/<run>.db` against
both `01_shadow_23-10_20-16.db` and `01_shadow_prudent_23-10_20-17.db` and
expect zero violations. Today the five conditions flag (+6.70, +3.00, +6.70,
+1.50, +3.10); they must not be repaired by hand and the audit must not be
"greened" before #795.

## 2. Pair-level attribution is impossible today

Owner: agent, only if repeat violations recur after #795. When: next
retrospective. `closes` carries no `pair_id`, so the audit holds at the
per-condition invariant. If a condition hosts several pairs over its life, a
per-pair over-basis can hide inside an under-basis condition total. A future
schema note on `closes` would allow a per-pair audit.

## 3. Run databases are hot while audited

Owner: operator. When: next run cycle. Both run databases were still being
written while audited (hot `-wal`), so any audit is a snapshot read under a
read-only connection, consistent but possibly seconds behind the run. For a
final verdict on a finished run, snapshot after the run has stopped.

## 4. Legacy rescue spellings carry the invariant forward

Owner: none (informational). `naked_exit` and `ladder_exit` are legacy
bucket members of `RESCUE_METHODS` with no current writer. The audit imports
the live set, so historical rows under those names are still audited —
correct by construction; do not widen the filter by hand.
