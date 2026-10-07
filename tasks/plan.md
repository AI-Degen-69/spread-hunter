# Plan — Issue #393: refuse quotes resting behind an unfillable queue

Branch: i393/refuse-quotes-resting-behind-an-unfillable-queue | Issue: #393
Size: Standard — 4 source files (`config.py`, `risk.py`, `markets.py`, `trader_loop.py`)
+ one new focused test file; one design decision (the bar's default mode, answered by
the operator: record-only first).
Type: [Backend/Logic]. Stack: Python 3.12, pytest. Spec embedded below; no root
SPEC.md exists in this repo (precedent #386/#390/#392).

## Spec (embedded)

Goal: a new resting bid is only worth placing if the queue ahead of it can plausibly
clear. Measure it; do not guess it.

- **Measured, not guessed.** Queue ahead = current book depth AT the new bid's own price
  (`book["bids"].get(round(price, 4), 0.0)` — the same read `live_fill_engine.py:98` and
  `shadow_fills.queue_ahead_at` use). Reachable flow = taker **SELL** prints on that token
  at or below that price inside a bounded window of the public tape.
- **Minutes to clear** = `queue_ahead / (reachable_shares / window_minutes)`. Refuse past a
  named bar; a non-zero queue with zero reachable flow is infinite minutes (never clears).
- **Record-only at ship** (operator decision, this session): the reason is reported on
  every gated placement and on the cycle feed, and nothing is dropped, until
  `HUNTER_QUEUE_CLEAR_GATE=1`. The enforcing path is the same code with `enforce=True`.
- **One built rule, not a second copy of it.** The measurement and the bar semantics
  already exist and are tested: `scoring.selector.queue_minutes_at` and
  `scoring.selector.maker_queue_allowed` (reason: `maker queue: N min to clear at our
  price >= M min bar`). `core_brain/risk.py` gains an adapter, never a duplicate rule.
- **Scope: new resting placements only**, inside `_visit_one`, after `plan_orders`.
  Crossed (FOK) intents never rest and pass through. Held orders, `intents`, `why`,
  `visit_outcome`, the refusal-grace counter and `cancel_queue_ahead` recording are
  untouched.
- **A couple never splits.** If any new passive placement in a visit is refused, every new
  passive placement in that visit is dropped with it. Fresh intents all carry
  `pair_id=None` at this point — the fresh id is minted inside `_submit_intents`
  (`trader_loop.py:1106`) — so a `pair_id`-keyed rule cannot keep a fresh couple together,
  and one resting leg with no partner is the `Unpaired` alert state in the glossary.
- **Fail open on an unmeasurable tape.** `unavailable` (request failed / payload not a
  list) or `truncated` (page limit reached before the window start) keeps today's
  behaviour and says in the reason that the gate was skipped. A *complete* window with no
  reachable sells is a real measurement and refuses.
- **Unchanged:** `core_brain/quotes.py` and `decide_quotes`, `scripts/filter_markets.py`
  and its inert `queue_bar_reject`, the shadow seam, the command-line `quote`/probe paths,
  `data/orders.db`.
- Out of scope: selection/ranking, placement pricing, post-fill management, live runs,
  wiring the shadow rehearsal's flow port.

## CodeRabbit plan intake (3-line note)

- **Adopted:** gate in `_visit_one` (its Design Choice 1 option 3), exact-price depth +
  at-or-through SELL flow, bounded window with a reported status, fail-open on an
  unmeasurable window, settings in `MakerConfig`, tests injected with `now`/`session`.
- **Rejected / corrected:** (1) a new pure rule with the marker `unfillable queue` — the
  same rule already ships as `maker_queue_allowed`/`queue_minutes_at`, and a second name
  for one concept plus `>` vs the repo's `>=` convention is drift, so risk.py adapts the
  existing pair instead (see Improvement); (2) strict `>` at the bar — the repo's caps are
  "a ceiling reached, not approached" (`maker_queue_allowed` docstring); (3) the
  `pair_id`-keyed couple rule — fresh intents carry `pair_id=None` until `_submit_intents`
  mints one, so it cannot protect a fresh couple; a visit-level block does; (4) evaluating
  the gate on the *live* run only and leaving the shadow seam unwired — accepted for now,
  recorded in Notes as a follow-up rather than smuggled into this change.
- **[UNVERIFIED]:** none. Every path, symbol, line number and default cited above was read
  in the live checkout in this session. The one number it could not verify is the 60-minute
  bar itself, which is a starting point, not a measurement (`max_queue_clear_minutes`).

## Open question (resolved — one operator answer)

The issue asks what the refuse bar is (relative vs absolute). Resolved from code: relative
— `queue_minutes_at` and `maker_queue_allowed` are that bar, already measured at our own
price, already used by the ranker.

Left genuinely open and asked (money lever, no code answer): whether the new gate ships
enforcing or record-only. The repo's own precedent for this exact rule ships record-only
(`maker_queue_allowed(enforce=False)`: "enforcing on day one refuses every market and takes
the bot silent"). **Operator answered: record-only first.** So
`enforce_queue_clear_gate: bool = False` at ship, flipping to enforcing with
`HUNTER_QUEUE_CLEAR_GATE=1`. The named reason is produced either way; only the drop is
gated by the switch.

## Interface contracts (locked before logic)

```python
# core_brain/markets.py — next to recent_trades; same _SESSION, TRADES_API, TAPE_TIMEOUT
class SellFlow(NamedTuple):
    status: str                       # "complete" | "truncated" | "unavailable"
    window_sec: float
    by_token: dict[str, dict[float, float]]   # token -> {price(4dp): taker SELL shares}

def recent_sell_flow(condition_id: str, window_sec: float, *, now: float | None = None,
                     session=None, page_size: int = 500, max_pages: int = 3) -> SellFlow: ...
```
`_SESSION.get(TRADES_API, params={"market": cid, "limit": page_size, "offset": n*page_size},
timeout=TAPE_TIMEOUT)`. Rows kept only when: side trims/case-folds to `SELL`, price and size
finite, size > 0, `now - window_sec < timestamp <= now + 60`; de-duplicated by
`(transactionHash, asset, timestamp, price, size)`. `unavailable` on exception or a non-list
payload; `complete` on a short page or when a page's oldest row reaches the window start;
`truncated` when `max_pages` is reached first. Millisecond stamps are dropped, not converted.

```python
# core_brain/config.py — MakerConfig, beside the queue-hold settings
enforce_queue_clear_gate: bool = False   # record-only at ship; HUNTER_QUEUE_CLEAR_GATE
max_queue_clear_minutes: float = 60.0    # HUNTER_MAX_QUEUE_CLEAR_MIN, _bounded_float 0.1..1440
queue_flow_window_sec: float = 1800.0    # the 30m tape the issue cites; no env var
```
`HUNTER_QUEUE_CLEAR_GATE` follows `HUNTER_ENDGAME_GATE` exactly (`0`/`false`/`off`,
case-insensitive, = off). Bad `HUNTER_MAX_QUEUE_CLEAR_MIN` raises at `load()`, never clamps.

```python
# core_brain/risk.py — adapter over the existing measured rule (never a copy of it)
def queue_clear_block(cfg, side: str, price: float, queue_shares: float,
                      reachable_shares: float, window_min: float) -> tuple[bool, str]: ...
```
Returns `(allowed, why)`; `why` is empty when the queue clears, and otherwise is the shared
`maker queue: ...` reason with the leg appended (`(UP @ 0.4700)`) so the string stands alone
in the cycle feed. `max_queue_clear_minutes <= 0` disables, as everywhere else.

```python
# core_brain/trader_loop.py
VenueSeam.flow_fn: Optional[Callable[[str, float], SellFlow]] = None   # optional port
LiveFleetResult.queue_why: str = ""          # appended; empty when clear/unmeasured
def _admit_placements(to_submit, market, up_book, down_book, flow_fn, cfg
                      ) -> tuple[list[QuoteIntent], str]: ...
```
The port is called **lazily**, only after some passive intent is found to have
`queue_ahead > 0`, and at most once per visit.

## Improvement (one, evidence-backed)

**Reuse the existing measured rule instead of writing a second one.** CodeRabbit's plan
introduces `risk.queue_clear_block` as a new pure rule with the marker `unfillable queue`
while stating, verbatim, "Minutes-to-clear = `queue_shares / (reachable_shares /
window_minutes)`. This is the same arithmetic as `scoring.selector.queue_minutes_at`." The
repo already ships that arithmetic and its bar (switchable via `max_queue_minutes <= 0`,
inf when nothing traded at our price, record-only vs enforcing) with tests in
`tests/test_maker_queue_bar.py`. Classification: **simplification → adopt-by-default**, so
`queue_clear_block` is a ~15-line adapter with a lazy `from scoring.selector import ...`
(verbatim precedent: `core_brain/shadow_exec.py:460-461`). One vocabulary ("maker queue"),
one arithmetic, one place to fix it. Rejected alternatives recorded under Notes.

## Tasks

Dependency graph: T1 ← T2 ← T3. Risk first: the reader's honesty (status) is the one
piece that can silently mislead, so it is built and proven offline before the loop touches
it.

### [x] T1 — RED: reader + gate tests, offline [Backend/Logic] (S)
Target: `tests/test_queue_clear_gate.py` (new). Fake session modelled on `_TapeSession`
(`tests/test_velocity_gate.py:16`) counting calls and returning canned pages; fake cfg
helper modelled on `_gate_cfg` (`tests/test_completable_pair_gate.py:70`); fixed `NOW`; no
network, no signer, no `data/orders.db`.
- Reader: counts only SELL rows inside the window; drops BUY rows, out-of-window rows,
  future rows and millisecond stamps; de-duplicates repeats; skips `nan`/zero/missing-side
  rows; `OSError` → `unavailable` + empty map; non-list payload → `unavailable`; quiet
  window (`[]`) → `complete` + empty map; full pages older than the window start →
  `truncated` with the session called `max_pages` times.
- Rule (record-only by default): deep queue + thin flow reports `would refuse` and
  **allows**; the same cfg with `enforce_queue_clear_gate=True` refuses; zero queue allows
  even with zero flow; non-zero queue with zero flow refuses as "never clears"; exactly at
  the bar refuses (`>=`, the repo's convention); `max_queue_clear_minutes=0` disables;
  env overrides `load()` (`HUNTER_QUEUE_CLEAR_GATE=off`, `HUNTER_MAX_QUEUE_CLEAR_MIN=15`);
  `abc`/`nan`/`inf`/`-1`/`0` raise at load.
Helper skill: test-driven-development. Depends on: none.
Verify: RED first (ImportError/AttributeError), then the T2 suites.
Checkpoint: the measurement's contract is pinned by tests.

### [x] T2 — GREEN: reader, settings, adapter [Backend/Logic] (M)
Target: `core_brain/markets.py` (`SellFlow`, `recent_sell_flow`), `core_brain/config.py`
(3 fields + 2 env mappings), `core_brain/risk.py` (`queue_clear_block`),
`tests/test_queue_clear_gate.py` (fill GREEN).
Helper skills: test-driven-development, incremental-implementation. Depends on: T1.
Verify: `python -m pytest -q tests/test_queue_clear_gate.py tests/test_maker_queue_bar.py`
Checkpoint: measurement + rule proven; loop still untouched.

### [ ] T3 — GREEN: admission in the visit, port, live wiring [Backend/Logic] (M)
Target: `core_brain/trader_loop.py` (`VenueSeam.flow_fn`, `LiveFleetResult.queue_why`,
`_admit_placements`, the `_visit_one` call site and its dry-run/log/event reporting, the
`main()` wiring `flow_fn=lambda cid, win: recent_sell_flow(cid, win)`),
`tests/test_queue_clear_gate.py` (visit-level tests through `_visit_one`, following
`tests/test_trader_loop.py`'s fake seam).
- Calls the port only when a passive intent has `queue_ahead > 0` (asserted by a counting
  fake: zero-queue visit never calls it).
- Crossed intents pass through; a refused visit drops every new passive intent in it.
- Reason travels: `queue_why` on the result, `queue_why` in the `decide`/`submit` event
  `extra`, and the dry-run log names admitted vs planned.
- Unmeasurable (`unavailable`/`truncated`) allows and says so.
Helper skills: test-driven-development, incremental-implementation. Depends on: T2.
Verify: `python -m pytest -q tests/test_queue_clear_gate.py tests/test_trader_loop.py
tests/test_market_quote.py tests/test_shadow_run.py tests/test_plan_orders_asymmetric_hold.py`
Checkpoint: feature complete and observable.

## Guardrails

- Focused suites only (named per task); full `pytest -q` is the GitHub CI merge gate.
- New behaviour has tests that fail without it (RED first), and no existing test is
  weakened, skipped or deleted to make anything green.
- No new dependencies. `data/orders.db` read-only. No live commands run from this station.
- `core_brain/quotes.py`, `scripts/filter_markets.py`, `scoring/` and the shadow run are
  not modified; `tests/test_shadow_run.py` must stay green as the proof of that.
- No `type: ignore`, no silent `except: pass`; the reader keeps its existing
  never-raise-outward contract by returning `unavailable`.

## Build log (Station III — actuals, not the plan)

- T1+T2 landed as ONE commit on purpose: the RED tests and the code that turns them
  green, so no commit on this branch carries a red suite.
- T2 verified with the focused set: `tests/test_queue_clear_gate.py`,
  `tests/test_maker_queue_bar.py`, `tests/test_completable_pair_gate.py`,
  `tests/test_endgame_gate.py` → 136 passed (38 of them new).
- Bar ceiling BUILT as `0.1..1440` (a day), not CodeRabbit's `0.1..10000`: it mirrors
  `HUNTER_ENDGAME_HORIZON_MIN`'s ceiling, and past a day the "queue" is the market's whole
  remaining life. Contract line above updated; `0` is refused at load (it is the rule's own
  disable value, so setting it by name would read as an enforced limit).
- The reader reuses `markets._SESSION` / `TRADES_API` / `TAPE_TIMEOUT` and pages with the
  same `offset` parameter `markout._default_trades_fn` uses, bounded at 3 pages.

## Notes (session memory — do not lose)

- Prior `tasks/plan.md` was Issue #392 (closed); this file now plans #393. Convention kept.
- Labels at intake: `ready-for-agent` + `needs-answers`. No `quick-fix` label, so no
  quick-fix lane was offered (Step 0C outcome 1). The one unanswerable-from-code question
  was asked and answered: **record-only first**.
- Operator-visible surface: the Trader dry run (`python -m core_brain.trader_loop --no-live
  --once`) and the dashboard's cycle feed (`runtime/cycle_events.jsonl` over SSE,
  `dashboard/server.py:2922`). The shadow run does NOT show this gate — its seam leaves
  `flow_fn` unset by design (its socket guard would turn a blocked tape read into an empty
  tape, which the honest reading is "unmeasurable"). Recorded as follow-up, not scope.
- Residual cost, accepted honestly: a gated market re-measures the tape once per rotation
  (~1/s per market) while its queue is deep. Rejected mitigation for now: a TTL cache on
  the flow read — more moving parts than the measurement it saves. Measure the reasons
  first (that is what record-only is for), then decide.
- `>=` at the bar means an exactly-60-minute queue is refused. That is `maker_queue_allowed`'s
  documented reading, not a new decision here.
- Evidence anchors (2026-10-06): 7-share orders behind $85k/$26k queue-ahead on Texas
  Senate, $3k/$6k on Kansas, against $19,279/$2,783 per 30m of window tape.
