# Plan — #408: Re-check UMA resolution state for markets already quoting

Branch: i408/re-check-uma-resolution-state-for-markets-alread | Issue: #408

- Tier: **Large** — cross-cutting (`market_resolution`, `trader_loop`,
  `shadow_run`, `cycle_stream` confirm-only), one new reader + one new cache,
  no new dependency, no schema migration.
- Task type: **Code + Security** (money-protection gate: quoting a resolving
  market risks fills that settle against us).
- Stack: Python, pytest (`python -m pytest -q <file>` for focused runs).
- Skills: `test-driven-development` (RED-first regressions),
  `incremental-implementation` (thin slices, atomic commits),
  `security-and-hardening` (fail-open review lens: an outage must not
  mass-cancel, a misread must not fake "clean").
- Spec: `SPEC.md` (supersedes #402). Guardrails: `CONSTRAINTS.md`.
- CodeRabbit plan intake (read once; rendered copy only, HTML echo ignored):
  - Adopted: separate `fetch_uma_resolution_status` reader with a 3-way
    result (clean / flagged / unreachable) and mandatory condition-id match;
    caller-owned cache on the `AgedOutMarketStateCache` pattern (30s TTL,
    flagged pinned, unreachable uncached); optional seam ports
    (`fetch_uma_status`, `record_market_event`) so unset means old behavior;
    check first in `_visit_one` before `fetch_market`; cancel `open` +
    `partial` via the existing `cancel_fn`; per-status reasons
    `uma_resolution_proposed/disputed/resolved` for #402's list; `BLOCKED`
    `market_events` row per `order_manager.py` convention; unreachable warns
    and continues (fail open); no signer; borrowed test vectors for T1–T3.
  - Rejected/trimmed: 3-phase over-split merged into 4 atomic tasks (Rule 4
    — shadow wiring split out of the visit task so live and shadow land
    separately); line numbers treated as hints, re-verified at build time.
  - `[UNVERIFIED]` at plan time, verified from code ✓ or left for build:
    UMA parse/extract helpers (`market_resolution.py:98-141`) ✓;
    first-row-no-match gap (`:414`) ✓; `CANCEL_*` block
    (`trader_loop.py:74-77`) ✓; optional-port precedent (`flow_fn`, `:504-509`)
    ✓; fetch-first visit order (`_visit_one`, `:1055-1069`) ✓; shadow seam
    wiring shape (`shadow_run.py:760-779`) ✓; free-form `emit` action
    (`cycle_stream.py:364`, no filter to extend) ✓; 30s TTL cache pattern
    (`single_buy_saver.py:1366-1423`) ✓. Still for build to confirm: exact
    resting-order lookup to reuse for the open+partial cancel (saw
    `open_orders_fn` at `:1110`, dropped-market cleanup at `:833` — pick one,
    don't invent a third); `_make_logging_emit` insert point (`:410-458`
    seen); `order_manager.py` BLOCKED field convention (cited, not re-read).
  - Drift found while verifying: none material — cited seams exist where
    named. `fetch_uma_resolution_status` confirmed absent (new reader, no
    clash). Note: `MarketEndState`/`fetch_open_market_state` stay untouched.
- Open questions: none — no `needs-answers` label, no Open-questions section;
  issue names files, lines, and acceptance criteria explicitly.
- Skipped personae (recorded, not simulated): `code-explorer` — every cited
  seam verified by direct read/grep; a trace pass would duplicate evidence.
  `type-design-analyzer` — one 3-way result type, no non-trivial domain
  model; contract frozen below and in CONSTRAINTS.md.

## Interface lock

New (`core_brain/market_resolution.py`):

- `fetch_uma_resolution_status(gamma_host, condition_id, *, timeout=5.0,
  urlopen=...) -> clean | flagged(status) | unreachable` — same query
  (`condition_ids={cid}`), User-Agent, and envelope handling as
  `fetch_open_market_state`; row MUST match the requested condition id
  (mismatch = absent); row passes through `extract_uma_resolution_status`;
  `proposed`/`disputed`/`resolved` = flagged, nothing recognized = clean,
  network/decode/malformed = unreachable. Existing readers untouched.
- Caller-owned cache (model: `AgedOutMarketStateCache`):
  `UMA_RESOLUTION_STATUS_TTL_SEC = 30.0` beside the 60s sweep backoff;
  clean cached per TTL, flagged pinned (no expiry), unreachable/absent never
  cached; injectable clock.

Changed (signatures otherwise frozen):

- `trader_loop.py`: three reason constants `uma_resolution_proposed`,
  `uma_resolution_disputed`, `uma_resolution_resolved` beside `CANCEL_*`,
  marked as entries for #402's single discard list; optional `VenueSeam`
  ports `fetch_uma_status(cid)` + `record_market_event(...)` (absent = old
  behavior); `_visit_one` calls the UMA port BEFORE `seam.fetch_market` —
  clean continues, unreachable warns (`quoting/uma_check_unreachable`) and
  continues, flagged cancels open+partial via `cancel_fn`, emits one
  `quoting/discard` (cid, status, reason, cancelled/failed counts), writes
  one `market_events` row, returns non-error WITHOUT decide/submit; live
  builder wires both ports (one cached reader per run, near `_fetch_market`
  + live cancel adapter).
- `shadow_run.py`: shadow seam builder wires both ports (same cached reader
  shape; `record_cancel` stays the cancel adapter); `_make_logging_emit`
  logs one discard line (cycle, market, status, reason, cancelled count).
- `cycle_stream.py`: confirm-only — `emit` already takes any action.

Frozen: #377 gate, `fetch_pinned_market`, dropped-market cleanup, sweep,
pricing/sizing levers, enforce flags, `data/orders.db`, live venue cancel
path (never registry-only for live).

## Improvement proposal (adopted — hardening, evidence-based)

> The UMA check MUST run before `seam.fetch_market(cid)` in `_visit_one`,
> not after it — a CLOB outage on a flipped market must not skip the cancel.
> Evidence for: today's fetch-failure path returns before any cancellation —
> "try: market = seam.fetch_market(cid) except Exception as e: ...
> return LiveFleetResult(status="ERROR", ...)" (`trader_loop.py:1055-1069`).
> Any check placed after that fetch inherits the outage blind spot (the
> 17:04 episode quoted through an emptied feed for the same structural
> reason). Adopted by default (hardening, no scope change; converges with
> CodeRabbit's Design Choice 4).

## Dependency graph

- T1 → T2, T1 → T3 (reader + cache unblock the visit check and the shadow
  wiring).
- T2 → T3 (shadow builder reuses the ports + reason mapping T2 defines).
- T2 + T3 → T4 (rehearsal needs both modes wired).
- Checkpoint C1 after T1 (reader proven offline, no network in tests).
  Checkpoint C2 after T2 (flagged visit cancels with the named reason, clean
  visit byte-identical). Then T3, T4.

## Tasks

### T1 [x] — Gamma UMA reader + caller-owned cache [Backend/Logic] (M)

- Target files: `core_brain/market_resolution.py` (new reader + cache +
  TTL constant), `tests/test_uma_resolution_gate.py` (vectors).
- Build: `fetch_uma_resolution_status` + cache per Interface lock; reuse
  private request/envelope helpers if available; never touch
  `fetch_open_market_state` / `fetch_market_end_state`.
- Verify: `tests/test_uma_resolution_gate.py` — injected `urlopen` fake:
  clean rows; flagged `proposed`/`disputed`/`resolved`; mismatched cid =
  absent; empty listing = absent; network error + malformed envelope =
  unreachable; injected clock: clean reuse + TTL expiry, flagged pinned,
  unreachable uncached. Each new test FAILS without the change.
- Depends on: none.

### T2 [x] — Visit check + reasons + live builder [Backend/Logic] (M)

- Target files: `core_brain/trader_loop.py` (reasons, ports, `_visit_one`
  check, live builder), `tests/test_trader_loop.py` (stateful fake).
- Build: per Interface lock; reuse the existing resting-order lookup (no
  third lookup); cancel failures reported in the discard event and retried
  next visit (flagged stays pinned); no inventory/fill/close/merge change.
- Verify: `tests/test_trader_loop.py` — stateful `fetch_uma_status`
  clean→`proposed`: visit 1 places quotes; visit 2 cancels every resting
  order with `uma_resolution_proposed`, skips decide+submit, emits discard,
  calls `record_market_event` once; clean-repeat run byte-identical to a run
  without the port; unreachable warns without cancelling; failed cancels
  reported + retried. Checkpoint C2 after review.
- Depends on: T1.

### T3 [x] — Shadow builder + readable discard line [Backend/Logic] (S)

- Target files: `core_brain/shadow_run.py` (both ports in shadow seam
  builder, `_make_logging_emit` discard line), `core_brain/cycle_stream.py`
  (confirm-only, extend only if a filter exists).
- Build: same cached-reader shape as live; `record_market_event` calls
  `log_market_event()` on the shadow registry (`BLOCKED`, reason + status
  in details); absent port = skip the write.
- Verify: builder test — both ports set on the shadow seam; `caplog` shows
  one discard line with cycle/market/status/reason/count. Temp DBs only.
- Depends on: T2.

### T4 [x] — Shadow flip rehearsal, full targeted sweep [Backend/Logic] (M)

- Target files: `tests/test_shadow_run.py` (new rehearsal); source changes
  ONLY if the rehearsal exposes a wiring gap (no lever/gate change).
- Build: admit clean market → quote both legs → flip fake Gamma reader to
  `proposed` → feed refresh returns `[]` → next cycle: both rows cancelled
  with `uma_resolution_proposed`, one `market_events` row carries the
  reason, log names it, zero new orders placed.
- Verify: rehearsal green + focused sweep `test_uma_resolution_gate`,
  `test_trader_loop`, `test_shadow_run`. Full suite stays with CI.
- Depends on: T2, T3.

---
Supersedes: `tasks/plan.md` for #402 (work done, all tasks `[x]`).
`SPEC.md` / `CONSTRAINTS.md` likewise re-issued for #408.
Claim note: `gh issue edit 408 --add-assignee "@me"` failed twice (first
unquoted flag, then GitHub GraphQL internal error `B7F9:3036D9:2D34E5:35A0D5:6AC67900`);
assignee unset — operator or Station III may re-claim. Branch created locally.
