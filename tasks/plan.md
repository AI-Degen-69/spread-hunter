Branch: i699/trigger-orphan-leg-stop-loss-and-hedge-completion- | Issue: #699

# Implementation Plan — Issue #699: Reactive Orphan Leg Trigger on MarketStream Price Thresholds

- **Issue:** [#699 — [IMPROVE] Trigger orphan leg stop-loss and hedge completion reactively on MarketStream price thresholds](https://github.com/AI-Degen-69/spread-hunter/issues/699)
- **Size Tier:** Standard (3 core modules modified + 1 focused trigger module added + 4 test modules updated)
- **Task Type:** `Code`, `Performance`
- **Assigned Skills:** `api-and-interface-design`, `incremental-implementation`, `constraint-driven-development`

---

## CodeRabbit Plan Intake & Open Questions Resolution

### Three-Line CodeRabbit Intake Note
1. **Adopted:** Three-task vertical decomposition (`MarketStream` `price_listener` + `MarketBookCache.best_prices`, `OrphanTriggerBoard` + `build_watches` in `core_brain/orphan_trigger.py` / `core_brain/risk_guardian.py`, and dedicated `guardian_wake` + `retain_markets` wiring in `core_brain/trader_loop.py`).
2. **Rejected:** None of the structural tasks needed splitting; kept the 3-task vertical structure and rejected any changes to `single_buy_saver.py` or `shadow_run.py`.
3. **Resolved `[UNVERIFIED]` items from live code:**
   - `detect_unhedged_positions` in `core_brain/risk_guardian.py:48-57` already exposes `"up_token"` and `"down_token"`, and we will extract a shared `_group_condition_tokens(registry)` helper that picks the latest quote per `(condition_id, side)` by `ts` (matching `single_buy_saver.py:1661-1671`) so token re-minting never overwrites a newer token with a stale quote row.
   - Stop-loss config knob names in `core_brain/single_buy_saver.py:1633-1643` are verified verbatim: `stop_loss_mode = str(getattr(cfg, "stop_loss_mode", "NONE"))`, `drop_cents = float(getattr(cfg, "drop_cents", 0.05))`, `drawdown_pct = float(getattr(cfg, "drawdown_pct", 10.0))`, `bot_risk_circuit_max_drawdown_pct = float(getattr(cfg, "bot_risk_circuit_max_drawdown_pct", None) or getattr(cfg, "emergency_drawdown_pct", 75.0))`, and `max_pair_cost = float(getattr(cfg, "bot_pos_price_max_pair_cost_usd", None) or getattr(cfg, "max_pair_cost", 0.995))`.

### Open Question Resolved from Code
- **Default debounce / in-flight lock:** Uses a 2.0-second per-condition mute (`MUTE_DURATION_SEC = 2.0`) inside `OrphanTriggerBoard` to coalesce rapid WebSocket ticks into a single wake, while process-wide mutual exclusion (`_LIFECYCLE_LOCK` via `lifecycle_mutation_guard`) and fresh registry re-reads inside `manage_single_leg_positions` prevent duplicate order execution. When a subsequent guardian publish drops the condition (after exit/completion), its mute is cleared immediately; if an exit is refused, the 1.0s fallback timer continues retrying.

---

## Improvement Proposal (Step 5)

- **Classification:** Edge-case hardening (Adopt-by-default).
- **Evidence:** In `core_brain/risk_guardian.py:24-31`:
  ```python
  for q in all_quotes:
      cid = q.get("condition_id")
      side = str(q.get("side") or "").upper()
      tok = q.get("token_id")
      if cid and side in ("UP", "DOWN") and tok:
          token_pairs.setdefault(cid, {})[side] = tok
  ```
  Whereas `core_brain/single_buy_saver.py:1661-1671` selects the token with the latest `ts`:
  ```python
  ts = float(quote.get("ts") or 0.0)
  prior = quoted_tokens.setdefault(cid, {}).get(side)
  if prior is None or ts >= prior[0]:
      quoted_tokens[cid][side] = (ts, token_id)
  ```
- **Proposal:** Unify token-pair grouping in `_group_condition_tokens(registry)` (shared by `detect_unhedged_positions` and `build_watches`) to select the latest `token_id` by `ts` per `(condition_id, side)`, preventing order-dependent dict overwrites when a market has multiple historical quote rows.

---

## Interface Contracts (Step 4)

### 1. `core_brain/market_stream.py`
- `MarketBookCache.best_prices(self, tokens: Iterable[str]) -> dict[str, tuple[Optional[float], Optional[float]]]`
  - Acquires `self._lock` once. Returns `{str(tok): (book["best_bid"], book["best_ask"])}` for each token in `tokens` that is currently present in `self._books`.
- `MarketStream.__init__(self, *, cache=None, emit_fn=None, time_fn=time.time, ping_sec=PING_INTERVAL_SEC, tick_sec=TICK_SUMMARY_SEC, price_listener: Optional[Callable[[dict[str, tuple[Optional[float], Optional[float]]]], None]] = None)`
  - In `_on_message`:
    1. `changed = self._cache.apply_frame(payload)`
    2. Identify `stray` and `wanted_changed = [t for t in changed if t not in stray]` under `self._lock`.
    3. Drop `stray` from `self._cache`.
    4. Call `self._note_snapshot(token)` for each token in `wanted_changed`.
    5. If `self._price_listener is not None and wanted_changed`: read `prices = self._cache.best_prices(wanted_changed)` and, **outside all locks**, call `self._price_listener(prices)` inside a `try/except Exception` block that logs a warning, increments `self.listener_errors`, and emits `self._emit("market_stream_error", reason="listener_error", extra={"reason": "listener_error", "detail": f"{type(e).__name__}: {e}"[:120]})`.

### 2. `core_brain/orphan_trigger.py` [NEW]
- `@dataclass(frozen=True) class Watch:`
  - `condition_id: str`
  - `token_id: str`
  - `kind: str` (`"stop"` | `"hedge"`)
  - `level: float`
  - `source: str` (`"strategy"` | `"emergency"` | `"stranded_hedge"`)
- `@dataclass(frozen=True) class TriggerEvent:`
  - `condition_id: str`
  - `token_id: str`
  - `kind: str`
  - `source: str`
  - `level: float`
  - `price: float`
  - `triggered_at: float`
- `class OrphanTriggerBoard:`
  - `__init__(self, wake_event: threading.Event, clock: Callable[[], float] = time.monotonic, mute_sec: float = 2.0)`
  - `next_generation(self) -> int`
  - `publish(self, watches: Mapping[str, Watch], generation: Optional[int] = None) -> bool`
  - `observe(self, prices: Mapping[str, tuple[Optional[float], Optional[float]]]) -> None`
  - `pop_triggers(self) -> list[dict[str, Any]]`
  - `condition_ids(self) -> set[str]`
  - `watches(self) -> dict[str, Watch]`

### 3. `core_brain/risk_guardian.py`
- `_group_condition_tokens(registry: OrderRegistry) -> dict[str, dict[str, str]]`
- `build_watches(registry: OrderRegistry, cfg: Any, db_path: Optional[Path | str] = None) -> dict[str, Watch]`
- `run_guardian_tick(client: Any, registry: OrderRegistry, cfg: Any, *, live: bool = False, single_buy_saver_fn: Optional[Callable] = None, trigger_board: Optional[Any] = None) -> list[dict[str, Any]]`

### 4. `core_brain/trader_loop.py`
- `start_risk_guardian(client: Any, registry: Any, cfg: Any, interval: float = 1.0, live: bool = True, single_buy_saver_fn: Optional[Callable] = None, stop_event: Optional[Any] = None, wake_event: Optional[Any] = None, trigger_board: Optional[Any] = None, emit_fn: Optional[Callable] = None, ring_path: Optional[Path | str] = None) -> threading.Thread`
- `_make_subscribe_fn(market_stream: Any, cache: Any, fetch_market_fn: Callable[[str], Any], *, trigger_board: Optional[Any] = None, timeout_sec: float = 5.0, poll_sec: float = 0.05, time_fn: Callable[[], float] = time.monotonic, sleep_fn: Callable[[float], None] = time.sleep) -> Callable[[list], None]`
- `run(..., guardian_wake: Optional[threading.Event] = None, trigger_board: Optional[Any] = None)`

---

## Task Breakdown & Dependency Graph (Step 6)

```text
Task 1 (Stream Price Listener) ──┐
                                 ├──► Task 3 (Guardian Wake, Wiring & Retention)
Task 2 (Trigger Board & Watches) ┘
```

### [x] Task 1: Stream Price Listener (`core_brain/market_stream.py`)
- **Task ID:** `T1`
- **Size:** `S`
- **Domain Tag:** `[Backend/Logic]`
- **Depends on:** None
- **Target Files:** `core_brain/market_stream.py`, `tests/test_market_stream.py`
- **Helper Skill:** `api-and-interface-design`
- **Description:**
  - Add `MarketBookCache.best_prices(tokens)` taking `_lock` once and returning `{token: (best_bid, best_ask)}` for ready books.
  - Add `price_listener=None` to `MarketStream.__init__`.
  - In `MarketStream._on_message`, after `apply_frame`, dropping stray tokens, and `_note_snapshot`, read `best_prices` for changed wanted tokens, release all locks, and call `price_listener(prices)` with fault isolation (`market_stream_error` emission on exception).
- **Verification:**
  - `python -m pytest -q tests/test_market_stream.py`
  - Required tests: `test_price_listener_gets_new_best_bid_and_ask`, `test_price_listener_skips_token_without_snapshot`, `test_price_listener_skips_stray_token`, `test_raising_listener_keeps_stream_alive`, `test_listener_runs_outside_cache_lock`, `test_fresh_book_after_reconnect_reaches_listener`.

### [x] Task 2: Trigger Board & Watch Refresh (`core_brain/orphan_trigger.py`, `core_brain/risk_guardian.py`)
- **Task ID:** `T2`
- **Size:** `M`
- **Domain Tag:** `[Backend/Logic]`
- **Depends on:** None
- **Target Files:** `core_brain/orphan_trigger.py` [NEW], `core_brain/risk_guardian.py`, `tests/test_risk_guardian.py`
- **Helper Skill:** `incremental-implementation`
- **Description:**
  - Create `core_brain/orphan_trigger.py` with `Watch`, `OrphanTriggerBoard(wake_event, clock=time.monotonic)`, `next_generation()`, `publish(watches, generation)`, `observe(prices)`, `pop_triggers()`, and `condition_ids()`.
  - Add `_group_condition_tokens(registry)` and `build_watches(registry, cfg, db_path=None)` in `core_brain/risk_guardian.py` (and re-export or import cleanly).
  - Update `run_guardian_tick` to accept `trigger_board=None`, capture generation before registry inspection, and publish `build_watches(registry, cfg)` after the saver pass (or `{}` when no unhedged positions exist).
- **Verification:**
  - `python -m pytest -q tests/test_risk_guardian.py tests/test_single_leg_lifecycle.py`
  - Required tests in `tests/test_risk_guardian.py`: safe band (100 frames, 0 client/registry calls, wake clear), stop boundary (`<= level` fires, `+0.01` and `None` do not), hedge boundary (`< level` fires, `== level`, `0`, `None` do not), 2.0s mute coalescing (50 frames -> 1 wake, +1.9s muted, +2.0s fires), publish generation ordering & mute cleanup on condition removal, `build_watches` stop level accuracy & average fill updates, balanced/unpriced exclusions, `stranded_completion_enabled` gating, and `run_guardian_tick` empty publication.

### Checkpoint 1 (after T1 & T2)
- Both `tests/test_market_stream.py` and `tests/test_risk_guardian.py` pass with the new listener and trigger board in isolation.

### [x] Task 3: Guardian Reactive Wake, Loop Wiring & Subscription Retention (`core_brain/trader_loop.py`)
- **Task ID:** `T3`
- **Size:** `M`
- **Domain Tag:** `[Backend/Logic]`
- **Depends on:** `T1`, `T2`
- **Target Files:** `core_brain/trader_loop.py`, `tests/test_trader_loop.py`, `tests/test_lifecycle_single_executor.py`
- **Helper Skill:** `incremental-implementation`
- **Description:**
  - Update `start_risk_guardian` in `core_brain/trader_loop.py` to accept `wake_event=None`, `trigger_board=None`, `emit_fn=None`, and `ring_path=None`. Wait on `wake_event` (or `stop_event` when `wake_event is None`) with remaining-interval timeout; exit immediately when `stop_event` is set; clear `wake_event`; emit `guardian_reactive_wake` via `cycle_stream.emit` for each item in `trigger_board.pop_triggers()` (including `condition_id`, `token_id`, `kind`, `source`, `level`, `price`, and `latency_ms`); pass `trigger_board=trigger_board` to `run_guardian_tick`.
  - Update `_make_subscribe_fn` to accept `trigger_board=None` and pass `set(current_cids) | trigger_board.condition_ids()` to `market_stream.retain_markets(...)`.
  - Update `run` and `_guardian_fill_tick` to thread `guardian_wake` and `trigger_board` to `start_risk_guardian` and `run_guardian_tick`, and on shutdown set `stop_workers` and `guardian_wake` and join the guardian thread.
  - Update `main` to instantiate `guardian_wake = threading.Event()` and `trigger_board = OrphanTriggerBoard(guardian_wake)` when `use_ws_books and not a.once`, wiring `price_listener=trigger_board.observe` into `MarketStream`.
- **Verification:**
  - `python -m pytest -q tests/test_market_stream.py tests/test_single_leg_lifecycle.py tests/test_risk_guardian.py tests/test_trader_loop.py tests/test_lifecycle_single_executor.py tests/test_hard_stop_fresh_exit.py`
  - Required tests in `tests/test_trader_loop.py` & `tests/test_lifecycle_single_executor.py`: wake tick with `interval=3600`, burst coalescing (`saver.call_count <= 2` across 20 wakes), clean shutdown join, disabled modes (`--once` / `--no-ws-books`), subscription retention of watched condition IDs, end-to-end `MarketStream` + `OrphanTriggerBoard` + `start_risk_guardian` emitting `guardian_reactive_wake` to `ring_path`, and `Barrier(2)` concurrent wake-guardian + rotation pass asserting peak concurrency `== 1`.
