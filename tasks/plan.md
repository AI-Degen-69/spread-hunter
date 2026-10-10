Branch: i459/fix-live-stream-trades-tab-shows-no-trades-and-ro | Issue: #459

# Plan — #459: Live stream TRADES tab shows trades in plain English

Size: **Standard** — 7 source files across backend telemetry + frontend render,
but no new dependencies, no schema change (`cycle_intent` writes untouched, ring
`extra` is free-form), no public API change. One architectural decision:
observe-only telemetry + action-keyed sentence builders.
Task types: **Code** (backend), **UX / Copy** (sentences), **Design/UI** (rows, toggle).

## CodeRabbit intake note (read once, mined for scaffolding)

- Adopted: 2-phase shape (backend telemetry → frontend sentences), exact test
  names/assertions, protected-file list, honest-wording rules, `can_rotate=False`
  for relayer events, "Decided to quote" wording, card-level SHOW DETAILS toggle.
- Rejected: nothing material; its 6 tasks merged into 4 atomic tasks below
  (its Tasks 1.1–1.3 backend work is T1–T3 here; its Task 2.1 frontend work is T4).
- Still `[UNVERIFIED]`: exact `cancel`-adjacent patch targets are N/A here (no
  relayer mapping change); the two named source-presence checks in
  `tests/test_dashboard_server.py:1328–1339,1760–1772` may break on the filter
  rename — update only if broken. Line citations drifted slightly vs live code
  (verified live: `index.html:232–235` filters, `app.js:791/844/883–886/897–916/941–974`,
  `order_manager.py:2250/2345`, `trader_loop.py:1940/2731`, `cycle_stream.py:364–435`,
  `order_registry.py:1923`); plan uses live line numbers where checked.

## Interface contracts (locked before build)

- `fill_extra(fill, order, meta=None) -> dict`: `condition_id`, `token_id`,
  `order_id`, `trade_id`, `pair_id`, `side` (BUY|SELL venue direction),
  `outcome` (UP|DOWN, only if known), `size` + `price` from the **fill**,
  `market_title` only if known.
- `make_fill_observer(emit_fn, *, service, phase, meta_lookup=None) -> callable`:
  emits action `fill_recorded` with `fill_extra(...)` + best-effort `market_slug`;
  lookup failure still emits with empty name fields.
- `reconcile_orders(..., on_fill_recorded=None)`: invoked after a successful
  `record_fill()` (both fill paths) before `log_markout`, inside an isolated
  `try/except` that never alters persistence, counts, or status.
- `lifecycle_extra(result) -> dict` allow-list: `pair_id`, `condition_id`,
  `token_id`, `outcome` (renamed from result `side`), `venue_side` (SELL for
  `exited`, BUY for `completed`), `size`, `requested_size`, `fill_price`,
  `min_price`, `ask`, `pair_cost`, `notional`, `order_id`, `route`,
  `lifecycle_state`, `settlement_reason`. Never `response`.
- `relayer_extra(...)`: `condition_id`, `size` (only if requested),
  `relayer_state`, `transaction_hash` + `transaction_id` kept separate. Actions
  `merge_<status>` / `redeem_<status>`, status ∈ submitted|executed|failed|
  unknown|interrupted; service `query`, phase `settling`, `can_rotate=False`.
- Decide `extra` additions: `market_title` + `quotes` ([`{side, price, size}`],
  max 4, side names checked so no "buy BUY" text).
- Frontend: TRADES `data-filter="decide"` → `"trades"`; trade-marked actions =
  fills, exits, completions, rescues, all merge/redeem states, submits with
  `submitted > 0`. Market-name precedence: `extra.market_title` →
  `orderGroupMarket(condition_id)` → slug-as-words → "a market". Empty states:
  TRADES "No trades yet. Fills, sells, exits, merges and redeems show up here as
  they happen." / MARKET FILTER "No market filter updates yet." / ALERTS "No
  alerts right now." / ALL "No events yet." / after clear "Feed cleared. New
  events will appear here."

## Dependency graph

- T1: no dependencies (foundation).
- T2: Depends on T1 (same `cycle_stream.py` helper module + same poll-loop region).
- T3: Depends on T1 (same observer wiring pattern in Trader).
- T4: Depends on T1, T2, T3 (renders Phase-1 payloads; needs `extra` shapes).

## Tasks

### T1 — Fill telemetry end to end [x] [M] [Backend/Logic]

Target files: `core_brain/cycle_stream.py`, `core_brain/order_registry.py`,
`core_brain/order_manager.py` (poll loop ~2250), `core_brain/trader_loop.py` (~2731).
Build: `fill_extra` + `make_fill_observer` beside `emit()`; optional
`on_fill_recorded` in `reconcile_orders()` (both fill paths, isolated errors);
wire observer in poll loop (service `query`, phase `reconciling`) and in Trader
production reconcile wiring only. Keep `reconcile_ok` counts, port signature,
dedup, markout, status transitions.
Helper skills: `test-driven-development`, `incremental-implementation`.
Depends on: —.
Verification: `python -m pytest -q tests/test_cycle_stream.py tests/test_order_registry.py tests/test_trader_loop.py`
(RED first: new tests fail before the change). Checkpoint: fake fill reconciled →
one `fill_recorded` in ring, zero `cycle_intent` rows.

### T2 — Exit, completion, merge and redeem events [x] [M] [Backend/Logic]

Target files: `core_brain/cycle_stream.py`, `core_brain/order_manager.py`
(lifecycle ~2345, `_submit_and_log` 758–1041).
Build: `lifecycle_extra` + `relayer_extra`; enrich existing `lifecycle_*` event
(reason/error + slug, no second event); emit one `merge_*`/`redeem_*` after audit
status is written. Stub relayer HTTP/signing/balance in tests.
Helper skills: `test-driven-development`.
Depends on: T1.
Verification: `python -m pytest -q tests/test_cycle_stream.py tests/test_order_registry.py tests/test_order_manager.py`.
Checkpoint: stubbed relayer outcomes → `merge_executed`/`merge_failed`/
`merge_submitted`/`redeem_unknown` with unchanged audit statuses.

### T3 — Planned prices and title on decide events [x] [S] [Backend/Logic]

Target files: `core_brain/trader_loop.py` (~1940).
Build: add `extra.market_title` + `extra.quotes` (≤4) from existing intents;
keep reason, counts, condition id, `cycle_intent` rules.
Helper skills: `test-driven-development`.
Depends on: T1.
Verification: `python -m pytest -q tests/test_cycle_stream.py tests/test_trader_loop.py`.
Checkpoint: decide event carries quotes[0] `{side, price, size}` + title,
`intent_count` unchanged.

### T4 — Plain-English rows, TRADES filter, details toggle [L] [Design/UI + UX/Copy]

Target files: `dashboard/static/app.js` (~787–974), `dashboard/static/index.html`
(~220–242), `dashboard/static/styles.css`, new
`tests/js/event_stream_harness.cjs`, `tests/test_dashboard_server.py`.
Build: action-keyed sentence builders + trade markers + 3 prefix fallbacks;
decide/submit sentences; market-name helper; retain `extra` in buffered events;
TRADES → `data-filter="trades"`; two-line rows (sentence + muted raw line);
SHOW DETAILS toggle (state survives rebuilds); exact empty-state copy; escape
every inserted string; existing variables, no animation. Node harness from the
`format_local_time` pattern; wrappers skip when Node is missing.
Helper skills: `frontend-ui-engineering`, `humanizer`, `test-driven-development`.
Depends on: T1, T2, T3.
Verification: `python -m pytest -q tests/test_cycle_stream.py tests/test_dashboard_server.py`
+ Node harness assertions (service-independent TRADES, sentences, escaping,
empty states, autoscroll/details toggle). Checkpoint: feed
`query|fill_recorded` + skip-only `decide` → TRADES shows only the fill row.
Sub-issue: #<T4> (blocked by T1/T2/T3 issues).

Checkpoints every task (each task ends with a one-line progress note in Mode A).

## Improvement proposal (opt-in, scope expansion)

Evidence, verbatim from the issue: "`scripts/global_stop_loss.py` passes
`disable_rotation=True`, but `emit()` accepts only `can_rotate`.
`guardrail_alert` does not reach the ring today."
Proposal: file a follow-up issue for the guardrail/ring gap (ALERTS tab can never
show it). Not folded into this plan — enters only on operator approval.
