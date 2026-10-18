# Todo — Issue #648

- [x] T1 — `MarketBookCache` folds `book` + `price_change` frames, REST-identical shape, `BookNotReady` on unready reads (CP0 gate)
- [x] T2 — Socket worker: initial subscribe on open, `PING` heartbeat, liveness window, close-drops-books, capped backoff (CP1 gate)
- [x] T3 — Dynamic universe (`ensure_market` / `retain_markets` via `operation` frames, no reconnect) + lifecycle events + rate-limited tick summary
- [x] T4 — `fetch_books` seam adapter + `python -m core_brain.market_stream --listen` probe + hard-stop guard test
- [x] T5 — Dashboard plain-word sentences for the seven `market_stream` actions + parked-items note (CP2 gate)
- [x] CP2 - Focused checks green (383/383), probe run hands-on live twice (operation frames confirmed on the wire), card feed pinned by the harness; handoff to /iv-review-build-and-pr
