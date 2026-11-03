Branch: i925/trim-open-positions-table | Issue: #925

# Plan — #925 Trim the open positions table

- Issue: [#925 — [IMPROVE] Trim the open positions table to held inventory, both-side prices and one colour-coded P&L](https://github.com/AI-Degen-69/spread-hunter/issues/925)
- Size tier: **Standard** — one existing stitch table reshaped across `dashboard/static/app.js`, `dashboard/static/prototype.css` and pinned tests; one architectural decision (one both-legs price source shared by render and repaint). Not Large: no dependency, schema or public API change.
- Task type: **UI / Design + Code** (`[Design/UI]` tasks; JS logic verified by the repo's Node-backed pytest harness and a browser render).
- Repo state: worktree is on `i925/trim-open-positions-table`, branched from `main` at `091f8687`; tree was clean before claim. Issue assigned to `@me`.
- Stale plans/constraints checked: `tasks/plan.md`, `tasks/todo.md`, `tasks/pr-body.md`, `CONSTRAINTS.md`, `SPEC.md` — none existed, so nothing was replaced.
- Pre-flight helper `scripts/issue-intake.js` is absent in this repo; intake was done with `gh issue view`, `git status`, direct file reads and targeted grep.
- `~/.config/opencode/skills/ii-plan-issue` was read for the station; `~/.agents/main.md` was not found and is not required for this plan.

## CodeRabbit intake note (read once; rendered copy only)

CodeRabbit plan comment exists (`2026-11-03T02:57:56Z`); the HTML echo was ignored.

- Adopted: six columns in the issue order; keep existing sort keys and reject removed keys with a stitch-only allowlist; replace the held-only price source with a both-sides helper; share one price-line and one P&L formatter between render and repaint; remove the dead `.stitch-mark-val` branch in repaint; keep the classic plain-text repaint path; one shared formatter prints `--` / empty pct / `flat` when value is null.
- Rejected: renumbering sort keys `0..5`; a `proto-body` guard around all of `paintLiveMarks`; deleting the classic `data-cell="value"` repaint path; keeping unused per-leg row fields; removing `.stitch-pair-cost` (Closed Trades still uses it); splitting this cohesive ready-for-agent issue into sub-issues.
- `[UNVERIFIED]` resolved: the plan cites "Assumption 5" but never defines it. Resolved from current code: `unrealized === null` renders `--`, empty percentage and flat tone; repaint matches that shape.
- Verification of cited seams: `stitchPositionsHeadHtml` at `dashboard/static/app.js:10601`; `stitchHeldLegMarks` at `:10634`; `stitchPositionsRows` at `:10655`; `paintLiveMarks` at `:9036`; resting rung at `:10100-10104`; stitch call site at `:11374-11376`; `.stitch-mark-val` only at `:9060`, `:10826`, `prototype.css:1577`; `.stitch-pair-cost` still used by Closed Trades at `:11262`. Issue line ranges remain stale by default; paths and symbols are the locked seams.

## Resolved open questions (from issue; resolved from code)

1. Resting hedge rung — keep `N resting @ $P`, delete only the hedge-working suffix. Code confirms `dualLegLine` is the only place with the wording and Positions is the only caller passing `resting`.
2. Missing side with no quote — always render YES then NO; print `--` for a side with no observed mid. Code confirms `liveLegMids` → `latestLegMids` returns `null` mids, and `liveMarkMid` never invents a price.
3. Dropping HEDGE STATUS — keep `is-hedge-pending` row tint; delete only the badge cell/CSS usage in this table. Code confirms Closed Trades/Orders still use `.stitch-status-*`, so the rules stay.
4. Unrealised P&L — pair total only: one signed dollar value and one parenthesised percentage, coloured pos/neg/flat, computed from `positionMarkValue` minus `total_cost`; no per-leg lines; no `Total` label.

## Interface contract (locked before build)

Surface: stitch terminal only (`<body class="proto-body">`). Classic `positionsRows` is out of scope.

- Header `stitchPositionsHeadHtml(sort)` emits exactly six `<th>` in this order:
  `MARKET & CLASSIFICATION` key `1` → `CATEGORY` key `2` → `HELD INVENTORY` key `3` → `CURRENT PRICE` key `9` → `UNREALISED P&L` key `7` → `LAST UPDATED` key `0`.
- Stitch sort contract: keys accepted by header/rows/painter are `{0,1,2,3,7,9}`. Any other key is treated as no sort: no `aria-sort`, no active arrow, default row order (`totalCost` desc, then `ts` desc). Do not renumber keys. Remove switch cases `4`, `5`, `6`, `8`. Key `9` keeps its meaning: held YES mid, else held NO mid.
- Rows emit six `<td>` in the same order. Empty state uses `colspan="6"`.
- CURRENT PRICE cell keeps `data-cid`, `data-cell="cur-price"`, the `live` class and the live-age title. It always renders two lines, YES then NO, as `.stitch-cur-up` / `.stitch-cur-down` with `data-leg="YES|NO"`. Each line prints `YES $0.76` / `NO $0.24` or `YES --` / `NO --`; the `stitch-cur-live` dot appears only on a side with a fresh live mark.
- UNREALISED P&L cell keeps `data-cid`, `data-cell="unrealized"`. It contains one `.stitch-pnl-cell`, one `.stitch-pnl-val`, one `.stitch-pnl-pct`, each with `pos|neg|flat`. Value is `positionMarkValue(...) - Number(m.total_cost) || 0`; percentage is `value / cost * 100` only when `cost > 0`; missing value prints `--`, empty percentage, `flat`.
- `paintLiveMarks` updates the same two price lines and the same single value/percentage pair in place. It must not contain `.stitch-mark-val`, `.stitch-leg-pnl`, or `.stitch-pnl-total` after the change. Classic `data-cell="value"` plain-text path stays untouched. No whole-function `proto-body` guard.
- Removed from stitch positions body: `TOTAL COST`, `MARK VALUE`, `HEDGE STATUS`, `stitch-pair-cost` usage in this table, `.stitch-mark-val`, `.stitch-leg-pnl`, `.stitch-pnl-total`, `N sh total`, hedge-working wording, `stitch-status-badge` in this body.
- CSS: remove `.proto-body .stitch-mark-val` only after a final grep shows no users; keep `.stitch-pair-cost`, `.stitch-mid-sub`, `.stitch-status-*`, `.stitch-pnl-*`.

## Dependency graph

T1 reshapes the render contract. T2 makes the in-place repaint consume that same contract. T3 locks the classic surface and runs the focused regression.

## Tasks

### T1 — Reshape the stitch positions render path — Size: M — `[Design/UI]`
- Depends on: none
- Owns: `dashboard/static/app.js`, `dashboard/static/prototype.css`, `tests/test_stitch_positions_marks.py`
- Build: add the stitch sort allowlist + helper; six-column header; both-legs price helper and shared line/P&L formatters; remove `stitchHeldLegMarks` and its exports; reshape rows; change empty `colspan` to 6; drop hedge-working wording; remove `.stitch-mark-val`; keep `positionMarkValue` and `is-hedge-pending`.
- Tests first: update `tests/test_stitch_positions_marks.py` to the new contract, including leftover sort keys, missing-side `--`, zero cost basis, flat/pos colour, key 7 sort, and absence of `Total` / per-leg P&L / badges / hedge-working wording.
- Verification: focused RED → GREEN on `tests/test_stitch_positions_marks.py`; browser check happens at T2 checkpoint.

### T2 — Make the live repaint match the new cells — Size: S — `[Design/UI]`
- Depends on: T1
- Owns: `dashboard/static/app.js`, `tests/test_dashboard_server.py`
- Build: in `paintLiveMarks`, on the stitch surface run the stored sort through the allowlist; keep keys `6`/`7`/`9` full-render check for classic safety; delete only the `.stitch-mark-val` branch while keeping the classic plain-text path; update both price lines via the T1 helper; delete per-leg and pair-total P&L branches; keep the single value/percentage branch and format it with the shared formatter; keep the classic `else` branch; keep the no-fresh-live-side skip.
- Tests first: extend `tests/test_dashboard_server.py::test_positions_renderers_wire_activity_pill` so `stitchPositionsRows` must not contain `stitch-status-badge`/`statusBadge`; add a static `_js_function_body(app_js, "paintLiveMarks")` assertion that it no longer contains `stitch-mark-val`, `stitch-leg-pnl`, `stitch-pnl-total`; keep `positionsRows` classic assertions.
- Verification: focused RED → GREEN on `tests/test_dashboard_server.py`; then open the dashboard and confirm a live mark tick updates the two price lines and the one P&L pair without a full reload flash.

### T3 — Lock the classic surface and focused regression — Size: S — `[Code]`
- Depends on: T1, T2
- Owns: `tests/test_orders_trades_table.py` (stitch-only assertions, only if they read a removed column), `tests/js/orders_trades_harness.cjs` (no change expected)
- Build: no classic edits; if a stitch `surface="stitch"` assertion reads a removed column, update that assertion only; do not touch classic assertions or the harness; run the focused trio once after T1/T2 are green.
- Verification: `python -m pytest -q tests/test_stitch_positions_marks.py tests/test_orders_trades_table.py tests/test_dashboard_server.py`; any failure naming `data/` or `runtime/` teardown files is re-read against the live-run note before being treated as a regression.

Checkpoints: after T1 (six-column static render works), after T2 (live repaint matches), after T3 (focused trio green and report written).

## Sub-issue mapping

Skipped deliberately: #925 is one cohesive ready-for-agent change, not a wayfinder map; splitting it into child tickets would add tracker overhead without changing the build or the review surface.

## How to verify (operator, hands-on)

1. Run `python -m dashboard.server`, open `http://127.0.0.1:8799`, go to Orders/Trades → OPEN POSITIONS.
2. The header shows six columns in the order MARKET & CLASSIFICATION, CATEGORY, HELD INVENTORY, CURRENT PRICE, UNREALISED P&L, LAST UPDATED; failure looks like nine columns or any of TOTAL COST / MARK VALUE / HEDGE STATUS.
3. On a single-fill row, CURRENT PRICE shows both YES and NO; the missing side shows its own price or `--`; a resting hedge reads `5 resting @ $0.46` with no hedge-working wording.
4. UNREALISED P&L shows one coloured dollar value and one percentage; failure looks like per-leg lines, a `Total` line, or a stale number when live prices tick.
5. Click CURRENT PRICE, UNREALISED P&L and LAST UPDATED headers; rows reorder; no removed column is clickable; failure looks like a header doing nothing or a removed column reappearing.

No live orders are placed and no `data/` or `runtime/` file is rewritten for this verification.

## Risks / notes

- Shared painter (`paintLiveMarks`) also serves the classic surface — keep the classic branches untouched.
- `stitchHeldLegMarks` disappears from exports; any test calling it moves to the new both-legs helper.
- The row tint stays as the only signal that a lone held leg has its hedge resting.
- If a live tournament run is active, pytest teardown errors naming `data/`/`runtime/` files are environmental; re-read before acting.
