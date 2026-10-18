# Issue #648 — noticed but not touching

Parked while planning the live market stream engine (#648). None of these belong
in this diff.

## Trader Loop wiring — belongs to #650

- `core_brain/trader_loop.py:2749` still injects `fetch_books=full_book`. Flipping
  it to the stream adapter, plus the `VenueSeam.book_stream` port, the token-learning
  hook in the visit path, and start/stop lifecycle ownership, is #650's In-Scope
  item 1.
- `_market_specs` carries **no token ids** (`core_brain/trader_loop.py:2046-2083`),
  and `runtime/markets.json` rows carry none either (`cid`, slug, title, sizes
  only) — so subscription authority must come from a resolved market, which is
  loop-side work.
- Per-visit `fetch_pinned_market` (one HTTP market read per market per rotation,
  `markets.py:253/292`) stays. #648 removes only the two `GET /book` reads per
  visit; #650 is where the remaining HTTP work gets attention.
- No artificial visit delay or HTTP-shaped throttle exists to purge (verified
  2026-10-18: the only sleeps are the rotation `sleep_fn(interval)` at
  `trader_loop.py:736`, the resolution sweeper's off-path `time.sleep(max(30,
  interval))` at `:2532`, and `BOOK_TIMEOUT` inside `full_book`). The throttle
  purge listed in #650 and in that issue's Background appears to target a
  `pass_work + 1.0s` pattern this repo no longer has — worth re-verifying in #650
  rather than assuming it exists.
- Shadow rehearsal opt-in (`--book-source` in `core_brain/shadow_run.py`, keeping
  `ShadowExecutionClient(book_fn=...)` on its own REST reader): #650, not here.
- Loop-side read-timing telemetry ("record real in-memory read timings") — the
  probe's tick summary carries `read_us_p50` for #648; loop telemetry is #650.

## Other parked items

- `core_brain/live_marks.py` (dashboard marks, #427) ignores `price_change`
  frames: it reads top-level `bids`/`asks` only, so deltas never move its mid, and
  its `sync_subscription` close-and-redials instead of using the documented
  `{"operation": "subscribe"|"unsubscribe"}` frames. Out of scope here; a
  follow-up could fold the marks cache onto this engine once the operation frames
  are confirmed on the wire.
- The `best_bid` / `best_ask` cross-check on `price_change` entries is kept as an
  observational counter (`level_gap`) only. The venue's exact semantics for those
  per-entry fields are unverified; the counter is the evidence a follow-up needs
  before any corrective action (invalidate + resubscribe) is built.
- `core_brain/markout.py`, `scripts/book_tape_recorder.py`,
  `scripts/filter_markets.py` and `core_brain/order_manager.py` keep their own
  HTTP `/book` reads; none of them are on the quoting hot path.
- `docs/agents/architecture.md`'s module map does not list `live_marks.py`, so it
  was left alone rather than gaining a single new line for `market_stream.py`.
- `SPEC.md` still carries issue #632's spec (project-level doc, updated by
  whichever ticket owns it). #648's spec is embedded in `tasks/plan.md`.
