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
| 6 | Both pairs cost 0.95 combined, inside the 0.99 cap | `SELECT pair_id, ... FROM orders` (pair cost at post) vs `max_pair_cost = 0.99` |
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

**Named root cause (two contributors, one change cluster):**

1. **Universe narrowed by the Sep 27–Oct 1 change cluster.** The Sep 29 `#312`
   market-universe-empty fixes, the Sep 30 13:49 UTC **D12 submarket-admission trial**
   (`1c228e8`, #319), and the Oct 1 ladder trials coincide with the fade-to-stall.
   Today's fresh `runtime/pipeline.json`: 188 spread-universe → **5 eligible / 5 picked**,
   top rejection causes `carries a submarket group label` (88), `not primary`
   Moneyline/Outright or Macro/Politics (27), blocked submarket keywords (12).
2. **Survivors sit deeper.** Average queue multiple on posted quotes rose ~2453x → ~3270x,
   so per-quote fill probability collapsed alongside the ticket count.

**Deliberately not changed here:** disabling or widening the D12 admission gates would
kill or distort an approved, pre-registered experiment (#319) — that call is the
operator's (see the open question below), not a drive-by fix. No lifecycle, ranker, or
fill-rule code was touched for this addendum.

## How to verify

1. Copy `data/01_shadow.db` to a scratch path — never open or edit the original store.
2. Open the copy read-only and confirm 4 orders, 0 fills, and 2 cycle-1 `cycle_intent` rows.
3. Run the shadow statistics report against the copy — expect max queue multiple ≈7741x plus the single-cycle limitation line.
4. Run the report against a sibling-store copy — expect no single-cycle limitation.
5. Failure signs: the limitation line is missing, the multiples are blank, or `data/orders.db` was touched.
