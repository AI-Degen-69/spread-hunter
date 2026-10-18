# Plan — Issue #650: Wire `market_stream` and `user_stream` into the Trader loop

Branch: refactor/trader-loop-ws-streams-650 | Issue: #650
Size Tier: Large | Task Types: Code, Refactor, Telemetry

## Code Intake & Context (Station II Step 0A)

The two clients were built, tested and left unplugged. `core_brain/trader_loop.py` still
reads every book over REST (`fetch_books=full_book`, `:2749`) and learns about fills only
from the interval reconcile, so a leg that fills waits up to a full rotation before
anything reacts — the exact window a single-buy rescue has to win.

**Verified interface map (line numbers on `refactor/trader-loop-ws-streams-650`):**

| Piece | Where | Shape |
| --- | --- | --- |
| `run()` | `trader_loop.py:500-520` | `run(seam, *, interval, once, live, markets, markets_fn, sleep_fn, suspects_box, shutdown_box, spawn_guardian)`; "only loop control stays on the signature" |
| rotation order | `:612-750` | reconcile → refresh → resolved set → `order_by_exposure` → visit loop → dropped-market cancel → sweep → heartbeat → `once` break → `sleep_fn(max(0.0, interval))` |
| rotation suspects | `:688` | `res.status == "ERROR" and "book fetch error" in (res.error or "")` |
| `VenueSeam` | `:451-497` | dataclass of ports; required-port check `:533-547` includes `fetch_books` |
| visit | `:681-687` | `_visit_one(seam=, spec=, live=, cycle=, emit_fn=, stop_memory=, tail_memory=, inventory_recorder=)` |
| book read in visit | `:1703-1716` | `evaluate_market_quote(..., fetch_books=seam.fetch_books, ...)` |
| book error wrap | `quotes.py:1020-1023` | any `fetch_books` exception → `MarketQuoteError(f"book fetch error: ...")` |
| decide rows | `:1819`, `:1851` | `emit_fn(service="decide", cycle=cycle, phase="quoting", action="decide", ...)` |
| telemetry emit | `:41`, `:2741` | `cycle_stream.emit(cycle, phase, action, *, service, market_slug, reason, latency_ms, extra, ring_path, db_path, can_rotate, run_id)`; `decide_emit = partial(..., db_path=db_path)`; never raises |
| market resolve | `_fetch_market(cid)` `:2087` | HTTP `fetch_pinned_market`; specs from `_market_specs` `:2006` are `GraduatedMarket` — **no token ids** |
| `main()` | `:2690-2804` | `cfg` `:2703`, `registry` `:2726`, `client` `:2728-2738`, `seam` `:2742-2777`, `run(...)` `:2793-2800`, `except InstanceInUse` only, no `finally` |
| reconcile wiring | `_production_reconcile_fn(emit_fn)` `:2612` | returns `(client, registry, maker_address)`; bypassed under `--no-reconcile` |
| guardian | `risk_guardian.run_guardian_tick(client=, registry=, cfg=, live=, single_buy_saver_fn=)` via `:2585-2603` | sentry thread calls it every ≤1s |
| book cache | `market_stream.MarketBookCache` | `.book(token_id)` → `{token_id, bids, asks, best_bid, best_ask, malformed}` (the `full_book` shape) or raises `BookNotReady`; `.ready_tokens()`; `.drop()` |
| book stream | `market_stream.MarketStream(cache=, emit_fn=, time_fn=, ping_sec=, tick_sec=)` | `.ensure_market(cid, slug, up_token, down_token)`, `.retain_markets(cids)`, `.start()`, `.stop()`, `.connected` |
| user stream | `user_stream.UserStreamClient(emit_fn=, time_fn=)` | `.on_trade(cb)`, `.on_state(cb)`, `.start(condition_ids=None)`, `.stop()`; states `CONNECTED`, `AUTH_OK`, `RECONNECTING`, `DISCONNECTED`, `STOPPED`; `UserTradeEvent(trade_id, order_id, condition_id, token_id, side, price, size, status, fills)`; `get_user_stream_creds()` → `UserStreamCredentialsMissing` |
| shadow seam | `shadow_run.py:646` `build_shadow_seam` | `settling_inventory_fn` `:800-824` calls `settle_market(..., seen=seen, ...)`; `seen` is a per-market set of trade ids; ring emit closure `:569` (`.ring_path` `:641`), seam emit `:990` |


**Decisions where the issue's plan and the code disagree (binding for the build):**

1. The issue's plan named the guardian helper loosely. The real symbol is
   `core_brain.risk_guardian.run_guardian_tick(client=, registry=, cfg=, live=,
   single_buy_saver_fn=)`. The fill reaction calls it with the seam's own
   `client` / `registry` / `base_cfg` — no new port, no import cycle (import inside the
   function, exactly as `start_risk_guardian` does).
2. `subscribe_fn(markets)` receives the current universe once per rotation after a
   non-empty refresh; it dedupes by condition id, calls `_fetch_market` only for markets
   it has not subscribed yet, then `ensure_market(...)` + `retain_markets(cids)`. So
   subscriptions follow the universe and exited markets stop streaming.
3. **A cold cache is not a fallback trigger.** The reader raises
   `RuntimeError("book stream cold: <token>")`; `quotes.py` wraps it into
   `"book fetch error: ..."`, the string the rotation already keys suspects on — correct,
   an unstreamed market is not quotable. To stop a freshly graduated market being
   quarantined on its first visit, the subscribe port waits a bounded 5s for the new
   tokens' first snapshot.
4. **Deviation (operator-visible, added):** `--ws-books/--no-ws-books`
   (`BooleanOptionalAction`, default `None`) → effective `stream` unless `--once`, where
   it defaults to today's REST smoke path. A real-money loop needs a rollback lever and
   `--once` cannot warm a socket.
5. **Deviation (operator-visible, added):** the fill-triggered reconcile runs even under
   `--no-reconcile` (its own callable built from `_production_reconcile_fn`), per the
   issue's plan — the poll loop owns the cadence, not the record; registry dedup + lease
   make the overlap safe.
6. `book_source` / `book_read_ms` are merged into the two decide emits inside `_visit_one`
   that run **after** `evaluate_market_quote`, sourced from a `book_stats_fn` port the live
   adapter updates on every read. Shadow wires nothing, so shadow rows make no book-source
   claim (honesty over symmetry).
7. Waking the loop lives in `main()`'s `sleep_fn` (`event.wait(seconds)` then `clear()`),
   so `run()` needs no wake parameter — only `clock_fn` plus the drain port.

## Improvement Proposal (Station II Step 5)

- **Evidence:** `run()`'s work between waits is book-fetch-bound (`:1703-1716`), yet the
  loop always sleeps the full `interval` (`:728`) however long that work took; and every
  fill waits for the next rotation to be noticed.
- **Proposal (adopted):** measure the rotation with an injectable `clock_fn` and sleep
  `max(0.0, interval - work)`; drain the user-stream queue as soon as the wait returns and
  react in-loop (reconcile → visit the filled market → guardian tick). Same change the
  issue asks for, plus the timing correction that makes the shorter wait mean something.

---

## Tasks

### [x] T1 — Rotate on measured work, subscribe the universe (seam ports + loop wiring)
- **Size:** M | **Domain:** `[Backend/Logic]` | **Skill:** `test-driven-development`
- **Depends on:** none
- **Target files:** `core_brain/trader_loop.py`, `tests/test_trader_loop.py`
- **What is built:**
  1. Four optional seam fields, appended after `markets_fn_empty_is_routine` so every
     existing keyword construction keeps working (32 keyword-built constructions across
     `tests/`, none positional): `subscribe_fn: Optional[Callable[[list], None]] = None` and
     `book_stats_fn: Optional[Callable[[], dict]] = None` in T1;
     `fill_events_fn` / `fill_reconcile_fn` in T2.
  2. `run(..., clock_fn: Optional[Callable[[], float]] = None)`; `_now = clock_fn or time.monotonic`.
     Rotation start stamped before reconcile; `work = _now() - rot_t0` measured right before
     the wait.
  3. After the refresh block (`:628-651`), when `current_markets` is non-empty and
     `seam.subscribe_fn` is wired: `try: seam.subscribe_fn(list(current_markets)) except
     Exception as e: log.warning("subscribe failed: %s: %s", type(e).__name__, e)` — same
     isolation as reconcile/sweep. On a failed or empty refresh the old universe (and so
     the old subscriptions) is kept, as today.
  4. Additive ring events through `emit_fn`, both `service="decide"`:
     `rotation_complete` (`phase="quoting"`, `latency_ms=work*1000`, `extra={"markets": len(current_markets), "suspects": len(rotation_suspects)}`) emitted once per rotation before the
     `once` check so smoke runs emit it too; `ws_book_read`
     (`phase="quoting"`, `latency_ms=<read ms>`, `extra={"book_source": "stream", "token_id": ...}`)
     is T3's — emitted by the live adapter, never by `run()`.
  5. `_visit_one` merges `seam.book_stats_fn()` (when wired) into the `extra` of the two
     `action="decide"` emits after `evaluate_market_quote` (`:1819`, `:1851`); absent port →
     rows unchanged.
- **Tests (write first, watch fail):**
  - `test_run_sleeps_interval_minus_work`: injected `clock_fn` returning 0.0 then 0.4 with
    `interval=1.0` → `sleep_fn` receives `0.6`; work longer than the interval → `sleep_fn`
    receives `0.0`, never negative.
  - `test_run_emits_rotation_complete_once_per_rotation` (includes the `once=True` case).
  - `test_run_subscribes_current_universe_each_rotation`: `subscribe_fn` gets the refreshed
    list; a raising `markets_fn` keeps the previous list and the loop continues.
  - `test_run_without_new_ports_is_unchanged`: today's seam + `run()` → no subscribe call,
    no `rotation_complete` beyond the emitted set, same `LiveFleetResult` list.
  - `test_decide_row_carries_book_source_when_stats_port_wired`.
- **Verification:** `python -m pytest -q tests/test_trader_loop.py`

### [x] T2 — Drain fills after the wait and react in-loop
- **Size:** M | **Domain:** `[Backend/Logic]` | **Skill:** `test-driven-development`
- **Depends on:** T1
- **Target files:** `core_brain/trader_loop.py`, `tests/test_trader_loop.py`
- **What is built:**
  1. `fill_events_fn: Optional[Callable[[], list]] = None` and
     `fill_reconcile_fn: Optional[Callable[[], Any]] = None` seam fields.
  2. Immediately after `sleep_fn(...)` returns (never in `once` mode, never inside the
     `except KeyboardInterrupt` branch), drain the queue. Bounded dedupe:
     `deque(maxlen=512)` of `trade_id`; a repeat id is dropped silently.
  3. Per event, in this order, each step isolated:
     `emit_fn(service="decide", phase="reconciling", action="user_fill_received",
     extra={"trade_id", "order_id", "condition_id", "token_id", "side", "price", "size",
     "status", "matched"})` → `fill_reconcile_fn()` (skipped when `status == "FAILED"`) →
     one `_visit_one` for the spec whose `_cid` equals the event's `condition_id` (same
     `stop_memory` / `tail_memory` / `inventory_recorder` objects the rotation uses, a
     fresh `cycle` number, and the market skipped when it is in the resolved set) →
     `risk_guardian.run_guardian_tick(client=seam.client, registry=seam.registry,
     cfg=seam.base_cfg, live=live)`.
  4. An event whose `condition_id` is not in the universe still emits and still
     reconciles (`matched: false`), and skips only the visit.
- **Tests (write first):**
  - `test_fill_after_wait_triggers_reconcile_then_visit_then_guardian` (recording fakes;
    assert call order, not just presence).
  - `test_fill_event_is_deduped_by_trade_id` (same id twice → one reconcile, one visit).
  - `test_failed_fill_does_not_reconcile` (telemetry only).
  - `test_fill_for_unknown_market_still_reconciles_without_visit`.
  - `test_once_mode_never_drains_fills`.
  - `test_fill_reconcile_failure_keeps_the_loop_alive` (raising port → next rotation runs).
  - `test_resolved_market_fill_is_not_visited`.
- **Verification:** `python -m pytest -q tests/test_trader_loop.py`


### [x] T3 — Wire the streams in `main()`: cache reader, subscriptions, fill queue, shutdown
- **Size:** L | **Domain:** `[Backend/Logic]` | **Skill:** `test-driven-development`
- **Depends on:** T1, T2
- **Target files:** `core_brain/trader_loop.py`, `tests/test_trader_loop.py`
- **What is built:**
  1. `--ws-books/--no-ws-books` on `_parse_args` (`BooleanOptionalAction`, `default=None`);
     effective mode `a.ws_books if a.ws_books is not None else not a.once`, logged on start
     (`books=stream` / `books=rest`).
  2. Stream mode: `cache = MarketBookCache()`, `market_stream = MarketStream(cache=cache,
     emit_fn=decide_emit)`, `.start()`. `fetch_books` becomes a reader that times the read
     with `time.perf_counter()`, stores `{"book_source": "stream", "book_read_ms": ms,
     "token_id": ...}` in a mutable stats cell (surfaced via `seam.book_stats_fn`), emits
     `ws_book_read`, and returns `cache.book(token_id)`; `BookNotReady` →
     `RuntimeError(f"book stream cold: {token_id}")` (no REST retry).
  3. `subscribe_fn`: dedupe by condition id in the trader loop (the HTTP read is the
     expensive part; `ensure_market` is already idempotent per `(slug, up, down)` —
     `market_stream.py:233-263`); for each new market `_fetch_market(cid)` →
     `market_stream.ensure_market(cid, m.market_slug, m.up_token, m.down_token)`; then
     `market_stream.retain_markets([...current cids])`; then wait up to 5s for the new
     tokens to appear in `cache.ready_tokens()`. A market whose `_fetch_market` fails is
     logged and retried next rotation; it is not added to the subscribed set.
  4. REST mode keeps `fetch_books=full_book` and wires no subscribe port (unchanged path).
  5. User stream: `creds = get_user_stream_creds()` inside `try`; on
     `UserStreamCredentialsMissing` log one named line
     (`user stream disabled: credentials missing (fill-triggered reconcile off)`) and leave
     the seam's fill ports `None`. Otherwise `queue.Queue()`, `wake = threading.Event()`,
     `client.on_trade(lambda ev: (queue.put(ev), wake.set()))`, `client.on_state(...)` →
     `wake.set()` on `AUTH_OK` after a previous disconnect (the gap is reconciled at once),
     `client.start(None)` (the user channel is wallet-wide).
  6. `run(...)` call gains `clock_fn=None`, `sleep_fn=lambda s: (wake.wait(s), wake.clear())`;
     seam gains `subscribe_fn`, `fill_events_fn=lambda: drain(queue)`,
     `fill_reconcile_fn=lambda: fill_reconcile(client, registry, maker)` where
     `fill_reconcile = _production_reconcile_fn(decide_emit)` built once regardless of
     `--no-reconcile` (decision 5), `book_stats_fn=stats.get`.
  7. `finally:` around the existing `try/except InstanceInUse` → `market_stream.stop()` and,
     when started, `user_stream.stop()`. `InstanceInUse` still returns 2.
- **Tests (write first):** main is CLI-shaped, so the testable units are extracted and
  driven directly:
  - `test_parse_args_ws_books_defaults`: `--once` → REST, loop → stream,
    `--ws-books` / `--no-ws-books` override both.
  - `test_stream_reader_returns_cache_shape_and_loads_stats`: fake cache →
    `full_book` keys, `book_stats` populated, `ws_book_read` emitted with the measured ms.
  - `test_cold_cache_raises_book_stream_cold_and_never_calls_rest`.
  - `test_subscribe_dedupes_and_retains`: two rotations of the same universe → one
    `ensure_market` per market, `retain_markets` every call, no second `_fetch_market`.
  - `test_subscribe_skips_market_whose_fetch_raised`.
  - `test_user_stream_missing_creds_leave_loop_unchanged`.
  - `test_sleep_fn_wakes_on_event` (fake event with `wait` returning True immediately).
- **Verification:** `python -m pytest -q tests/test_trader_loop.py tests/test_market_stream.py tests/test_user_stream.py`

### [x] T4 — Shadow rehearsal emits simulated fills
- **Size:** S | **Domain:** `[Backend/Logic]` | **Skill:** `test-driven-development`
- **Depends on:** T2 (event shape)
- **Target files:** `core_brain/shadow_run.py`, `tests/test_shadow_run.py`
- **What is built:**
  1. In `build_shadow_seam`'s `settling_inventory_fn` (`:800-824`): snapshot
     `before = set(seen)` around the `settle_market(...)` call; for every id in
     `seen - before` emit through the seam's ring emit
     (`emit_fn(cycle, "reconciling", "user_fill_received", extra=...)` — the closure at
     `:569`, so `event.get("simulated") is True`): `extra={"simulated": True, "trade_id":
     tid, "condition_id": market.condition_id, "cycle": cycle}`.
  2. No stream clients in shadow (it holds no credentials and reads only), no
     `subscribe_fn`, no fill queue: the rehearsal shows the event shape, never a live
     socket.
  3. A settle that raised emits nothing (the `except` branch already degrades).
- **Tests (write first):**
  - `test_shadow_settle_emits_simulated_fill_for_each_new_trade`: seeded shadow store that
    fills a resting quote → exactly one `user_fill_received`, `simulated: True`, its
    `trade_id` in the emitted set; a second settle of the same book adds none.
  - `test_shadow_settle_failure_emits_no_fill`.
- **Verification:** `python -m pytest -q tests/test_shadow_run.py`


---

## Files Touched / Not Touched

**Touched:** `core_brain/trader_loop.py`, `core_brain/shadow_run.py`,
`tests/test_trader_loop.py`, `tests/test_shadow_run.py`, plus this plan,
`CONSTRAINTS.md`, `docs/issues/650-noticed-but-not-touching.md`.

**Not touched (invariants):** `core_brain/quotes.py`, `core_brain/markets.py`,
`core_brain/market_stream.py`, `core_brain/user_stream.py`,
`core_brain/risk_guardian.py`, `core_brain/cycle_stream.py`, `dashboard/**`,
`data/orders.db`, `runtime/**`.

## Risk Notes

- **Book staleness:** the cache serves the last snapshot even if the socket has been quiet —
  a stale spread is a stale quote. Accepting that is the issue's premise (the venue pushes
  on change); the mitigation is telemetry (`ws_book_read` + `rotation_complete`), not a
  silent REST re-read (CONSTRAINTS §3).
- **Reactive visit vs. cadence:** a fill-triggered visit runs outside the rotation, so one
  market may be visited twice in a second. Both visits read fresh inventory and the registry
  gates duplicates; the loop's resident instance lock still owns writes.
- **Threading:** the fill queue is `queue.Queue` (thread-safe); `wake.set()` from the socket
  thread only signals, it never touches the registry.

## How to Verify (operator, hands-on)

1. Rehearsal: `python -m core_brain.shadow_run --minutes 5 --db data/650_shadow.db` →
   in the run's ring file, every rotation carries a `rotation_complete` row with a real
   `latency_ms`, and a settled fill shows `user_fill_received` with `simulated: true`.
2. Dashboard: `python -m dashboard.server` → `http://127.0.0.1:8799` → CYCLE EVENT STREAM
   shows the new `rotation_complete` / `user_fill_received` rows; during a live loop the
   decide rows carry `book_source: stream`.
3. Smoke, unchanged path: `python -m core_brain.trader_loop --once --no-ws-books` → same
   output as before this branch (REST books).
4. Live loop (`.\scripts\spread-hunter-menu.ps1 start`): stderr shows
   `books=stream`; no repeating `book fetch error` for a market that is streaming; a fill
   is followed within ~1s by a reconcile row and a fresh decide row for that market.

