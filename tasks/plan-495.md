# Plan — #495: one table shape across the four Orders & Trades tabs

**Branch:** `i495/ui-orders-trades-tables` (from `main` @ 6763bd6)
**Worktree:** `C:/Users/Tiger/.cline/worktrees/7a6f0/i495-ui-orders-trades-tables` (isolated from `i492`)
**Scope:** the four STITCH renderers only (the ones that actually run — see "Why stitch only").

> Written to `tasks/plan-495.md` rather than `tasks/plan.md`, which still holds #488's
> completed plan. Overwriting a tracked file from another issue is a silent side change.

---

## What the issue asked vs what is actually true

The issue text says "fix header/data column mismatches" and quotes `9 vs 7` and `8 vs 6`.
**Those numbers are wrong.** Measured at runtime on `main`:

| Tab | Header `<th>` | Body `<td>` | Match |
| --- | --- | --- | --- |
| Active Markets | 10 | 10 | yes |
| Positions | 9 | 9 | yes |
| Closed Trades | 8 | 8 | yes |

No view has a mismatch, so "add/remove a cell to make counts equal" is **not** the work.
What the operator actually described — and what the evidence confirms — is a set of
*consistency* defects. Those are what this plan fixes.

## Why stitch only

`renderOrdersTrades` (`app.js:7045`) picks the renderer by
`document.body.classList.contains('proto-body')`. `prototype.js:27` mounts on
`LAYOUT_PATHS = ['/', '/prototype']`, and the dashboard root is `/`, so the stitch renderers are
what paint the screen. The classic renderers (`otHeadHtml`, `positionsRows`, `closedTradesRows`,
`marketRowPairHtml`) are **dead code on this path**. Out of scope by operator decision
(scope discipline, Rule 5).

## Evidence — the five defects

### D1. Two Timestamp shapes, four tabs

Four time formatters coexist:

| Function | Line | Output |
| --- | --- | --- |
| `fmtStopwatch` | 1198 | `1:04:07` (counting up, no date) |
| `fmtTimestamp` | 427 | `04/09/2026, 14:07` |
| `fmtRelAgo` | 485 | `5m ago` |
| `fmtAgo` | 4495 | `5m ago` (near-duplicate of `fmtRelAgo`, different unit floor) |

- Active Markets (`:6212`) and Positions (`:6382`) use the stopwatch-over-absolute pair
  (`stitch-age-stopwatch` + `stitch-age-meta`).
- Closed Trades (`:6835`) uses the same pair but names the column `AGE / CLOSED`.
- Open Orders (`:6094`) puts the column **second-to-last**, in sentence case `Age / Stamp`.

Same fact, two markup shapes, four different names.

### D2. The same concept does not sit in the same slot

| Slot | Active Markets | Positions | Closed Trades | Open Orders |
| --- | --- | --- | --- | --- |
| Time | 0 | 0 | 0 | **5** |
| Market | 1 | 1 | 1 | 1 |
| Category | 2 | 2 | 2 | merged into market cell |
| Legs | 3 / 4 | 3 / 4 | — | 3 (both legs, one cell) |
| Cost | 5 `PAIR COST` | 5 `TOTAL COST` | 3 `TOTAL COST` | 4 `Total Committed` |
| Value / P&L | 6 `EDGE` | 6 / 7 | 4 | — |
| Status | 9 | 8 | 6 | 1 (in market cell) |
| Actions | — | — | 7 | 6 |

Cost lands at 5 / 5 / 3 / 4. Time at 0 / 0 / 0 / 5. Status at 9 / 8 / 6 / 1.

### D3. Money is formatted three ways

`fmtPrice` (3dp `$0.500`), `fmtUSD` (2dp), `fmtCompactUSD` (`$1.2M`), `signedUSD`.
Active Markets shows `PAIR COST` via `fmtPrice`; Positions and Closed Trades show `TOTAL COST`
via `fmtUSD`. Same concept, two names, two formats.

### D4. P&L shows a percentage in only two tabs

Positions (`:6431`) and Closed Trades (`:6869`) render `($ and %)`. Active Markets has no P&L.
Open Orders has none **by design** (nothing is held) — correct, stays.

### D5. Header case is inconsistent

`AGE / STAMP` (caps) vs `Age / Stamp` (sentence) vs `AGE / CLOSED`.
`YES QUOTE` vs `Dual-Leg Quotes (YES / NO)` vs `YES HELD`.

### D6. A redundant Category slot — the one column that earns deletion

Operator ask: *"delete columns that don't earn their place."* The Category **column** is the
clearest candidate. Counted at runtime, Category is rendered **twice per row** in three of four
stitch bodies — once as its own `<td>` (Col 2) and again inside the market cell:

| view | own Category `<td>` | Category inside market cell | total |
| --- | --- | --- | --- |
| Active Markets | yes (Col 2) | yes (`stitch-market-meta`) | 2 |
| Positions | yes (Col 2) | yes (`stitch-market-meta`) | 2 |
| Closed Trades | yes (Col 2) | yes (`stitch-cat-avatar` chip) | 2 |
| Open Orders | **no** | yes (`stitch-meta-category`) | 1 |

Open Orders already proves the single-slot design works. Dropping the standalone Category column
from the other three (keeping the in-cell caption) removes one whole column, tightens every row,
and makes all four agree with the one tab that is already right. The word "Category" as a sort key
moves with it (see Task 4). **This is the deletion the operator asked for — and it is measured,
not a guess.**

### D7. Tags and labels speak several vocabularies

Operator ask: *"TAGS/LABELS rethink — logical, unified names."* Today at least six tag families
coexist, some tone-coloured, some not, with no shared rule:

| family | where | shape |
| --- | --- | --- |
| `otTag` (tone: good/warn/alert) | classic only — dead here | tone `<div>` |
| `marketStatusPill` (RESTING/QUOTING/IDLE) | `:5371` | `.pill` |
| `pairStatusTag` (PAIRED/PARTIAL/UNPAIRED) | via `pairSummary` | `.ot-tag` |
| `stitch-status-badge` (+`stitch-status-dot`) | positions, closed trades, open orders | badge + dot |
| `stitch-category-pill` / `stitch-cat-avatar` | every body | pill / avatar chip |
| `stitch-reason-chip`, `stitch-winner-badge`, `stitch-pill-count` | closed trades | one-off chips |

The goal is **one pill grammar**: one base class, one set of tones (a fixed map from state →
tone), so PAIRED/PAIR/FILLED/RESTING/STOP/MERGED all draw from the same three colours and the
same component. This pass defines that map and routes the live bodies through it; it does not
redesign the palette.

## Non-goals (explicit)

- Do **not** add or remove cells to chase the issue's wrong `9 vs 7` / `8 vs 6` counts.
- Do **not** touch the classic renderers, `index.html` markup, or `server.py`.
- Do **not** break a column's sort accessor — sort indices stay attached to their meaning.
- Do **not** give Open Orders a P&L column (nothing is held).
- Do **not** redesign the colour palette — D7 unifies the pill grammar, not the hues.

## The shape we are converging on

One slot grammar, applied where the tab has the data:

```
0  TIME        — stopwatch over absolute, one label everywhere
1  MARKET      — title + category caption, one label
2  LEG / PAIR  — UP and DOWN, one cell per leg (or one pair cell where the tab is pair-level)
3  SIZE        — shares held
4  COST        — money committed, one format (fmtUSD), one name
5  VALUE       — mark value / pair cost
6  P&L         — signed, $ and % together, same two-line shape
7  STATUS      — the vocabulary pill, same position
8  ACTIONS     — expand / ledger, right-aligned, only where something can expand
```

A tab that genuinely lacks a stage leaves the slot empty rather than inventing a column:
Active Markets has no SIZE/COST/P&L (nothing is held); Open Orders has no P&L.

---

## Tasks

### [ ] Task 1 — RED: a test that names the shared shape

**Size: M**

New file `tests/test_orders_trades_shape.py`, using the same `_eval_js` Node harness pattern as
`tests/test_orders_trades_table.py:31` and the `requires_node` skip guard. Build one fixture
market per stage, render all four stitch headers and bodies, and assert:

- the Time header label is **identical** in all four tabs;
- the Time cell uses the **same** element pair (`stitch-age-stopwatch` + `stitch-age-meta`) in all four;
- where two tabs both show cost, the header label and the formatter output are identical;
- the P&L cell, where present, renders `$` and `%` in the same two-line shape;
- header case follows one rule (caps everywhere — three of four tabs already do).

Every assertion must fail on today's code.

**Done when:** `python -m pytest -q tests/test_orders_trades_shape.py` is RED, and each failure
names one of D1-D5.

---

### [ ] Task 2 — GREEN: one Timestamp cell helper

**Size: M**

Add `stitchTimeCell(ts)` beside `timestampCell` (`app.js:498`) that renders the stopwatch line and
the absolute line via `fmtStopwatch` + `fmtTimestamp`. Replace the four inline copies at
`:6096`, `:6214`, `:6384`, `:6837`.

Then collapse the duplicate: `fmtAgo` (`:4495`) and `fmtRelAgo` (`:485`) do the same job with
different unit floors. Keep `fmtRelAgo`, point `fmtAgo`'s two callers (`:6818`, `:7350`) at it,
delete `fmtAgo`.

**Done when:** Task 1's Time assertions pass and `tests/test_orders_trades_table.py` stays green.

---

### [ ] Task 3 — GREEN: align the shared slots, tab by tab

**Size: L**

Re-run the suite after each tab.

1. **Active Markets** — `PAIR COST` via `fmtPrice` moves to the shared cost slot, renamed
   `TOTAL COST`, formatted with `fmtUSD`. `EDGE` moves to the P&L slot and renders as a signed
   percentage in the same two-line shape as the other two tabs.
2. **Positions** — this is the reference shape (Time 0, Market 1, Category 2, legs 3/4, cost 5,
   value 6, P&L 7, status 8). Confirm labels and formats; expect no change. It is the pattern
   the others copy.
3. **Closed Trades** — rename `AGE / CLOSED` to the shared Time label; confirm `TOTAL COST` name
   and the `($ and %)` P&L shape already match.
4. **Open Orders** — move Time from slot 5 to slot 0; rename `Age / Stamp` to the shared label;
   rename `Total Committed` to `TOTAL COST`; rename `Dual-Leg Quotes (YES / NO)` to match the
   `YES QUOTE` / `YES HELD` vocabulary.

Header case: caps everywhere.

**Done when:** all of Task 1 passes and `tests/test_orders_trades_table.py` plus
`tests/test_stitch_closed_trades.py` are green.

---

### [ ] Task 4 — Sort indices stay honest after the reorder

**Size: S**

Moving Open Orders' Time column from 5 to 0 changes `sortIdx` in its `cols` array (`:5899`) and
the body's `otSortGroups` switch (`:5989` region). Re-check `OT_TEXT_COLUMNS` (`:4775`) —
`'open-orders': new Set([1, 2])` maps Market and Leg; if either moves, that set moves with it.

**Done when:** the sort suite (`tests/test_orders_trades_table.py`, sorting section from `:1857`)
keeps its meaning, and a new case proves Open Orders still sorts by time after the move.

---

### [ ] Task 5 — GREEN: drop the redundant Category column (D6)

**Size: M**

Remove the standalone Category `<td>` (Col 2) from Active Markets, Positions, and Closed Trades
bodies and their header arrays. Keep the in-cell caption (the one Open Orders already uses).
Drop the Category sort case from each body's `otSortGroups` switch, and fix
`OT_TEXT_COLUMNS` (`:4775`) so the remaining text columns keep their new indices. Active Markets'
`case 2: return marketCategory(r.m)` (`:5427`) is removed with the column it served.

**Done when:** Task 1's header-shape test still passes, Category appears exactly once per row, and
sorting by the surviving columns is unaffected.

---

### [ ] Task 6 — GREEN: one pill grammar (D7)

**Size: L**

Define a single `stitchPill(state, label, opts)` with a fixed state→tone map, and route the live
bodies' status tags through it (`pairStatusTag`, `marketStatusPill`, `stitch-status-badge`
producers). Leave the one-off provenance chips (`stitch-reason-chip`, `stitch-winner-badge`,
`stitch-pill-count`) as-is — they are not states and are out of this grammar.

**Done when:** PAIRED / PARTIAL / UNPAIRED / RESTING / QUOTING / FINISHED render through the one
component, a test pins the state→tone map, and no body still emits a bespoke status pill.

---

### [ ] Task 7 — Commit, push, open PR

**Size: XS**

Conventional commit on `i495/ui-orders-trades-tables`. The PR body states plainly that the issue's
quoted counts (`9 vs 7`, `8 vs 6`) were measured and do not reproduce, and that this PR fixes the
consistency defects instead. Post `@coderabbitai review` after opening.

**Done when:** PR is open with the measured evidence in the body.

---

## How to verify (operator, hands-on only)

1. `.\scripts\spread-hunter-menu.ps1 start`, then open `http://127.0.0.1:8799`.
2. Click through all four tabs of Orders & Trades.
3. The Time column is **last** (before Actions) in all four, with the counting timer over
   the absolute date, and the same header word in all four.
4. Where two tabs show money committed, it reads the same label and the same number format.
5. On Positions and Closed Trades, P&L shows the dollar and the percentage together in the same
   two-line shape.

