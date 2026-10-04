# Analysis: zero-fill `01_shadow` rehearsal (Issue #351)

> Evidence status (revised 2026-10-03, Station III-B): the stores **were** measured —
> on read-only scratch copies of the live folder (`data/01_shadow.db`: 4 orders / 0 fills /
> 0 closes / 2 cycle-1 intents — ticket numbers verified exactly, queues
> 2524/5092/38706/6113 at sizes 6/6/5/5). Everything below scoped to `01_shadow.db`
> is measured fact. The sibling store (`01_shadow_12-09_00-58.db`) tells a different
> story — see "Addendum: the 3-day stall" — and must not be cited as healthy proof.

## Evidence (ticket report)

| # | Fact | Confirm query (read-only, on a scratch copy) |
|---|------|-----------------------------------------------|
| 1 | 4 orders across 2 pairs (WTA Birrell/Marcink, CS2 TS7/Furia), all `open` | `SELECT count(*), status FROM orders GROUP BY status;` |
| 2 | 4 quote rows | `SELECT count(*) FROM quotes;` |
| 3 | 2 `cycle_intent` rows, both cycle 1, ~0.5 s apart | `SELECT cycle, count(*), max(ts)-min(ts) FROM cycle_intent GROUP BY cycle;` |
| 4 | 0 rows in `fills` | `SELECT count(*) FROM fills;` |
| 5 | Queue ahead 2,524 / 5,092 / 38,706 / 6,113 vs sizes 5–6 (multiples ≈420x–7741x) | `SELECT queue_ahead, size, queue_ahead/size FROM quotes;` |
| 6 | Both pairs cost 0.95 combined, inside the 0.99 cap | `SELECT pair_id, ROUND(SUM(price),4), COUNT(*) FROM orders GROUP BY pair_id;` (both rows read 0.95 over 2 legs) and `SELECT DISTINCT max_pair_cost_at_post FROM orders;` (cap at post) vs `max_pair_cost = 0.99` |
| 7 | Sibling store: 23,844 orders, 178 filled, 555 cycles (engine fills fine on long runs) | Same queries on a copy of `01_shadow_12-09_00-58.db` |

## Why zero fills is expected, not a fill-model defect

Three facts multiply:

1. **Own-price queue depth** — `queue_ahead_at()` (`core_brain/shadow_fills.py`) counts only
   bids resting at the order's exact price. The reported queues (up to 38,706 shares against a
   size-5 order, ≈7741x) mean the tape must trade thousands of shares at that exact price
   before one share reaches us.
2. **Tape-only credit** — `credit_fills()` credits a fill only from real trade tape at the
   order's exact price, after the whole queue ahead is consumed. Deep queue + thin tape at that
   price = mathematically zero fills.
3. **Single decision cycle** — `shadow_run.py` settles each market *before* decision logic runs,
   stops `--minutes` runs at a sleep boundary via `_Deadline`, and runs no final
   `settle_market()` pass after the loop. With only cycle 1 on record, posted orders never got a
   settlement pass after posting — nothing ever asked the tape whether they filled.

## Correction: `last_polled_ts == posted_ts`

The ticket read equal timestamps as "the settle pass never revisited them". From the code
(`core_brain/shadow_exec.py`, `settle_market` updates `last_polled_ts` only on the fill path),
equality is the *expected* state of any unfilled order. The valid run-length evidence is the
single-cycle `cycle_intent` span (row 3 above), not the timestamp equality.

## Unresolved: intended `--minutes` and kill cause

The ticket asks whether the run was meant to last longer and was killed after ~1 s. Unresolvable
from this checkout (no CLI log, stores absent). Default assumption: **truncated smoke run, not a
model bug**. To distinguish normal completion from a crash, check the heartbeat finished state and
see `tests/test_shadow_run_stopwatch.py`.

## Do not merge populations

`docs/issues/analysis-why-orders-dont-fill.html` reports a different population (96 / 7,427).
It is a separate measurement and is not combined with this ticket's 4-order store anywhere here.

## What landed vs what was deferred

- **Landed**: queue-multiple stats + single-cycle warning in the shadow statistics report
  (`core_brain/statistics_report.py`, helper `queue_multiple()` in `core_brain/shadow_fills.py`).
- **Proposed, not implemented**: queue-bar enforcement. Zero observed tape yields `math.inf` in
  `maker_queue_allowed` (`scoring/selector.py`), so the bar would have refused these quotes — but
  normal ranking supplies no `queue_minutes_fn` and the ranker is shared with live trading, so
  enforcement would leak rehearsal policy into live selection.
- **Future isolated path** (no shared-ranker touch): a ranker run with `--out-dir` and an explicit
  measurement callback → `shadow_run --markets-path` reading that output → a separate shadow DB.
  See `docs/superpowers/specs/2026-08-25-maker-queue-selection-bar.md`.

## Addendum (2026-10-03): the 3-day stall on the sibling store — NOT zero-fill

The operator corrected the framing: the sibling run did not end at zero. Measured on a
scratch copy of `data/01_shadow_12-09_00-58.db`: **112 closes = 87 wins + 25 losses**
(74 `shadow_merge`, 35 `single_buy_exit`, 3 `shadow_settlement`, ~85 markets) — then **no
fill and no close since 2026-09-30 11:03 UTC**, i.e. a 3-day stall, while the loop itself
is alive (`cycle_intent` cycling today, 32 distinct cycles).

Flow vs stall, measured:

| window | quotes | quoted mkts/day | fills/day | avg queue multiple |
|---|---|---|---|---|
| Sep 24–26 (flow) | ~700–1000/day | 46–54 | 11–12 | — |
| Sep 27–30 (fade) | ~500–1850/day | 29–38 | 2–6 | ~2453x |
| Oct 1–3 (stall) | ~580–920/day | 18–24 | **0** | ~3270x |

At ~0.3% historical fill rate, 0 fills on ~2,270 stalled-window quotes has p ≈ 0.001 —
this is a real stall, not variance. Orders still post (580 today); they just never fill.

**Named root cause (two contributors, one change cluster) — corrected 2026-10-03:**

1. **Universe narrowed over Sep 27–Oct 1; the standing identity rule does the excluding.**
   Today's fresh `runtime/pipeline.json`: 188 spread-universe → **5 eligible / 5 picked**,
   top rejection causes `carries a submarket group label` (88), `not primary`
   Moneyline/Outright or Macro/Politics (27), blocked submarket keywords (12). These
   rejections come from `identity_allowed` in `scoring/selector.py` — a long-standing rule
   (Sep 1 run docs already show the same rejection causes), **not** from D12.
   Correction to the first version of this addendum: D12 (`1c228e8`, #319) added *opt-in*
   paired-admission plumbing (`paired_admission_arm`); it is dormant — no admission bundle
   is active and the running loop consumes the plain ranker output. There are no "D12
gates" to pause. The fade (54 → 34 quoted markets/day from Sep 27) aligns instead with
   the Sep 29 `#312` universe-empty fixes, which tightened what the ranker admits.
2. **Survivors sit deeper.** Average queue multiple on posted quotes rose ~2453x → ~3270x,
   so per-quote fill probability collapsed alongside the ticket count.

**Chosen fix (Station III-B, same session):** narrowed the group-label veto in
`identity_allowed` (`scoring/selector.py`) to fragment-shaped labels only — spread /
handicap lines, game/map/round numbers, over/under and totals numbers, bare numeric
price bands. Bare country, candidate, party, team, and date labels now pass to the
unchanged volume/depth/spread/movement/horizon gates. Measured on a frozen 71-market
venue snapshot: identity admits 27 → 45 (+67%); true fragments (Spread -3.5, Game 1,
O/U props, price bands) stay refused. The paired-admission treatment arm exists as the
second stage if this does not restore fills. No lifecycle or fill-rule code touched.

**Live-money flag:** the ranker is shared with live trading, so this widens live
selection too. It is committed locally, unpushed, and needs Station IV review plus the
operator's go-ahead before merge.

## Addendum (2026-10-04): Order lifetime & cancel-reason instrumentation (Issue #359)

Following the discovery in `#358` that median order lifetime collapsed from ~300s to ~40s
while queue depths reached ~2,575x, the shadow statistics report (`core_brain/statistics_report.py`)
now surfaces order lifetimes and cancel reasons under `## Queue depth (shadow)`:

1. **Terminal order lifetime:** Defined as `(last_polled_ts - posted_ts) / 1000.0` in seconds for
   terminal `cancelled` and `filled` orders. The report displays median lifetime in seconds along with
   the measured count (or `n/a` when no terminal orders exist).
2. **Lifetime unknown bucket:** Non-terminal orders (`open`, `pending`, `partial`, `unattributed`),
   or orders with invalid timestamp deltas, are explicitly categorized under `Lifetime unknown`
   with a status breakdown (e.g. `open: 4`), avoiding treating unmeasured lifetimes as zero.
   Note on timestamps: `last_polled_ts` is updated upon cancellation or fill; for resting open orders,
   `last_polled_ts == posted_ts` is the expected state.
3. **Cancel reasons breakdown:** Cancelled orders are grouped by `cancel_reason` (normalizing empty
   or whitespace-only entries to `(no reason recorded)`), sorted deterministically by descending
   count and name, reporting exact count and share percentage.

This telemetry enables empirical evaluation of the queue-hold (`#360`) and dead-band (`#361`)
rehearsal experiments.

## How to verify

1. Copy a shadow store to a scratch path — never open or edit `data/orders.db` directly.
2. Generate a shadow report using `python -m core_brain.statistics_observer --mode shadow --watch <scratch-path> --run-id <id>`.
3. Open `reports/<timestamp>_shadow_<run-id>_statistics_report.md`.
4. Confirm `## Queue depth (shadow)` displays:
   - Cancelled and filled median lifetimes (or `n/a`).
   - `Lifetime unknown` count (with status breakdown when open orders exist).
   - `Cancel reasons` list with counts and percentages (including `price_moved` if present).
5. Failure signs: missing lifetime lines, unhandled status errors, or omission of `price_moved` shares.
