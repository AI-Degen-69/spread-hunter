# CONSTRAINTS.md — Issue #648: Live Market WebSocket stream engine

Locked in Station II (`ii-plan-issue`). The build (Station III) must respect every
line below. Changing a constraint requires an explicit operator decision, recorded
at the bottom of `tasks/plan.md`.

## 1. Protocol invariants (venue contract, from the published docs)

- Endpoint: `wss://ws-subscriptions-clob.polymarket.com/ws/market` (public, no
  auth headers, no credentials anywhere near this module).
- Initial subscription frame, sent on every (re)connect:
  `{"assets_ids": ["<token>", ...], "type": "market"}`.
- Differential frames for a live socket: `{"assets_ids": [...], "operation":
  "subscribe"}` and `{"assets_ids": [...], "operation": "unsubscribe"}`.
  **Never** re-send the initial `{"type": "market"}` frame on an open socket:
  `live_marks.py` hand-observed the venue answering INVALID OPERATION and then
  streaming nothing for that connection.
- Application-level heartbeat: text frame `PING` every 10 s; the venue replies
  `PONG`. A `PONG` (or any inbound frame) refreshes liveness; nothing inbound for
  30 s closes the socket deliberately so the reconnect path runs.
- Frames to handle: `book` (full snapshot — **replace** both sides) and
  `price_change` (`price_changes`: `BUY` → bids, `SELL` → asks, `size == 0`
  removes the level). Every other `event_type` is ignored.
- Room for the socket package: `websocket-client>=1.7`, already declared in
  `requirements.txt`; imported lazily inside the worker with a loud warning when
  missing (never a silent dead feed).

## 2. Zero regressions

- Targeted suites covering touched code must pass after the change:
  `tests/test_market_stream.py` (new), `tests/test_quotes.py`,
  `tests/test_cycle_stream.py`, `tests/test_live_marks.py` (proves the marks feed
  was not disturbed), the event-stream tests in
  `tests/test_dashboard_server.py`, and `node --check dashboard/static/app.js`.
- Broader sweep before the PR: `tests/test_market_quote.py`,
  `tests/test_hard_stop_fresh_exit.py`, `tests/test_trader_loop.py`.
  The full `python -m pytest -q` suite is the GitHub CI merge gate (Ubuntu +
  Windows), not a local loop.
- Every changed behaviour needs a test that fails without the change.

## 3. Read contract — fail closed, never stale, never blocking

- `fetch_books(clob_host, token_id)` returns a `parse_book`-shaped dict
  (`token_id`, `bids`, `asks`, `best_bid`, `best_ask`, `malformed`) or raises
  `BookNotReady`.
- Not ready means: unknown token, no snapshot on the current connection, socket
  closed, or the 30 s liveness window expired. A not-ready token must produce the
  error, **never** a stale or empty book, **never** an HTTP `GET /book` fallback,
  and **never** a wait inside the read.
- A closed socket drops **every** book (`clear_all`): a dead feed must not keep
  serving prices.
- `markets.parse_book` is reused for snapshot folding, so a stream book and a REST
  book are the same object shape. Do not re-implement level parsing.
- Latency invariant: median local read < 1 ms, asserted over 200 reads in the
  test suite. No new locking on the read path beyond the one cache lock.

## 4. Hard-stop invariant (LOCKED — must not be weakened)

- Hard-stop / emergency exits keep their own independent, fresh venue read:
  `core_brain/single_buy_saver.py` (`create_and_post_market_order`,
  `client.get_order_book`), the live SDK client path, and the shadow rehearsal's
  `book_fn` wiring in `core_brain/shadow_run.py` / `core_brain/shadow_exec.py`.
- The stream cache must be unreachable from those paths. A guard test asserts
  `core_brain/single_buy_saver.py` contains no reference to `market_stream`.
- `tests/test_hard_stop_fresh_exit.py` is read-only: read it, never edit it.

## 5. Telemetry discipline

- Events go through `core_brain.cycle_stream.emit` (`emit_cycle_event()` does not
  exist) with `service="market_stream"`, `phase="stream"`, `cycle=0`, and the
  seven documented `market_stream_*` actions below. `service="market_stream"`
  contains neither `filter` nor `screener`, so events route to the live ticker
  family and do not rotate the ring.
- The ring is a **500-line** shared window (`cycle_stream.MAX_LINES = 500`,
  `KEEP_LINES = 400`) that the quoting loop already churns. One
  `market_stream_tick` summary at most every 10 s; **never** one event per frame.
- Emit calls never raise into the stream thread and never run while the cache lock
  is held.
- Actions (frozen): `market_stream_connected`, `market_stream_reconnect`,
  `market_stream_subscribe`, `market_stream_unsubscribe`,
  `market_stream_snapshot`, `market_stream_error`, `market_stream_tick`.

## 6. Anti-cheat

- Forbid skipping/disabling tests, deleting or weakening assertions, adding lint
  or type suppressions, or `time.sleep` longer than 0.2 s in a test (inject
  `ping_sec` / `time_fn` / the fake `WebSocketApp` instead).
- No test may touch the network, the venue, or `data/orders.db`.
- No new external dependency. Standard library plus the declared
  `websocket-client` only.

## 7. Scope fence (what this ticket does not touch)

- Untouched files: `core_brain/trader_loop.py`, `core_brain/quotes.py`,
  `core_brain/markets.py` (read-only reuse), `core_brain/single_buy_saver.py`,
  `core_brain/shadow_run.py`, `core_brain/shadow_exec.py`,
  `core_brain/live_marks.py`, `core_brain/cycle_stream.py`, `SPEC.md`.
- Deferred to #650 (Trader Loop wiring, per the operator's Job Guard): the
  `VenueSeam.fetch_books` injection at `trader_loop.py:2749`, a `book_stream`
  port, subscription authority from the loop's effective selection, throttle
  purge, reactive fill handling, loop-side read-timing telemetry, shadow opt-in.
  Deferred to #649: the authenticated user stream.
- Do not revive any deprecated polling logic or code from closed #621 / #647.
- Anything else noticed while building goes to
  `docs/issues/648-noticed-but-not-touching.md`, never into this diff.

## 8. Dashboard copy

- Sentences are plain words: no underscores, no codes, no JSON, no raw token ids
  unless a count is what the operator needs.
- Exception, by explicit operator decision (2026-10-18, Station III-B): the
  `market_stream` service mirrors the probe's tagged log convention
  (`[TAG] subject · facts`) on the card, because the two surfaces are the same
  feed read in two places and one convention beats two. The tag vocabulary is
  frozen at `OPEN WAIT + - BOOK WARN TICK ?` and lives in both
  `core_brain/market_stream.py::LOG_TAGS` and
  `dashboard/static/app.js::STREAM_TAGS`; they must stay byte-identical.
- Reuse `buildStreamSentence`; add no new table and no new filter/tab. Every
  interpolated value goes through `esc()`; never `innerHTML +=`.
