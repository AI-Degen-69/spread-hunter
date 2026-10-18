# CONSTRAINTS — Issue #650: Wire the stream clients into the Trader loop

Branch: refactor/trader-loop-ws-streams-650 | Issue: #650

## Non-Negotiable Quality Guardrails

1. **Zero Regressions (focused):**
   - `python -m pytest -q tests/test_trader_loop.py` must pass.
   - `python -m pytest -q tests/test_market_stream.py tests/test_user_stream.py` must pass.
   - `python -m pytest -q tests/test_shadow_run.py` must pass.
   - `python -m pytest -q tests/test_quotes.py` must pass (the book seam is read-only; prove it).
   - The full suite on GitHub CI (Ubuntu + Windows) is the merge gate.

2. **Read-only invariants — do NOT edit:**
   - `core_brain/quotes.py` — the wrap contract (`MarketQuoteError("book fetch error: ...")`) is what the loop classifies on. A cold cache must not be special-cased inside it.
   - `core_brain/markets.py` — `full_book` is the shape contract; the stream cache must produce that shape, not the other way round.
   - `core_brain/user_stream.py`, `core_brain/risk_guardian.py`, `core_brain/cycle_stream.py` — consumed as published. If an API is genuinely missing, stop and report; do not widen those modules inside this issue. (`core_brain/market_stream.py` got a one-line `__init__` fix: `_landed` is created there now, not only on socket open, so `retain_markets` before the first open cannot raise `AttributeError`.)
   - `data/orders.db`, `runtime/*.json` — production state. Tests use `tmp_path`.

3. **No REST fallback on the live path.** When stream books are enabled, `fetch_books` reads the cache only. A cold/blocked book must fail closed (ERROR + rotation suspect), never silently re-read the REST endpoint — a hidden fallback is exactly the polling this issue removes.

4. **Behaviour-preserving default.** Every new seam port and `run()` parameter is optional with a `None` default. A seam constructed as today (32 keyword-built `VenueSeam(` constructions across `tests/`, 27 of them in `tests/test_trader_loop.py`; none positional) must behave identically: no subscriptions, no fill drain, no events beyond the additive telemetry the plan names.

5. **Failure isolation.** Subscribe, fill drain, fill reconcile, and the fill-triggered guardian tick are each wrapped so a failure degrades one cycle, never stops the loop (matching the existing reconcile/sweep/visit isolation).

6. **No double-counting.** A fill event is deduped by `(trade_id, status)` (bounded memory): a repeated identical event reacts once, a status change reacts again. Fill-triggered reconcile coexists with the interval reconcile — both must be safe to run back-to-back (registry dedup + lease is what makes that true; prove it, do not assume it).

7. **Telemetry honesty.** New events (`rotation_complete`, `user_fill_received`) carry real measured values or nothing — no invented latency, no `book_source: "stream"` on a row whose book came from REST or a shadow fixture. Simulated `user_fill_received` rows fire only for recorded `shadow-` fills, never for unrelated public prints; stream provenance travels on the decide row via `book_stats_fn`, never as a per-read ring event.

8. **Anti-cheat:** no `@pytest.mark.skip`, no deleted or weakened assertions, no sleep-based timing assertions relaxed beyond what the behaviour needs.
