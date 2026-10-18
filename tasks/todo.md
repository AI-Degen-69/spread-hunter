# Todo — Issue #648

- [x] T1 — `MarketBookCache` folds `book` + `price_change` frames, REST-identical shape, `BookNotReady` on unready reads (CP0 gate)
- [x] T2 — Socket worker: initial subscribe on open, `PING` heartbeat, liveness window, close-drops-books, capped backoff (CP1 gate)
- [ ] T3 — Dynamic universe (`ensure_market` / `retain_markets` via `operation` frames, no reconnect) + lifecycle events + rate-limited tick summary
- [ ] T4 — `fetch_books` seam adapter + `python -m core_brain.market_stream --listen` probe + hard-stop guard test
- [ ] T5 — Dashboard plain-word sentences for the seven `market_stream` actions + parked-items note (CP2 gate)
- [ ] CP2 — Focused checks green for every touched module, probe run hands-on once, card showing the events; handoff to `/iv-review-build-and-pr`
