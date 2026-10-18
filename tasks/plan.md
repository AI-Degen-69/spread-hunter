# Plan — #656 Surface active trading breathing green pills in Traded Positions and Market Scans

Branch: i656/active-trading-green-pills | Issue: #656

Right-sizing: **Standard** (1 source file + 3 test files, one shared rule, one
architectural decision: where the "is this market being worked" rule lives).
Task type: **Design/UI + Core** (renderer markup; verified through the Node
render harnesses and a real browser view, plus source-level guards).

CodeRabbit intake (read once, on `gh issue view 656 --comments`):

- **Adopted:** the served-table-first order; the `stitchPositionsRows` scope
  addition (Assumption 1, verified below); "no new IDLE pill" (Assumption 2);
  the small shared helper (Assumption 4); the test matrix and the two
  `tests/test_dashboard_server.py` source checks.
- **Changed:** the show/not-show condition (see Improvement proposal) and one
  impossible assertion: its Task 1.2 test asks for `ot-pair-alt` on a
  single-market fixture, which never produces that class.
- **Rejected:** nothing material. `tests/js/orders_trades_harness.cjs` and
  `tests/js/live_marks_harness.cjs` are edited by no task.
- **Left `[UNVERIFIED]`:** none. Every file, function and line cited below was
  read directly in the live tree on this branch's HEAD `01157a3`.

---

## 1. Spec (Small/Standard — embedded; the stable pointer is `SPEC.md`)

### The problem, in operator terms

A held market that the engine is actively quoting or holding orders on looks
exactly like a dead one in ORDERS & TRADES → OPEN POSITIONS. To find out,
the operator leaves the tab and cross-checks ACTIVE MARKETS.

### The change

OPEN POSITIONS shows the same green RESTING / QUOTING pill the ACTIVE MARKETS
tab already shows, on the held market that the engine is working. Everything
else about the row is unchanged.

### Acceptance criteria (from the issue) and where each is satisfied

| # | Criterion | Satisfied by |
|---|---|---|
| 1 | Held positions whose market is actively quoted or has resting orders display `.pill.resting-breathing` or `.pill.quoting-breathing` | T1 (served table) + T2 (classic table) |
| 2 | Idle or finished markets keep their standard non-animated tags | T1.4, T1.6, T2.4 — no pill is emitted at all; no IDLE pill exists in this view |
| 3 | `prefers-reduced-motion: reduce` keeps suppressing the animation | Already true in `dashboard/static/styles.css:599-603`; nothing in this change touches CSS. Pinned by T3's guard test |
| 4 | The named acceptance command passes | T3 |

### Explicitly out of scope

- Market Scan / Kanban cards. The issue title names them; the "In scope" list
  and all four acceptance criteria cover positions only. The follow-up needs
  `state` threaded into `renderScreener` (`app.js:9776`) and activity added to
  the board fingerprint; neither is in this change.
- `dashboard/static/styles.css`, `dashboard/static/index.html`,
  `dashboard/static/prototype.js`, `dashboard/static/prototype.css`,
  `dashboard/server.py`, everything under `core_brain/`, `data/orders.db`.
- Active Markets: `marketStatusPill`, `activeMarketsRows`,
  `stitchActiveMarketsRows`, `isQuotedMarket`, `isRestingOrder`,
  `heldMarketEntries`, `paintLiveMarks` — all read, none edited.

---

## 2. Verified seams (line numbers on HEAD `01157a3`)

| Seam | Where | Verified fact |
|---|---|---|
| `marketStatusPill(m, restingHere)` | `app.js:7286-7291` | returns `pill resting-breathing` / `pill quoting-breathing` / `pill stopped` (IDLE) |
| `isRestingOrder(o)` | `app.js:7033-7036` | true for `open`, `partial`, `pending` (case-insensitive) |
| `isQuotedMarket(m, orders)` | `app.js:7029-7032` | `(m.quotes_count || 0) > 0 \|\| orders.some(isActiveOrder)`; false for finished markets |
| `isActiveOrder(o)` | `app.js:6344-6346` | `!isCancelledStatus(status) && !isMergedOrder(o)` — much broader than "resting" |
| `groupOrdersByMarket(orders)` | `app.js:6291-6300` | `{condition_id: [orders]}` |
| `positionsRows(kpi, state, sort)` | `app.js:7666-7759` | market `<td class="ot-market" rowspan>` built at **7735**; `pairTags` at 7723; render map opens at 7713 |
| `stitchPositionsRows(kpi, state, sort)` | `app.js:8343-8516` | **`state` is accepted and never used** (only `kpi` is); market cell at **8453-8462** |
| Dispatcher | `app.js:8940-8961` | `proto-body` + `positions` → `stitchPositionsRows`; otherwise → `ordersTradesRows` → `positionsRows` |
| `proto-body` | `prototype.js:27,579,603` | added on `/` and `/prototype` → the served page always takes the **stitch** path |
| Breathing CSS | `styles.css:574-598` | both pills blink with `hb-blink 1s steps(1, end) infinite` |
| Reduced motion | `styles.css:599-603` | covers `.pill.resting-breathing::before`, `.pill.quoting-breathing.resting::before`, `.pill.quoting-breathing::before` → `animation: none` |
| `@keyframes hb-blink` | `styles.css:433` | `0%,49.9% {opacity:1} 50%,100% {opacity:.15}` — not edited |
| `paintLiveMarks` | `app.js:6868-7033` | rewrites only `td[data-cell="value"|"cur-price"|"unrealized"]` inside `#orders-trades-body` |
| Exports | `app.js:10498`, `app.js:10527` | `marketStatusPill, isQuotedMarket, isRestingOrder` exported; `stitchPositionsRows` exported |

---

## 3. Interface contract (locked before code)

### 3.1 New function — the single shared rule

```js
/* One rule for both held-positions tables: the Active Markets activity pill,
 * and only for a market the engine is actually working. */
function marketActivityPill(m, orders) -> string   // '' | '<span class="pill …">…</span>'
```

- **Placed** directly after `marketStatusPill` (`app.js:7292`, between line
  7291's `}` and line 7293's `function activeMarketsRows`).
- **Exported** by adding `marketActivityPill,` to the export line that already
  carries `marketStatusPill` (`app.js:10498`), so the precedence rule can be
  pinned directly.
- **Show/not-show condition — identical, term for term, to the ACTIVE MARKETS
  membership test** (`app.js:7296-7299`):

  ```js
  const list = orders || [];
  if (!isQuotedMarket(m, list)) return '';
  const restingHere = list.some(o => isRestingOrder(o));
  if (!restingHere && !(Number(m && m.quotes_count) > 0)) return '';
  return marketStatusPill(m, restingHere);
  ```

  A resting order implies `isActiveOrder`, so this is equivalent to
  "resting here, or `quotes_count > 0`" — and it cannot drift from the tab it
  mirrors.
- **Never returns the IDLE pill**: reaching `marketStatusPill` means `restingHere`
  or `quotes_count > 0`, so the third branch is unreachable.
- `marketStatusPill`, `isQuotedMarket`, `isRestingOrder`,
  `groupOrdersByMarket`, `heldMarketEntries` are **not modified**.

### 3.2 DOM contract — classic table (`positionsRows`)

Inserted inside the existing market cell, on the `ot-pair-start` leg row only:

```html
<td class="ot-market" rowspan="N">${marketCell(...)}${activity}${pairTags}</td>
```

- `activity` is `''` when idle → byte-identical markup to today.
- **No** new column, **no** `data-cell`, **no** `data-cid`, **no** `live`
  class on the pill or its cell.

### 3.3 DOM contract — served stitch table (`stitchPositionsRows`)

A new sibling of the existing `.stitch-market-meta` rows, rendered only when
non-empty:

```html
<div class="stitch-market-meta" style="margin-top:2px;">${activity}</div>
```

…placed between the `.stitch-market-title` `</div>` (line 8456) and the
category-pill conditional (line 8457). The PAIRED / PARTIAL / UNPAIRED badge,
`data-cell` cells, `rowspan`s and the 8-column head are untouched.

### 3.4 Invariants the tests pin

1. Idle held market ⇒ no `resting-breathing`, no `quoting-breathing`, no `pill stopped`.
2. RESTING beats QUOTING on the same market.
3. Orders belonging to another market never mark the held one.
4. Finished markets stay out (`heldMarketEntries(kpi, false)` already excludes them).
5. Column set, row count and band classes are unchanged; the pill is never inside a `data-cell`.

---

## 4. Improvement proposal (Step 5 — one, evidence-based)

**Evidence (verbatim, `app.js:7296-7299`, the ACTIVE MARKETS filter):**

```js
    .filter(([cid, m]) => isQuotedMarket(m, ordersByMarket[cid])
      && ((m.quotes_count || 0) > 0
        || (ordersByMarket[cid] || []).some(o => isRestingOrder(o))));
```

**Proposal (simplification + edge-case hardening — adopted by default):** the
new helper reuses that exact condition — `isQuotedMarket` plus the same
`quotes_count || resting` term — instead of a fresh `quotes_count > 0` test.
Two tabs that answer "is this market being worked?" then share one definition
and cannot disagree about a held market, and the IDLE branch stays unreachable
by construction rather than by convention. Cost: two lines. `activeMarketsRows`
itself is not edited.

**Rejected proposal, recorded so it does not resurface:** extracting the shared
predicate out of `activeMarketsRows` into a named function used by both call
sites. Rejected — the issue's scope guard names the Active Markets renderer as
must-not-change; the reuse above gets the same guarantee with no edit there.

---

## 5. Tasks

Dependency graph: `T1 → T2 → T3` (T2 needs T1's helper; T3's source checks
assert both renderers).

### T1 — the shared rule + the served Stitch Positions table  [Design/UI]

- **Size:** S · **Depends on:** — · **Files:** `dashboard/static/app.js`,
  `tests/test_stitch_positions_marks.py`
- **Build:**
  1. `app.js:7292` — add `marketActivityPill` exactly as specified in §3.1.
  2. `app.js:10498` — add `marketActivityPill,` to the export line beside
     `marketStatusPill`.
  3. `app.js:8349` — in `stitchPositionsRows`, **after** the empty-state early
     `return` at 8346-8348 (so the empty view does no work) and before the
     `const rows = entries.map(...)` at 8350, add
     `const ordersByMarket = groupOrdersByMarket(state && state.orders);`
     — this is what finally uses the renderer's `state` argument.
  4. `app.js:8381-8385` — the per-market row object gains
     `activity: marketActivityPill(m, ordersByMarket[cid]),`.
  5. `app.js:8409-8413` — add `activity` to the destructuring parameter list of
     `sorted.map(({ … }) => {`.
  6. `app.js:8456-8457` — insert
     `${activity ? `<div class="stitch-market-meta" style="margin-top:2px;">${activity}</div>` : ''}`
     between the `</div>` that closes `.stitch-market-title` and the
     category-pill conditional. **Anchor the edit on the multi-line boundary**
     (`</div>` + newline + the `${(!category || category === 'Uncategorized') ? '' : `<div class="stitch-market-meta" style="margin-top:2px;">` line):
     the stitch ACTIVE MARKETS cell (8242-8250) has the same shape without that
     inline `margin-top:2px`, and `stitchClosedTradesRows` (8813-8820) has a
     third variant. The three must not be confused.
- **Tests (`tests/test_stitch_positions_marks.py`, inline Node `_eval_js`,
  `app.stitchPositionsRows(kpi, state, null)`):**
  - Fixture: `_kpi()` gains a `quotes_count=0` parameter and emits
    `"quotes_count": quotes_count` on `0xabc`. Default `0` keeps every existing
    test in the file passing untouched.
  - `test_market_activity_pill_prefers_resting_and_stays_empty_when_idle` —
    direct calls: `app.marketActivityPill(m, [])` for `{quotes_count: 0}` → `''`;
    `{quotes_count: 2}` → contains `quoting-breathing`; `{quotes_count: 2}` with
    one `open` order → contains `resting-breathing` and not `quoting-breathing`.
  - `test_stitch_positions_resting_order_shows_resting_pill` — parametrized
    `open`, `partial`, `pending`, `Open`; one matching order, `quotes_count: 0`
    → `class="pill resting-breathing"` and `>RESTING<`.
  - `test_stitch_positions_resting_wins_over_quoting` — one `open` order plus
    `quotes_count: 3` → `resting-breathing` present, `quoting-breathing` absent.
  - `test_stitch_positions_quoting_only_shows_quoting_pill` —
    `quotes_count: 1`, orders only `filled` and `cancelled` →
    `quoting-breathing` present, `resting-breathing` absent.
  - `test_stitch_positions_idle_market_keeps_standard_tags` — `quotes_count: 0`,
    no orders → no `resting-breathing`, no `quoting-breathing`, no
    `pill stopped`, and `stitch-status-badge` still present.
  - `test_stitch_positions_other_market_orders_do_not_mark_held_market` — one
    `open` order on `0xdef`, `quotes_count: 0` on `0xabc` → no breathing class.
  - `test_stitch_positions_finished_market_has_no_activity_pill` — `0xabc` with
    `resolved: true` plus an `open` order → `No legs have filled` empty state,
    no breathing class.
  - Extend `test_live_overlay_moves_the_per_leg_lines` (currently lines
    122-140): give the fixture `quotes_count: 2`, keep its live-mark
    assertions, then add — the pill is still present after the marks apply, and
    it sits before the first `data-cell` in the output
    (`html.split('data-cell')[0]` contains `quoting-breathing`).
- **Verify:** `python -m pytest -q tests/test_stitch_positions_marks.py`
  (RED before the code, GREEN after).

### T2 — the pill on the classic `positionsRows` table  [Design/UI]

- **Size:** S · **Depends on:** T1 · **Files:** `dashboard/static/app.js`,
  `tests/test_orders_trades_table.py`
- **Build:**
  1. `app.js:7670` — **after** the empty-state early `return` at line 7669, add
     `const ordersByMarket = groupOrdersByMarket(state && state.orders);`
     (once per render, matching `activeMarketsRows`, `ordersTradesCounts` and
     `renderOrdersTrades`, which each already do this).
  2. `app.js:7724-7726` — inside the per-market `sorted.map` callback, after
     `const span = legs.length;` and before `return legs.map(...)`, add
     `const activity = marketActivityPill(m, ordersByMarket[cid]);`.
  3. `app.js:7735` — `…${marketCell(m, cid, { categoryCaption: true })}${activity}${pairTags}</td>`.
  - No new column (`otSortGroups` switch, cases 0-8, `app.js:7695-7709` — each
    case maps to a fixed column),
    no background on the pill or the cell (banding is `.ot-pair-alt > td`,
    `styles.css:1881`), no finished-market logic (`heldMarketEntries` handles it).
- **Tests (`tests/test_orders_trades_table.py` via
  `tests/js/orders_trades_harness.cjs`, `_render('positions', kpi, state)`):**
  - Shared fixtures are reused as-is. `CID_HELD` already carries
    `quotes_count: 2` with only `filled`/`cancelled` orders; `CID_QUOTED`
    carries the two resting orders (`partial` + `open`) and holds nothing.
  - Slice per market: `html.split('ot-pair-start')[1]` for a single held market
    (assert `'0xheld'` is in the slice before asserting on it), and the
    `split('ot-pair-start')[1]` / `[2]` blocks for the two-market banding test.
  - `test_positions_quoting_held_market_shows_quoting_pill` — baseline render →
    `class="pill quoting-breathing"` in the slice, `resting-breathing` absent.
  - `test_positions_held_market_with_resting_order_shows_resting_pill` — deep-copy
    `_kpi()`/`_state()`, append an `open` order on `CID_HELD` →
    `resting-breathing` present, `quoting-breathing` absent.
  - `test_positions_unrelated_market_resting_orders_do_not_mark_held_market` —
    baseline fixtures → no `resting-breathing` in the `CID_HELD` slice.
  - `test_positions_idle_held_market_keeps_standard_tags` — `CID_HELD`
    `quotes_count` set to `0` → no `resting-breathing`, no `quoting-breathing`,
    no `pill stopped`; `class="pill active"` (UP leg) and the `ot-tag` pair
    tags still present.
  - `test_positions_activity_pill_keeps_banding_and_live_cells` — baseline plus
    an `open` order on `CID_HELD`, and a **second** held market cloned under a
    new condition id (`quotes_count: 2`, no orders, `total_sh` non-zero) so the
    second market renders the `ot-pair-alt` band:
    `rendered["columns"]` equals the unchanged positions list
    `['Timestamp','Market','Leg','Size','Avg Price','Cost','Mark Value','Unrealized','Realized']`;
    `ot-pair-start` and `ot-pair-alt` are both present; `data-cell="value"` and
    `data-cell="unrealized"` are present; the `ot-pair-alt` slice contains the
    quoting pill; and no `data-cell` cell contains a breathing class.
  - `tests/js/orders_trades_harness.cjs` is **not** edited (it already passes
    `kpi`, `state` and `view`).
- **Verify:** `python -m pytest -q tests/test_orders_trades_table.py tests/test_positions_live_marks.py`
  (the second file drives the same classic path through
  `tests/js/live_marks_harness.cjs` and must stay green with no edits).

**Checkpoint A** (after T1): `tests/test_stitch_positions_marks.py` green with
the new tests → the served table shows the pill.
**Checkpoint B** (after T2): both positions files green → both tables agree.

### T3 — source checks, reduced-motion guard, and the full focused run  [Core]

- **Size:** XS · **Depends on:** T1, T2 · **Files:** `tests/test_dashboard_server.py`
- **Build:**
  - `test_positions_renderers_wire_activity_pill` — read `app.js`, cut the body
    of `positionsRows` and of `stitchPositionsRows` (from `function <name>(`
    to the next `\nfunction `), assert each body contains `marketActivityPill(`
    and `groupOrdersByMarket(`. Fails on HEAD, passes after.
  - `test_styles_css_reduced_motion_covers_breathing_pills` — read
    `styles.css` via `_read_static("styles.css")` (helper at line 45) and select
    the `@media (prefers-reduced-motion: reduce)` block **that contains
    `.pill.resting-breathing::before`** (`styles.css:599-603`) — the file has
    seven such media blocks, so select by content and never by the first match;
    assert it also covers `.pill.quoting-breathing.resting::before` and
    `.pill.quoting-breathing::before` with `animation: none`; assert both
    breathing rules (`styles.css:575-598`) still declare
    `animation: hb-blink 1s steps(1, end) infinite` and that
    `@keyframes hb-blink` (`styles.css:433`) is unchanged. **This is a boundary
    guard: it passes on HEAD and must pass after.** It is the `hb-blink` /
    reduced-motion tripwire the issue's criteria 3 rests on. (Existing patterns
    to follow: `tests/test_dashboard_server.py:1338-1392` for source slices,
    1696-1701 for the reduced-motion JS precedent, and
    `test_styles_css_has_kanban_styles` at 1703-1744 for `styles.css` asserts.)
  - `test_app_js_has_expandable_market_rows` (1338) and
    `test_styles_css_has_expandable_row_styles` (1383) are left untouched.
- **Verify (all four green during development and again after review fixes):**
  1. `python -m pytest -q tests/test_stitch_positions_marks.py tests/test_orders_trades_table.py tests/test_positions_live_marks.py tests/test_stitch_active_markets.py tests/test_sidebar_prototype.py tests/test_dashboard_input_lag.py`
  2. `python -m pytest -q tests/test_dashboard_server.py -k "test_positions_renderers_wire_activity_pill or test_styles_css_reduced_motion_covers_breathing_pills"`
  3. the issue's acceptance command:
     `python -m pytest -q tests/test_dashboard_server.py -k "test_app_js_has_expandable_market_rows or test_styles_css_has_expandable_row_styles"`
  4. Node must be present or every harness test skips silently:
     `node --version` (v24.14.1 on this host).

**Checkpoint C** (after T3): all four runs above green; the full
`python -m pytest -q` suite stays with GitHub CI on Ubuntu and Windows.

---

## 6. Baseline recorded on HEAD `01157a3` (before any edit)

- `tests/test_stitch_positions_marks.py tests/test_orders_trades_table.py tests/test_positions_live_marks.py`
  → **134 passed** in 8.68s.
- Six focused suites together → **194 passed** in 10.76s.
- Acceptance command → **2 passed, 128 deselected**.

Any of these failing after the change is a regression this station caused.

---

## 7. Constraints

See `CONSTRAINTS.md`: zero regressions, no new dependencies, no skipped or
weakened tests, no CSS/keyframe edits, no suppression flags, and the DOM
contract of §3.4 as a hard boundary.

---

## 8. Noticed but not touching

Recorded in `docs/issues/656-noticed-but-not-touching.md` (owner + timeline per
row): the stale root `SPEC.md` inherited from issue #632, the
`test_dashboard_server.py:734-754` line drift, the third un-pilled
`ot-market` cell in `openOrdersRows` (`app.js:7539`), and the fact that
`quotes_count` is a whole-run counter so a market the engine has stopped
quoting can still read QUOTING (same behaviour as ACTIVE MARKETS today).

---

## 9. How to verify (operator, hands-on — nothing here spends money)

Read-only dashboard on a shadow store; no menu `start`, no dashboard START, no
live Trader, no `quote`, no `complete`.

1. Start: `python -m dashboard.server --db data\04_shadow_24-09_test.db --port 8799`,
   then open `http://127.0.0.1:8799`. The page shows the SHADOW badge.
2. Home → **ORDERS & TRADES** → **OPEN POSITIONS**. A held market the engine is
   working now carries a green **RESTING** or **QUOTING** pill with a blinking
   dot beside its title. Failure: no pill on a market that ACTIVE MARKETS lists
   as RESTING/QUOTING, or the zebra row bands look broken.
3. Compare the same market on **ACTIVE MARKETS**: the two tabs must never
   disagree about RESTING vs QUOTING. Failure: they disagree.
4. A held market with no quotes and no resting orders looks exactly as before —
   no green pill, no new grey IDLE pill. (If the store has no idle held market,
   skip.) Failure: it grew a pill.
5. Watch **MARK VALUE** and **UNREALIZED P&L** for a few seconds: prices still
   change and the pill stays put. Failure: the pill disappears or moves when a
   price updates.
6. Turn on your OS "reduce motion" setting and reload: the pills stay green and
   the dot stops blinking. Failure: the dot still blinks.

---

## 10. Assumptions and risks (one line each)

- `proto-body` is always on for the served page, so the stitch table is the one
  the operator sees; the classic table is still changed because the ticket names
  `positionsRows()` and the harnesses drive it.
- `quotes_count` counts quotes logged in the selected run, so a market the
  engine has stopped quoting can read QUOTING — identical to ACTIVE MARKETS
  today, unchanged here.
- `state.orders` spans the whole database while `kpi.by_market` spans one run, so
  a resting order from another run in the same DB can mark a held market
  RESTING — identical to ACTIVE MARKETS today.
- Node-based tests skip when `node` is missing; a skip is not a pass.
- A shadow store may contain no active markets; showing no green pill there is
  the correct result, not a failure.
