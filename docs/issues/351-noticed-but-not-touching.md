# Noticed-but-not-touching — Issue #351 (Station IV review)

All three candidates were dispositioned by the operator at Station VI closeout on 2026-10-03 and
published as one family (head: #355). No rows remain `open`.

| ID | Candidate | Discovering station | Evidence | Status | Resolution |
|----|-----------|---------------------|----------|--------|------------|
| N1 | `not a primary Moneyline/Outright or Macro/Politics market` bucket (~32 rejects: NCAAF matchups like Alabama vs Mississippi State with no league word in venue metadata) may hide more main lines the same way the group-label veto did | IV | `runtime/market_universe.json` (199-row rank, 2026-10-03); `scoring/selector.py:identity_allowed` matchup branch | published | #355 (family head) — audit the matchup-refusal bucket before any vocabulary change |
| N2 | `cycle_intent.submitted` sums to 0 on days with hundreds of posted orders — submit accounting may not capture posting (instrumentation gap, not a fill cause) | III-B | `01_shadow_12-09_00-58.db` scratch copy: 200 intent rows, `sum(submitted)=0` on 2026-10-03 vs 580 posted orders | published | #356 — find the broken link in the submit-accounting chain |
| N3 | Expired markets (`t_remaining` ≈ −16h) still enter the scan universe and burn cycles as skips | III-B | Same store: `t_remaining -57738s < 0s` skip reasons | published | #357 — prune expired markets from the universe before ranking |
