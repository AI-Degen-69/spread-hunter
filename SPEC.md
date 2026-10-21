# SPEC — #659 Unify real-time pricing (WS-first + REST fallback)

## Goals
- One seam contract `BookProvider` with `get_book(token_id)` / `get_mid(token_id)`:
  WS-cache-first → REST fallback, fail-closed (never empty/stale book on failure).
- Speed-sensitive decision reads (quoting, shadow queue modelling, saver trigger
  bid, stray guard) evaluate on real-time books (<1ms in-memory).
- Execution reads (hard-stop exit poll, shadow `get_order_book` for fills,
  completion paths) stay on always-fresh REST.
- Fill accounting unchanged: REST trade tape remains the only fill evidence;
  stream `last_trade_price` prints are never fill evidence (no dedup guarantee).
- Operator gets a one-flag undo: `--book-source rest` on the live trader.

## Acceptance criteria
1. `core_brain/book_provider.py` exists with `BookProvider`:
   `get_book`, `fetch_books(clob_host, token_id)` seam adapter, `get_fresh_book`
   (REST-only), `get_mid`, lifecycle passthrough
   (`ensure_market`, `retain_markets`, `start`, `stop` with ownership tracking).
2. Shadow + defense decision paths evaluate on real-time books; fallback tests
   with silent/disconnected WS mock are green.
3. `BookNotReady` never escapes the provider (converted to REST fallback).
4. `get_mid` mirrors `live_marks` validity: finite, `bid < ask`, both in 0–1,
   else `None` (never zero).

## Edge cases
- Stream disconnected / silent beyond `LIVE_WINDOW_SEC` → REST fallback.
- Unknown token → REST fallback.
- REST also failing → raise `full_book`'s own exceptions (callers treat as
  fetch failure); never return an empty book.
- Closed socket: cache already drops every book; provider must not serve them.
- No stream attached → REST directly.
- REST results are never written into the stream cache (no competing writers).

## Explicit out-of-scope
- Dashboard `live_marks` rebuild on top of `market_stream` (#657 unfinished;
  separate process cannot share in-memory provider). Provider's `get_mid`
  only mirrors its validity rule.
- Stream trade-print fill crediting + equal-price fill policy change (separate
  money-sensitive decision).
- Execution-time book reads stay fresh-REST (hard-stop contract intact).
- `last_trade_price` / tick-size frames remain ignored by the fold rules.
