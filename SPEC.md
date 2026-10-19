# SPEC.md — Issue #699: Reactive Orphan Leg Trigger on MarketStream Price Thresholds

**Issue:** [#699 — [IMPROVE] Trigger orphan leg stop-loss and hedge completion reactively on MarketStream price thresholds](https://github.com/AI-Degen-69/spread-hunter/issues/699)  
**Branch:** `i699/trigger-orphan-leg-stop-loss-and-hedge-completion-`  
**Tier:** Standard (3 core modules + new trigger board module + unit/integration test suites)  
**Task Type:** `Code`, `Performance`

---

## 1. Goal

Eliminate the up-to-1.0s periodic polling delay when an unhedged single-leg ("orphan") position crosses its deterministic hard-stop bid or profitable taker-hedge ask threshold on the live WebSocket `MarketStream`. Evaluate incoming best-bid/best-ask updates in-memory on the stream thread (< 1 ms, 0 API/DB calls) and immediately wake the existing `risk-guardian-sentry` thread (`guardian_wake.set()`) while preserving the single-executor guarantee (`lifecycle_mutation_guard`) and the 1.0s fallback timer.

---

## 2. Acceptance Criteria

1. **Stream price listener (`core_brain/market_stream.py`):**
   - `MarketBookCache.best_prices(tokens)` takes the cache lock once and returns `{token: (best_bid, best_ask)}` for ready books only.
   - `MarketStream.__init__` accepts an optional `price_listener=None`.
   - In `MarketStream._on_message`, after `apply_frame`, dropping stray tokens, and `_note_snapshot`, read `best_prices` for changed tokens that are still wanted, release all stream/cache locks, and invoke `price_listener(prices)`.
   - If `price_listener` raises an exception, log it, emit `market_stream_error` via `_emit`, and keep the `market-stream` worker thread alive.

2. **In-memory trigger board & watch refresh (`core_brain/orphan_trigger.py` & `core_brain/risk_guardian.py`):**
   - `OrphanTriggerBoard(wake_event, clock=time.monotonic)` maintains an immutable token-to-watch mapping (`Watch` with `condition_id`, `token_id`, `kind` in `{"stop", "hedge"}`, `level`, `source`).
   - `next_generation()` and `publish(watches, generation)` atomically replace the watch map; stale generations are ignored, and per-condition mutes for conditions no longer present in the published map are cleared.
   - `observe(prices)` performs only in-memory map lookups and float comparisons (0 SQLite queries, 0 HTTP calls):
     - `stop` watch fires when `best_bid is not None and best_bid <= level` (matching `HARD_STOP` in `single_leg_lifecycle.transition`).
     - `hedge` watch fires when `best_ask is not None and best_ask > 0 and best_ask < level` where `level = max_pair_cost - held_avg` (matching strict `< max_pair_cost` in `single_buy_saver.py`).
   - Per-condition 2.0s mute (`MUTE_DURATION_SEC = 2.0`) coalesces rapid consecutive crossing frames so `wake_event` is set once per burst; `pop_triggers()` returns and clears recorded trigger events.
   - `build_watches(registry, cfg)` computes active stop watches using `stop_trigger(...)` off the held leg's average fill price from `inventory_from_registry` (taking the latest quote per `(condition_id, side)` by `ts`), skips balanced or unpriced positions, and includes a `hedge` watch on the light token only when `bool(getattr(cfg, "stranded_completion_enabled", False))` is `True`.
   - `run_guardian_tick` accepts `trigger_board=None`, takes a generation before reading the registry, and publishes rebuilt watches after the saver pass (or `{}` when no unhedged positions exist).

3. **Guardian reactive wake, subscription retention & lifecycle serialization (`core_brain/trader_loop.py`):**
   - `start_risk_guardian` accepts `wake_event=None`, `trigger_board=None`, `emit_fn=None`, and `ring_path=None`. Instead of `evt.wait(timeout)` alone, it waits on `wake_event` (when wired) for the remaining interval (`max(0.01, interval - elapsed)`), waking immediately when `wake_event` or `stop_event` is set.
   - Before each guardian tick: check `stop_event`, clear `wake_event`, pop any fired triggers from `trigger_board` and emit `guardian_reactive_wake` via `cycle_stream.emit` (with `condition_id`, `token_id`, `kind`, `source`, `level`, `price`, `latency_ms`), then call `run_guardian_tick(..., trigger_board=trigger_board)`.
   - `run()` accepts optional `guardian_wake=None` and `trigger_board=None`, forwards them to `start_risk_guardian` and `_guardian_fill_tick`, and on shutdown sets both `stop_workers` and `guardian_wake` and joins the guardian thread (`join(timeout=2.0)`).
   - `_make_subscribe_fn` accepts `trigger_board=None` and passes the union of `current_cids` and `trigger_board.condition_ids()` to `market_stream.retain_markets(...)` so held unhedged positions that leave the rotation stay subscribed on the WebSocket stream.
   - In `main()`, `OrphanTriggerBoard` and `guardian_wake` are created only when `use_ws_books` is `True` and `not a.once`.

---

## 3. Edge Cases

- **Unpriced or zero-cost fills:** If `held_shares > 0` but `held_cost <= 0` (or `held_avg <= 0` / `> 1`), `build_watches` skips the condition rather than raising `ValueError` from `stop_trigger`.
- **Token re-minting across quotes:** Grouping quotes by `(condition_id, side)` selects the latest `token_id` by `ts` (matching `single_buy_saver.py`).
- **Stale publish race:** If a slow timer tick overlaps a faster fill-triggered guardian tick, `next_generation()` + `publish(watches, generation)` ensures the older generation cannot overwrite newer watches.
- **Burst of crossing ticks:** 50+ rapid `price_change` frames on a breached token set `guardian_wake` once within the 2.0s mute window; if the exit is refused and the position remains unhedged, the 1.0s fallback timer still evaluates on schedule and the mute expires after 2.0s.
- **Cache lock safety:** `price_listener` is invoked strictly outside `MarketBookCache._lock` and `MarketStream._lock` so a listener or concurrent `book()` reader can never deadlock.
- **Missing snapshot / stray token:** Deltas on tokens without a snapshot or tokens not in `_want` never invoke `price_listener`.

---

## 4. Explicit Out of Scope

- Modifying `core_brain/single_buy_saver.py`, `core_brain/single_leg_lifecycle.py`, `core_brain/config.py`, `core_brain/order_registry.py`, `core_brain/order_manager.py`, or `core_brain/unhedged_stop_loss.py`.
- Re-quoting maker orders on every price tick (dual-resting quotes remain governed by rotation cadence and `BOT_ORDER_REQUOTE_DEAD_BAND_CENTS`).
- Modifying offline/rehearsal tools (`core_brain/shadow_run.py`, `core_brain/shadow_exec.py`, `scripts/book_tape_recorder.py`, `scripts/booktape_report.py`).
- Changing the rotation `wake` event or `_make_sleep_fn` used by `UserStreamClient`.
