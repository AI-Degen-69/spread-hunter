# Plan — #417: shadow_fills: clear queue and fill order on a sell print below the resting bid

Branch: i417/shadow-fills-clear-queue-and-fill-order-on-a-sell | Issue: #417

- Tier: **Small** — one function (`credit_fills`) in one file plus its test file; straightforward once the code is read.
- Task type: **Code** — fill-model rule + regression tests.
- Stack: Python, pytest; no external dependency.
- CodeRabbit plan: **adopted as scaffolding, merged down** — its 3 phases (RED tests, GREEN impl, regression + operator check) become T1–T3 below; its 4 design choices adopted (full-sweep default, no side check, rounded-level compare, evidence not consumed); its `[UNVERIFIED]` on the `math` import resolved from code (`shadow_fills.py:14` already imports it — no import needed); its line pointers spot-checked (`shadow_exec.py:632` call site, `markets.py:551 recent_trades`, telemetry comment `shadow_exec.py:585` — all verified verbatim). Rejected: nothing material — only merged its task splits into fewer atomic tasks per Rule 4.
- Open question resolved from code (issue's sweep-vs-cross question): `traded` is aggregated per token per price with no ordering or size (`credit_fills` lines 102–106 sum into `remaining_volume`), so per the issue's stated default, any sell print strictly below the resting bid counts as a full level-clearing sweep filling the entire remainder. No operator question needed.
- Improvement proposal (adopted, edge-case hardening): require the lower bucket to hold **finite** volume greater than 0 (`math.isfinite`), so NaN/inf tape can never count as sweep evidence. Evidence: `tests/test_shadow_fills.py` lines 79–84 — `assert queue_multiple(float("nan"), 5.0) is None` — the repo already treats non-finite tape as unmeasured, and the plan's own rationale says the guard "rejects empty or broken buckets".
- Safety: do not open or rewrite `data/orders.db`; tests use temporary DBs. No live quoting, Trader loop, manual completion, or dashboard START.

## Locked behavior (spec, embedded — Small tier, no SPEC.md)

- A sell tape print strictly below the resting bid (`trade.price < resting_bid`, compared at rounded 4-decimal levels) clears that order's `queue_ahead` to 0 and credits its full remaining size at the order's own price.
- Exact-price fills and the consume-queue-first rule are unchanged.
- Prints above our price, other tokens' prints, zero-volume buckets, and non-finite buckets credit nothing new.

## Interface contracts (frozen)

- `credit_fills(orders, traded) -> (fills, queues)` signature unchanged.
- `ShadowFill` for a trade-through fill carries the order's own price and `o.remaining`, never the print price.
- `traded` shape unchanged: `token -> price -> volume`.
- One fill per order per call (caller derives `filled`/`partial` status from `order.filled + f.size`).

## Dependency graph

- T1 → T2 → T3

## Tasks

### T1 [x] — RED: trade-through tests that fail on current code [Backend/Logic] (S)

- Target files: `tests/test_shadow_fills.py`
- Build: replace `test_volume_at_another_price_or_token_credits_nothing` (its `{"tok-up": {0.46: 999.0}}` tape becomes a fill under the new rule, so it must split) with two no-fill tests — other-token tape `{"tok-dn": {0.46: 999.0, 0.47: 999.0}}` and above-price tape `{"tok-up": {0.48: 999.0}}`. Add: main case (`queue_ahead=500`, tape `{"tok-up": {0.46: 1.0}}` → one `ShadowFill("ord-1", "tok-up", 0.47, 100.0)`, queue `0.0`); remainder-only (`filled=30` → fill `70.0`); already-full (`filled=100` → no fill, queue `0.0`); evidence-not-consumed (two orders at 0.47 → two fills of `100.0`); only-higher-orders-fill (`ord-2` at 0.45 keeps queue `10.0`); zero-volume (`{0.46: 0.0, 0.47: 100.0}` → exact-price fill `40.0`); rounding boundary (`0.46999` → no fill, queue `35.0`); non-finite bucket (`{0.46: inf}` → no trade-through fill). Use only the `_order(**kw)` helper and plain-dict tape; leave exact-price/queue/size-cap/oldest-first tests untouched.
- Helper skill: `test-driven-development`.
- Depends on: nothing.
- Verify: `python -m pytest -q tests/test_shadow_fills.py` — the new trade-through tests FAIL, the no-fill/boundary tests pass.

### T2 [x] — GREEN: trade-through check inside `credit_fills` + docstrings [Backend/Logic] (S)

- Target files: `core_brain/shadow_fills.py`
- Build: keep the normalized `remaining_volume` map; per order, before the exact-price step, check for a same-token rounded price strictly below the order's rounded price with finite volume > 0. On hit: set `queues[o.local_id] = 0.0`, emit one `ShadowFill` at own price for `o.remaining` if > 0, skip the exact-price step, do not reduce the lower bucket. On miss: current flow exactly. Update the module docstring (one trade-through sentence, keep the rehearsal-only + venue-confirmation statements) and the `credit_fills` docstring (both cases: exact-price queue-first, lower-price full remainder at own price). Touch nothing in `shadow_exec.py`, `markets.py`, or `live_fill_engine.py`.
- Helper skill: `incremental-implementation`.
- Depends on: T1.
- Verify: `python -m pytest -q tests/test_shadow_fills.py` — all green.

**Checkpoint:** trade-through fills credit at own price with queue zeroed; exact-price behavior untouched. Demonstrate with the T3 operator call.

### T3 [ ] — Regression: caller suite + hands-on operator check [Backend/Logic] (XS)

- Target files: none (verification only).
- Build: run `python -m pytest -q tests/test_shadow_exec.py` unchanged (its tapes use own-price levels only — verified in Station II — so it must pass as-is). Hands-on check: `python -c` importing `credit_fills` + `ShadowRestingOrder`, one order (`price=0.47, size=100, filled=0, queue_ahead=500`), tape `{"tok-up": {0.46: 1.0}}` → expect one fill at 0.47 size 100.0, queue 0.0.
- Helper skill: none (verification).
- Depends on: T2.
- Verify: both focused suites green; operator call prints the expected fill.
