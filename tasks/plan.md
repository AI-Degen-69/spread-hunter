# Station II plan — #649 [CREATE] User Account WebSocket stream

**Issue:** https://github.com/AI-Degen-69/spread-hunter/issues/649
**Branch:** `i649/user-account-ws-stream` (from `main` 6b0b191, clean, 682/682)
**Size:** M (one new module + one new test file; zero changes to existing modules)
**Scope guard:** #649 only. No pricing math, no order placement/cancellation, no change to
`core_brain/market_stream.py`, no change to hard-stop logic in `core_brain/single_buy_saver.py`.
Phase-3 loop wiring belongs to a later issue.

## 0. Grounded seams (verified verbatim — no guesswork)


- `core_brain/venue.py:128` — `def api_creds_from_env():` reads `POLY_API_KEY` /
  `POLY_API_SECRET` / `POLY_API_PASSPHRASE`, returns `ApiCreds` or `None` when any is
  missing. **This is the credential source.** The issue text cites
  `core_brain/signer.py:1-120` — that file **does not exist** (stale reference, recorded
  as a contradiction below). Never call `venue.client()` here (it calls
  `create_or_derive_api_key()`, a network call).
- `core_brain/venue.py:100` — wallet label via `os.environ.get("POLY_FUNDER")`, else `"unknown"`.
- `core_brain/market_stream.py:36` — `MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"`;
  user channel is the sibling `.../ws/user`. `MarketStream` (line 194) is the lifecycle
  template to mirror: two daemon threads, `threading.Event` stop, lazy `import websocket`,
  fresh `WebSocketApp` per attempt, send-lock, backoff `1.0 -> 30.0s`. Do not modify it.
- `core_brain/cycle_stream.py:364` — `def emit(cycle, phase, action, *, service, market_slug,
  reason, latency_ms, extra, ring_path, db_path, can_rotate, run_id) -> None`.
  Never raises. Ring defaults to `runtime/cycle_events.jsonl`. User events use
  `service="user_stream"`, `can_rotate=False`, never `db_path` (no cycle_intent rows).
- `tests/conftest.py:45-57,74-77` — every test runs with `POLY_*` scrubbed; tests set creds
  explicitly with `monkeypatch.setenv`.
- Dashboard card exists ("LIVE EXECUTION & CYCLE EVENT STREAM"); no frontend change needed.

## 1. Assumptions & contradictions

- **Contradiction (issue vs repo):** issue cites `core_brain/signer.py` — file absent.
  Resolution: credentials come from `core_brain/venue.py:api_creds_from_env()`.
- **Contradiction (issue vs venue protocol):** issue acceptance says "using HMAC headers".
  The venue's documented user-channel protocol is a JSON auth frame
  `{"auth": {"apiKey", "secret", "passphrase"}, "type": "user"}` sent once on open —
  not HTTP headers. Implement the documented frame; `auth_ok` is inferred from the first
  server reply (venue sends no explicit confirmation) and the reason text says so.
- Assumption: user frames carry `event_type` of `trade` / `order` (same snake_case wire
  convention as the market channel). Unknown types are skipped and counted.
- Assumption: `websocket-client` (`websocket.WebSocketApp`) is installed (market_stream
  imports it lazily the same way; if missing, log clearly and refuse `start()`).

## 2. Interface lock (what build implements against)

`core_brain/user_stream.py` (new, ~250 lines, stdlib + lazy `websocket` only):

- Constants: `USER_WS_URL` (`.../ws/user`), `PING_TEXT = "PING"`,
  `PING_INTERVAL_SEC = 10.0`, `PONG_MISS_LIMIT = 3`,
  `RECONNECT_MIN_SEC = 1.0`, `RECONNECT_MAX_SEC = 30.0`,
  `FINAL_TRADE_STATUSES = {"CONFIRMED", "FAILED"}`, `SERVICE = "user_stream"`.
- `UserStreamCredentialsMissing(RuntimeError)` — message names the three env vars, never values.
- Frozen dataclasses: `MakerOrderFill` (order_id, price, size, side), `UserTradeEvent`
  (trade_id, order_id, condition_id, token_id, side, price, size, status, fills tuple),
  `UserOrderEvent` (order_id, condition_id, token_id, side, price, size, status).
  Prices/sizes are `float`; status stored as sent; no raw payloads kept.
- `parse_frame(text)` — pure, network-free. One object or a list; routes by `event_type`;
  plain `PONG` is a heartbeat reply; skips non-JSON/unknown/missing-or-nonnumeric with one
  short log line + skip count.
- `UserStreamClient(emit_fn=None, time_fn=time.time)` — owns registry AND lifecycle (no
  separate dispatcher): `on_trade(cb)`, `on_order(cb)`, `on_state(cb)`, each returning an
  unsubscribe fn. Lock-guarded lists; snapshot under lock, invoke after release; per-callback
  errors caught/logged. Docstring states: callbacks run on the socket thread, must be quick,
  notices are not committed fills.
- Lifecycle mirrors `MarketStream`: `start()`/`stop()`; `_on_open` sends the auth frame once
  (`markets` omitted unless condition IDs given — absent means all markets); heartbeat thread
  sends text `PING`/closes on 3 missed `PONG`s; reconnect loop backs off 1->30s via
  `stop_event.wait(delay)`; reset to 1s after `auth_ok`.
- Cycle events via injected `emit_fn` (default `cycle_stream.emit`, `service="user_stream"`,
  `can_rotate=False`, prose `reason`, `extra` with order_id/status/size/price):
  `user_ws_connected`, `user_ws_auth_sent`, `user_ws_auth_ok` (once per connection, first
  server reply), `user_ws_trade`, `user_ws_order`, `user_ws_disconnected`,
  `user_ws_reconnecting`, `user_ws_stopped`, `user_ws_error`.
- `python -m core_brain.user_stream --listen [--condition-id ...]` read-only probe CLI.
- MUST NOT: import `venue.client`, call `create_or_derive_api_key`, touch `data/orders.db`,
  import/modify `market_stream`, `single_buy_saver`, pricing, or order paths.

## 3. Improvement proposal (adopted — simplification, evidence-based)

> Evidence (issue, Out of scope): "Final loop integration wiring (handled in Phase 3)."
> Proposal: this issue ships client + registry + events with zero engine wiring — no
> fill-listener hookup, no saver dispatch. The partial-fill-to-rescue path is Phase-3 work
> that needs the registry first; wiring it here doubles the review surface. Adopted by
> default (simplification, not scope expansion).




## 4. Task graph (3 vertical slices, risk-first)

- **U1 (M) — Offline core: creds, dataclasses, parser, registry.** `Depends on:` none.
  Pure network-free code + tests first. Unblocks U2/U3.
- **U2 (M) — Lifecycle: auth frame, text heartbeat, backoff, clean stop, probe CLI.**
  `Depends on:` U1. Models `MarketStream`; fake-socket tests (no network).
- **U3 (S) — Cycle-stream emission of all `user_ws_*` events.** `Depends on:` U1, U2.
  Fake `emit_fn` capture tests; assert `can_rotate=False`, no `db_path`.
- Checkpoints: after U1 (parse + registry proven offline), after U2 (lifecycle proven with
  fakes), after U3 (dashboard-visible events proven via captured emit records).

## 5. Tests that fail without the change (`tests/test_user_stream.py`, new)

- U1 offline: each missing credential raises (3 cases); secrets never in messages; no
  `create_or_derive_api_key` call; `POLY_FUNDER` label vs `"unknown"`; single trade frame;
  mixed list frame; order frame; skip-counter rises on non-JSON/unknown-type/bad-price;
  `FINAL_TRADE_STATUSES` membership; callback isolation; unsubscribe; concurrent registration.
- U2 lifecycle: open sends exactly one auth frame (no `markets` key by default); text `PING`
  cadence; missed-`PONG` close; backoff 1->2->...->30 cap; reset after `auth_ok`; prompt stop.
- U3 emission: one captured record per lifecycle/fill event, `service="user_stream"`, prose
  reason, order_id/status/size/price in extra, `can_rotate=False`.
- Regression files that must stay green: `tests/test_live_marks.py`, `tests/test_cycle_stream.py`.

## 6. Env caveats

- Tests are hermetic: conftest scrubs `POLY_*`; set creds per-test with `monkeypatch`.
- Live probe (`--listen`) needs real `POLY_API_KEY/SECRET/PASSPHRASE` + network; read-only
  (no order placement) but operator-run only, never agent-run.
- Never open `data/orders.db` except reads; shadow rehearsals use fresh `data/*_shadow_*.db`.

## 7. How to verify (hands-on, operator-run — no pytest here)

1. `python -m dashboard.server`, open `http://127.0.0.1:8799` -> Trades & Positions ->
   LIVE EXECUTION & CYCLE EVENT STREAM.
2. Second shell: `python -m core_brain.user_stream --listen`.
3. Confirm `USER_WS_CONNECTED -> wss://.../ws/user` then `USER_WS_AUTH_OK -> wallet: 0x...`.
4. Run `python -m core_brain.order_manager status` (read-only); confirm order/trade frames
   stream with exact `order_id`, `status`, `size`, `price`.

## 8. Deferred (out of scope, not touching)

- Engine wiring of fill callbacks into rescue/saver (Phase 3 per issue).
- `market_stream.py` changes (explicitly forbidden). HMAC-header auth (venue documents the
  JSON auth frame; implementing that instead).
