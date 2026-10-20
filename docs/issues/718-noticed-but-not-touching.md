# 718 — noticed but not touching (Station III)

#718 settles one type: bare `REBATE` leaves `CREDIT_TYPES`, everything else in
both lists stays exactly as it is.

## Noticed but not touching

| # | Noticed | Why out of scope | Owner | When | Status |
|---|---|---|---|---|---|
| N1 | The documented `/activity` type enum also lists `DEPOSIT`, `WITHDRAWAL`, `YIELD` and `TAKER_REBATE`, which neither `CREDIT_TYPES` nor `ACTIVITY_TYPES` contains. | The issue scope is the bare `REBATE` type only; every other type is explicitly out of scope. Whether the replay should request or credit these is its own money-path decision with its own evidence. | you | published | open |
