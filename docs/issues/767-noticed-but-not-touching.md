# 767 — noticed but not touching (Station III)

Scope: #767's build. Each row is something seen while working that was left alone, and what happened to it.

| # | Noticed | Severity | Status | Resolution |
| --- | --- | --- | --- | --- |
| N1 | `ORDER BY posted_ts` ties are arbitrary: `get_orders_by_pair` (and Q3, which copies its ordering) returns an undefined row order when orders share a `posted_ts`, so "first order's condition" in `load_pair` is arbitrary on ties. Same arbitrariness existed before this change (`orders[0]`). Pinning it would change long-standing reduction semantics beyond this issue's read-narrowing scope. | low | dismissed | Dismissed — #767 changed no ordering behaviour (pre-change `orders[0]` on an unsorted scan was equally arbitrary) and no field impact was observed. Unreachable as a regression while `get_orders_by_pair` stays a read-narrowing query; re-open as its own ticket with an equivalence test if a wrong pair-condition read ever appears in a run. |
| N2 | Q6 carries `ts` alongside `(up_price, shares)`, one column more than the plan's projection: the pair-scoped `close.ts >= min_posted_sec` floor in `_prior_exit_shares` (#763) needs it. No behavior change; the alternative (a second timestamp read) would add a round-trip inside the exit path. | low | accepted | Accepted as designed — the column is required by the #763 floor; no follow-up. |
