Branch: i474/fill-model-calibration-cli | Issue: #474

# SPEC — Fill-model calibration report (#474)

## Goal

Read a shadow database and a live registry, and report the **simulated** fill rate beside
the **observed** fill rate per price bucket and per queue bucket, with a signed
calibration gap. The number answers one question: how far is the shadow fill model from
what the venue actually did.

## Who reads the output

The operator, after #471 has produced at least 60 closes of live data. Before that, every
gap is `--` because the live side is thin — and the report says so instead of printing a
number built on three orders.

## Acceptance criteria (from the issue)

- [ ] A read-only report command ingests a shadow DB and a live registry.
- [ ] It reports simulated vs observed fill-rate with a signed calibration gap.
- [ ] A unit test seeds known fills and asserts the gap math; it fails without the tool.
- [ ] Runnable: `python -m pytest -q tests/test_fill_calibration.py`.

## The two measurements

| Measure | Formula | What it answers |
| --- | --- | --- |
| Share fill fraction | filled shares ÷ posted shares | How much of the capital we posted came back filled. Matches the `kpi.py` unit. |
| Order any-fill rate | orders with ≥1 fill ÷ orders | How many posts worked at all, regardless of size. Stops one large fill from hiding a low fill count. |

Both are reported per bucket, with one gap each. Neither is "the" answer; a model that
fills rarely but fully shows a low order gap and a high share gap.

## The cohort (one record per order)

A **resting maker bid**: an `orders` row with `side = 'BUY'` that has at least one row in
`quotes` for its `local_id`. That is the set `credit_fills` models — it credits only
resting orders.

| Included | Why |
| --- | --- |
| `cancelled` with no fill | Stays in the denominator: the rate means "filled before cancel". |
| Still `open` | Same reason. It had less time and the report shows it rather than hiding it. |
| `partial` | Contributes its filled shares. |
| A fill carrying another `run_id` than its order | The order owns its fills; the run filter is an order filter. |

| Excluded | Why |
| --- | --- |
| `SELL` orders | The strategy posts none; they would pollute the denominator. |
| A BUY with no `quotes` row | Not a rest. In shadow that is a **taker completion buy**, which fills by construction; comparing it to a resting bid is comparing nothing. |
| `original_size <= 0`, `price <= 0`, `price >= 1` | Not a real post. |
| Rows outside `--…-run-id` when that flag is given | Explicit cohort selection. |

## Buckets

- **Price:** 0.10-wide bands over `0..1`, `lo <= price < hi`, top band `[0.90, 1.00]`
  inclusive of 1.00. Labels `0.40-0.50`. Only non-empty buckets are printed, on either side.
- **Queue:** the `kpi.py:1723` boundaries, labels copied verbatim from
  `kpi.py:1731` — `0-1`, `1-50`, `50-150`, `150-400`, `400+`. A `queue_ahead` of `None`
  is the separate bucket `unknown`, never `0`.
- The queue value comes from the **first** quote row per `local_id` (lowest row `id`).

## Edge cases

| Case | Behaviour |
| --- | --- |
| Input path missing | status `NO_DATA`, exit code `1`, the path is not created. |
| Input has no `orders`/`quotes`/`fills` table | status `UNREADABLE` with the SQLite error text, exit code `1`. No migration, no repair. |
| Store loads, zero cohort orders | status `NO_DATA`, exit code `1`. |
| Bucket present on one side only | other side summarises to 0 orders; gap `None`; row status `insufficient`. |
| Either side below `--min-orders` (default 10) | gap `None`, row status `insufficient`. |
| `original_size` 0 or negative | excluded at load. |
| Fills exceed posted size | capped, `overfilled` flag set, order counts as filled once. |
| Duplicate quote rows for one `local_id` | first row only; filled shares still from `fills`. |
| Live `queue_ahead` is NULL (the normal case) | whole live side lands in `unknown`. Queue rows then read `insufficient` — honest, not a bug. |
| Both sides thin | `overall` also `insufficient`; the counts are still printed so the operator sees how thin. |
| Shadow fills are binary | In all ten tournament stores, 0 of 7,382 cohort orders carry a partial fill and none overfills. On the shadow side the share rate therefore equals the order rate exactly; the two measures diverge only on the live side, where partials do occur. Both are still reported — the fixture's partial is there to prove the summing path works. |

## Out of scope

- Making the fill model more optimistic. `core_brain/shadow_fills.py` is untouched.
- Any live execution. The tool cannot spend money; it reads two files.
- A calibrated/uncalibrated verdict or threshold: #470 defines none, so inventing one
  would be an unbacked claim.
- PnL per bucket, per-market breakdowns, time windows, charts, dashboard wiring.
- The real calibration run itself: gated on #471 producing ≥60 live closes (see
  `tasks/plan.md` → Follow-up).
