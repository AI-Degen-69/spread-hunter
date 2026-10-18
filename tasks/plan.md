# Plan — Issue #648: Live Market WebSocket stream engine replacing HTTP polling for quote evaluation

Branch: i648/live-market-websocket-stream-engine-replacing-http | Issue: #648
Size tier: **Standard** — 4-5 files changed (`core_brain/market_stream.py` (new),
`dashboard/static/app.js`, `tests/test_market_stream.py` (new),
`tests/js/event_stream_harness.cjs`, `tests/test_dashboard_server.py`), one
architectural decision (where a venue order-book read comes from). Top of the
Standard band; no new dependency (`websocket-client>=1.7` is already declared),
no schema change, no public API change.
Task type: **Code** (primary) + **UX / Copy** (dashboard event sentences).

## Spec (embedded — Standard work, single-issue build)

**Goal:** one long-lived public-socket engine that keeps every active market's
order book in memory, so a book read costs a dict lookup instead of two blocking
HTTP `GET /book` calls (~260 ms per visit today).

```
profit per pair = 1.00 - (avg UP price + avg DOWN price)
```

The stream must never be the reason a pair is assembled off a price that is
gone, and must never hide a dead feed: an unready book fails closed (the visit
holds its resting orders) rather than serving a stale one.

**Acceptance criteria (from the issue) — and where each one lands here:**

| # | Issue criterion | This ticket |
|---|---|---|
| 1 | `core_brain/market_stream.py` connects, processes `book` + deltas, sub-ms in-memory reads | ✅ T1–T4 |
| 2 | `trader_loop.py` consumes the stream without blocking `GET /book` in quote evaluation | ⛔ **Deferred to #650** (see below) — proven at the seam boundary in T4 instead |
| 3 | `runtime/markets.json` change → differential unsubscribe/subscribe frames, no reconnect | ✅ T3 (engine) + T4 (probe re-reads the file) |
| 4 | Stream lifecycle events in `runtime/cycle_events.jsonl`, visible in the dashboard card | ✅ T3 (emit) + T5 (plain-word sentences) |
| 5 | `python -m pytest tests/test_market_stream.py tests/test_quotes.py tests/test_cycle_stream.py -q` | ✅ T1–T5 focused checks; CI runs the full suite |

**Why criterion 2 is deferred (recorded, not dropped):** the operator's Job Guard
for this station says *"Trader Loop wiring belongs strictly to #650"*, and #650
(`[REFACTOR] Integrate Market and User WebSocket streams into Trader Loop
execution engine`) lists exactly that wiring as its In-Scope item 1, plus the
throttle purge, reactive fill handling and loop-side telemetry. Injection at
`trader_loop.py:2749` also is not a one-liner: `_market_specs` carries **no token
ids** (verified 2026-10-18), so wiring needs a `book_stream` port on `VenueSeam`,
a token-learning hook in the visit path, and start/stop lifecycle ownership —
i.e. #650's whole job. #648 therefore ships: the engine, the seam-shaped
`fetch_books` adapter, a standalone `--listen` probe (the hands-on surface the
Owner needs, same pattern as #649's `user_stream --listen`), and the dashboard
sentences. #650 flips `fetch_books=full_book` in `main()` to `stream.fetch_books`.

**Edge cases this plan must survive:**
- a `book` snapshot arriving mid-book must **replace** both sides, never merge;
- a delta with size `0` removes that price level (a removed level must not stay
  best bid);
- unknown frame types (`last_trade_price`, `tick_size_change`, `best_bid_ask`)
  are ignored, never merged;
- a garbage level (non-numeric price/size) invalidates that token only;
- socket closed → every book drops (no stale price survives a dead feed);
- socket alive but silently dead → the liveness window expires and the socket is
  closed deliberately to force the reconnect path;
- a market whose subscribe silently failed → snapshot timeout → one resend, then
  one controlled reconnect (token stays not-ready the whole time);
- a token not in the universe is never subscribed, and its book is dropped on
  unsubscribe;
- reads never block: no waiting inside `fetch_books`.

**Out of scope:** `trader_loop.py` / `VenueSeam` / `shadow_run.py` (all #650),
user/private stream (#649), pricing math (`quotes.py`), hard-stop exit reads
(`single_buy_saver.py`), any HTTP fallback inside the read path, `SPEC.md`
(project-level, owned by other tickets — this issue's spec is embedded above).

## CodeRabbit plan intake (read once, costed once)

Read from `gh issue view 648 --json comments` (comment 2, ~81 KB; the HTML echo
below the rendered copy was ignored).

- **Adopted:** the four-task engine split (cache → socket → dynamic
  subscriptions → events); `price_change` semantics (BUY→bids, SELL→asks, size 0
  removes) **reusing `markets.parse_book`** so stream output matches REST shape
  exactly; the fail-closed `BookNotReady` read contract; liveness window
  (≈30 s / three missed heartbeats); snapshot-timeout resend then one controlled
  reconnect; send-lock serialising worker/heartbeat/probe sends; backoff reset
  after a successful open; `phase="market_stream"` + `service="market_stream"`
  routing to the live ticker family without rotating the ring; throttled tick
  summary (never one event per frame); emit calls kept outside the cache lock.
- **Rejected:** its Task 2.1/2.2/2.3 (live `VenueSeam` injection, `--book-source`
  on both CLIs, shadow opt-in) → #650's scope, per the Job Guard. Its
  `best_bid`/`best_ask` cross-check as an *invalidator*: the venue's exact
  semantics for those per-entry fields could not be verified from the docs, and
  the failure mode of a wrong comparison is a resubscribe/reconnect storm on a
  live money path. Kept as an **observational counter only** (`level_gap` in the
  tick summary) so the theory can be checked against real traffic before any
  corrective action is built. Its `ws_*` action names → renamed to the issue's
  own `market_stream_*` names. Its read-side latency telemetry "record real
  in-memory read timings in cycle telemetry" → folded into the tick summary's
  `read_us_p50` (loop-side telemetry belongs to #650).
- **Still `[UNVERIFIED]` (build-time checks, not assumptions):**
  - the documented `{"assets_ids": [...], "operation": "subscribe"|"unsubscribe"}`
    frames actually update the outcome set on a live socket. `live_marks.py`'s
    hand-observed 2026-10-12 note ("a second subscribe on the open socket answers
    INVALID OPERATION and streams nothing") was measured with the **initial**
    frame shape `{"assets_ids": [...], "type": "market"}` re-sent, not with the
    documented `operation` form — so it does not falsify the `operation` frames.
    T3 sends only `operation` frames and the P3 probe confirms it on the wire;
    if the venue ignores them, the affected token stays not-ready (fail closed)
    and the probe prints the timeout — a diagnosed hold, not a stale quote.
  - whether a `price_change` entry's `best_bid`/`best_ask` are per-token
    post-change values (the basis of the observational `level_gap` counter).

## Resolved from code (this session, 2026-10-18) — no open questions for the operator

1. **`emit_cycle_event()` does not exist.** The issue cites
   `core_brain/cycle_stream.py:emit_cycle_event()`; the real function is
   `cycle_stream.emit(cycle, phase, action, *, service, market_slug, reason,
   latency_ms, extra, ...)`, imported elsewhere as
   `from core_brain.cycle_stream import emit as _emit_cycle_event`
   (`trader_loop.py:41`, `order_manager.py:2204`). The engine calls `emit`
   directly with `service="market_stream"`, `phase="stream"`, `cycle=0`
   (the same `cycle=0` convention `fill_recorded` uses).
2. **The ring is a 500-line shared window** (`cycle_stream.MAX_LINES = 500`,
   `KEEP_LINES = 400`) already churning at ~1 event/leg/rotation — hence the
   rate-limited tick summary, not one event per frame.
3. **`VenueSeam.fetch_books` is two-arg** (`fetch_books(clob_host, token_id)`,
   `trader_loop.py:465`, called at `quotes.py:1020-1021`) — the adapter matches
   it, which is why no `quotes.py` change is needed.
4. **The visit reads only three book fields.** The whole productive path uses
   `book["token_id"]` / `book["best_bid"]` / `book["best_ask"]`
   (`trader_loop.py:1722-1726`, `:1384-1385`; `quotes.py:195,368,793,830`);
   depth ladders come from `single_buy_saver` against the live SDK client, and
   `queue_ahead` from the registry (`trader_loop.py:2318`). Level maps are still
   kept, because best bid/ask can only be derived from them.
5. **Nothing to purge in the quoting cycle.** No artificial visit delay, network
   wait, or HTTP-shaped throttle exists: the only sleeps are the rotation
   `sleep_fn(interval)` (`trader_loop.py:736`), the resolution sweeper's
   off-critical-path `time.sleep(max(30.0, interval))` (`:2532`), and
   `BOOK_TIMEOUT` inside `full_book`. The cycle-time win comes only from not
   calling `/book` twice per visit. #650 owns the throttle work; the real
   remaining per-visit HTTP call is `fetch_pinned_market` (one per market per
   rotation), which this issue does not touch.
6. **The hard-stop invariant is structural, not incidental.** Live exits go
   through the SDK client (`single_buy_saver.py:994-995`,
   `create_and_post_market_order`) and `complete_pair` uses
   `client.get_order_book()` (`:1071`); the shadow rehearsal passes
   `book_fn=shadow_fetch_books` (`shadow_run.py:815,845,1242,1208`). None of them
   go through `VenueSeam.fetch_books`, so leaving those files alone *is* the
   invariant — pinned by a new guard test (T4).
7. **A reusable protocol/cache precedent already exists** (`core_brain/live_marks.py`:
   same URL, same initial subscribe frame, level maps, corrupt-frame drop,
   backoff, `HUNTER_MARKS_SOURCE`) and a reusable offline test double
   (`tests/test_live_marks.py:151-171` stubs `websocket.WebSocketApp`). This plan
   copies that pattern rather than inventing a new one — and leaves `live_marks.py`
   untouched, because it ignores `price_change` frames (it reads only top-level
   `bids`/`asks`) and its wanted-set sync deliberately closes the socket.
8. **`runtime/markets.json` rows carry no token ids** (verified in the file and in
   `market_feed.GraduatedMarket`): only `cid`, slug, title, sizes. Tokens come
   from `markets.fetch_pinned_market(cid)`, which is what the probe uses.
9. **Stale planning set replaced.** `tasks/plan.md`, `tasks/todo.md` and
   `CONSTRAINTS.md` all belonged to **#571** (merged) and were replaced wholesale.
   `SPEC.md` is likewise issue-scoped (#632) and is **not** touched here: this
   issue's spec is embedded above, and rewriting a project doc would only
   duplicate it.

## Interface contract (locked before build)

```python
# core_brain/market_stream.py
MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
SERVICE = "market_stream"                 # cycle_stream.emit service
PING_TEXT = "PING"                        # application-level heartbeat frame
PING_INTERVAL_SEC = 10.0                  # venue contract: PING every 10 s
LIVE_WINDOW_SEC = 30.0                    # no inbound frame -> connection suspect
SNAPSHOT_TIMEOUT_SEC = 10.0               # subscribed token with no snapshot
SNAPSHOT_RESEND_LIMIT = 1                 # resends before one controlled reconnect
RECONNECT_MIN_SEC / RECONNECT_MAX_SEC = 1.0 / 30.0
TICK_SUMMARY_SEC = 10.0                   # rate limit for market_stream_tick
READ_LATENCY_BUDGET_SEC = 0.001           # the sub-millisecond invariant

class BookNotReady(RuntimeError): ...     # fail-closed read error

class MarketBookCache:                    # thread-safe, one lock
    def apply_frame(self, msg: dict | list) -> list[str]
    def book(self, token_id: str) -> dict            # parse_book shape, raises BookNotReady
    def drop(self, token_ids) -> None
    def clear_all(self) -> None

class MarketStream:
    def __init__(self, *, cache=None, emit_fn=None, time_fn=time.time,
                 ping_sec=PING_INTERVAL_SEC, tick_sec=TICK_SUMMARY_SEC)
    def start(self) -> None
    def stop(self) -> None
    def ensure_market(self, condition_id, slug, up_token, down_token) -> None
    def retain_markets(self, condition_ids) -> None
    def fetch_books(self, clob_host: str, token_id: str) -> dict   # the seam adapter
    @property
    def connected(self) -> bool
```

Cycle-stream events (all `service="market_stream"`, `phase="stream"`, `cycle=0`):

| action | `extra` |
|---|---|
| `market_stream_connected` | `url`, `tokens` |
| `market_stream_reconnect` | `delay_sec`, `close_code`, `reason` |
| `market_stream_subscribe` | `slug`, `tokens` |
| `market_stream_unsubscribe` | `slug`, `tokens` |
| `market_stream_snapshot` | `slug`, `token` |
| `market_stream_error` | `reason` (`snapshot_timeout`, `level_gap`, `frame_error`) |
| `market_stream_tick` | `books`, `frames`, `level_gap`, `read_us_p50` |

CLI (the owner's hands-on surface):

```
python -m core_brain.market_stream --listen [--markets runtime/markets.json]
       [--max-markets 2] [--tokens a,b] [--minutes 3] [--refresh-sec 10]
```

## Tasks

Dependency graph: **T1 → T2 → T3 → {T4, T5}**. Risk-first: the frame semantics
(T1) and the socket/liveness contract (T2) are the parts that can be wrong
cheaply; the probe and the copy are the safe tail.

---

### T1 — [x] Book cache and frame folding (S/M, [Backend/Logic], files: `core_brain/market_stream.py`, `tests/test_market_stream.py`)
**Build:** `MarketBookCache` + `BookNotReady` + frame folding. `book` frame →
`markets.parse_book({"bids":…, "asks":…}, token_id)` result stored as the new
book (replace, never merge). `price_change` frame → walk `price_changes`:
`side == "BUY"` updates bids, `"SELL"` asks, `size == 0` deletes the level,
rebuild `best_bid`/`best_ask` and the stored token snapshot. Unparseable level →
invalidate that token only. Unknown `event_type` ignored. `level_gap` counter
increments when an entry's `best_bid`/`best_ask` (when both present) disagree
with the rebuilt book at 4 dp — observational, no action. `book()` raises
`BookNotReady` when the token is unknown or not ready, and **never** waits.
`Depends on:` none.
**Verify (TDD, offline):** `tests/test_market_stream.py::test_book_snapshot_matches_parse_book_shape`,
`…::test_price_change_buy_and_sell_update_the_right_side`,
`…::test_zero_size_removes_a_level_and_moves_best_bid`,
`…::test_multi_entry_price_change_frame`,
`…::test_unknown_event_type_is_ignored`,
`…::test_corrupt_level_invalidates_that_token_only`,
`…::test_unknown_token_raises_book_not_ready`,
`…::test_read_latency_p50_is_under_a_millisecond` (median over 200 reads).

### T2 — Socket worker, heartbeat, liveness, reconnect (M, [Backend/Logic], files: `core_brain/market_stream.py`, `tests/test_market_stream.py`)
**Build:** `MarketStream.start/stop` on a daemon thread running
`websocket.WebSocketApp` (module imported lazily inside the thread, same loud
missing-package log as `live_marks.py`); `on_open` sends the initial
`{"assets_ids": [...], "type": "market"}` for every wanted token; a maintenance
loop sends `PING` every `ping_sec`, treats `PONG`/any inbound frame as liveness,
and closes the socket deliberately when `LIVE_WINDOW_SEC` lapses; `on_close`
clears **all** books, records the close code, and reconnects with capped
exponential backoff (reset after a successful open); `stop()` sets the event,
closes the socket, joins with a timeout. All sends serialised by one send lock.
`Depends on:` T1.
**Verify:** `…::test_on_open_sends_the_initial_subscription`,
`…::test_heartbeat_sends_ping_text_frames`, `…::test_pong_keeps_the_connection_ready`,
`…::test_a_silent_socket_is_closed_by_the_liveness_window`,
`…::test_close_drops_every_book_and_schedules_a_reconnect`,
`…::test_backoff_grows_and_resets_after_a_successful_open`,
`…::test_stop_joins_the_worker_and_the_heartbeat`.

### T3 — Dynamic universe + telemetry (M, [Backend/Logic], files: `core_brain/market_stream.py`, `tests/test_market_stream.py`)
**Build:** market map `cid → (slug, up, down)`. `ensure_market(...)` sends
`{"assets_ids": [up, down], "operation": "subscribe"}` for a new market (or
replays it in the next `on_open` when no socket is open) and emits
`market_stream_subscribe`; `retain_markets(cids)` sends
`{"assets_ids": [...], "operation": "unsubscribe"}`, drops those books, and emits
`market_stream_unsubscribe`; neither touches other books nor the socket. A
subscribed token with no snapshot for `SNAPSHOT_TIMEOUT_SEC` gets one
`operation` resend then **one** controlled reconnect (close → redial), emitting
`market_stream_error` with `snapshot_timeout`; the token stays not-ready.
`emit_fn` is injected and called with the `cycle_stream.emit` keyword shape,
outside the cache lock; emit failures never propagate into the thread. One
`market_stream_tick` summary at most every `tick_sec` (books, frames, level_gap,
read_us_p50). `Depends on:` T2.
**Verify:** `…::test_ensure_market_subscribes_only_the_new_pair`,
`…::test_ensure_market_replays_on_the_next_open_when_offline`,
`…::test_retain_markets_unsubscribes_and_drops_only_the_removed_books`,
`…::test_a_market_swap_keeps_the_socket_and_every_other_book`,
`…::test_snapshot_timeout_resends_once_then_reconnects_once`,
`…::test_lifecycle_events_carry_the_documented_actions_and_service`,
`…::test_tick_summaries_are_rate_limited`,
`…::test_emit_failure_never_kills_the_stream_thread`.

### T4 — Seam adapter, guard test, and the operator probe (M, [Backend/Logic], files: `core_brain/market_stream.py`, `tests/test_market_stream.py`, `tests/test_quotes.py`)
**Build:** `fetch_books(self, clob_host, token_id)` returns the cached book (host
ignored) or raises `BookNotReady`. A `__main__` block implements `--listen`
(resolve up to `--max-markets` rows of `--markets` through
`core_brain.markets.fetch_pinned_market`, or `--tokens` directly; subscribe;
re-read the file every `--refresh-sec` and call `ensure_market`/`retain_markets`;
print one line per lifecycle event and per tick summary with the measured
read latency; `--minutes` bounded; emitter bound to `cycle_stream.emit`).
`Depends on:` T3.
**Verify:** `…::test_fetch_books_matches_the_venue_seam_signature`,
`…::test_a_seam_built_on_the_stream_never_calls_full_book` — drive
`quotes.evaluate_market_quote` with `fetch_books=stream.fetch_books` while
`core_brain.markets._SESSION.get` is monkeypatched to raise, and assert intents
come back from the cache — plus
`…::test_single_buy_saver_never_reaches_the_stream` (source-read guard pinning
the locked invariant) and
`…::test_probe_parses_the_markets_file_and_bounds_its_run`.

### T5 — Dashboard sentences + parked-items note (S, [UX / Copy], files: `dashboard/static/app.js`, `tests/js/event_stream_harness.cjs`, `tests/test_dashboard_server.py`, `docs/issues/648-noticed-but-not-touching.md`)
**Build:** one `if (svc === 'market_stream')` block in `buildStreamSentence`
(after the trade branches, before the `EVENT_TRANSLATIONS` lookup) mapping the
seven actions to plain sentences with no market clause — the events are
engine-level, so the table's `(${market})` suffix would print "a market":
connected / reconnecting (with the delay) / subscribed (token count) /
unsubscribed / snapshot received (slug) / stream problem (plain reason) / tick
summary (books + read latency). Every interpolated value goes through `esc()`.
The parked-items file lists what moved to #650 and #649.
`Depends on:` T3.
**Verify:** harness cases added to `tests/js/event_stream_harness.cjs` for the
seven sentences (asserted from `sentences` in the emitted JSON) plus
`tests/test_dashboard_server.py::test_app_js_translates_market_stream_events`.

## Checkpoints

- **CP0** (after T1): the cache matches REST shape and refuses unready reads.
  One-line progress note, no pause.
- **CP1** (after T3): the engine runs end to end against a stubbed
  `WebSocketApp` — subscribe, snapshot, delta, swap, liveness, reconnect, events.
- **CP2** (after T5): every focused check green, the probe run hands-on once
  against the live socket, and the dashboard card showing the events.

## How to verify (owner, hands-on — no test commands)

1. `python -m dashboard.server` in one terminal.
2. `python -m core_brain.market_stream --listen --minutes 3` in another.
   Expect a printed line per event and, every ~10 s, a tick summary line
   carrying the number of books held, frames applied, and the read latency
   (sub-millisecond).
3. Open `http://127.0.0.1:8799` → **Trades & Positions** → **LIVE EXECUTION &
   CYCLE EVENT STREAM**. Expect, in plain words: the stream connecting to the
   venue socket, one subscribed line per market with its token ids, a snapshot
   line per market with its slug, and a tick summary every ~10 s. With the
   default `--markets runtime/markets.json` the probe subscribes the first two
   graduated markets.
4. While it runs, edit `runtime/markets.json`: copy another row's `cid` and
   `slug` over one of the two rows the probe picked (the file carries no token
   ids — the probe resolves them). Within `--refresh-sec` the card must show the
   old pair unsubscribing and the new pair subscribing, with **no** connected
   line in between — the socket stayed up and every other book kept ticking.
5. Stop the probe (Ctrl-C): expect the terminal to exit cleanly and the card to
   stop.

## Risks and honest limits

- The `operation`-frame path is documented but not yet observed by this repo on
  the wire (see `[UNVERIFIED]`); if the venue ignores those frames, the new
  market holds (fails closed) and the probe prints snapshot timeouts — visible,
  not silent, and `retain_markets`/`ensure_market` can then move to the
  close-and-redial path `live_marks.py` already proves.
- Quoting latency does not drop in this ticket: `trader_loop.py` still reads HTTP
  books until #650 wires the seam. The sub-millisecond claim is proven at the
  adapter/probe level here.
- The observational `level_gap` counter is unproven theory kept deliberately
  inert; if it stays at zero in real traffic it can be deleted, and if it moves
  it is the evidence a follow-up needs (recorded in the parked-items file).
