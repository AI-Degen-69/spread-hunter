# 718 — noticed but not touching (Station III)

#718 settles one type: bare `REBATE` leaves `CREDIT_TYPES`, everything else in
both lists stays exactly as it is.

## Noticed but not touching

| # | Noticed | Why out of scope | Owner | When | Status |
|---|---|---|---|---|---|
| N1 | The documented `/activity` type enum also lists `DEPOSIT`, `WITHDRAWAL`, `YIELD` and `TAKER_REBATE`, which neither `CREDIT_TYPES` nor `ACTIVITY_TYPES` contains. | The issue scope is the bare `REBATE` type only; every other type is explicitly out of scope. Whether the replay should request or credit these is its own money-path decision with its own evidence. | you | — | dismissed: the operator ruled on 2026-10-20 that **no deposit or withdrawal happens during a run**, so `DEPOSIT`/`WITHDRAWAL` cannot shift `open_value` and the transfer half of this row is moot. `YIELD` and `TAKER_REBATE` stay unrequested for the same reason the `REBATE` row was settled: they are the venue's reward-program credits, graduated spread markets pay $0.00, and requesting a type nobody has ever observed would be a guess about the feed's shape. |
