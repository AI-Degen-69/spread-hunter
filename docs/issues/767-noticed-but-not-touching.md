# 767 — noticed but not touching (Station III)

Scope: #767's build. Each row is something seen while working that was left alone, and what happened to it.

| # | Noticed | Severity | Status |
| --- | --- | --- | --- |
| N1 | `ORDER BY posted_ts` ties are arbitrary: `get_orders_by_pair` (and Q3, which copies its ordering) returns an undefined row order when orders share a `posted_ts`, so "first order's condition" in `load_pair` is arbitrary on ties. Same arbitrariness existed before this change (`orders[0]`). Pinning it would change long-standing reduction semantics beyond this issue's read-narrowing scope. | low | open — needs a dedicated issue with its own equivalence test if anyone wants `posted_ts, rowid` ordering |
| N2 | Q6 carries `ts` alongside `(up_price, shares)`, one column more than the plan's projection: the pair-scoped `close.ts >= min_posted_sec` floor in `_prior_exit_shares` (#763) needs it. No behavior change; the alternative (a second timestamp read) would add a round-trip inside the exit path. | low | accepted — recorded here so Station IV need not re-derive it |
