# CONSTRAINTS.md — Issue #831: Closed-trades outcome shows wins and losses with exec-type pills

Branch: `i831/closed-trades-wins-losses-exec-pills` | Issue: #831

Locked during Station II planning. Stations III–V must hold every line below.
Tighten only with operator approval; never loosen silently.

## Zero regressions

- Focused suites for the touched module must be green after the change:
  `tests/test_stitch_closed_trades.py tests/test_orders_trades_table.py
  tests/test_close_method_taxonomy.py`. The full `python -m pytest -q` regression
  suite is the GitHub CI merge gate; it is not re-run locally during review.
- Pinned and unchanged: #579 cost-basis Total Cost and its column sort
  (`tests/test_stitch_closed_trades.py:193-234`), the `colspan="7"` span of the
  expanded row (`:267-277`), the Stop Loss / Grace Expired label checks
  (`:137-145`), the classic zero-profit exclusion
  (`tests/test_orders_trades_table.py:1299-1310`), the classic Orders badge tones
  (`:2953-2965`), and every classic-surface assertion in
  `tests/test_orders_trades_table.py` (it renders `surface="classic"` unless it
  says otherwise, so the legacy Closed Trades renderer is not touched).
- Every new behaviour needs a test that fails without it: the mixed
  2-win/4-loss count, the explicit-zero exclusion, the missing/null
  `realized_pnl` fallback, all five exec-type labels with their modifiers, the
  synthetic order fallback, and the removed summary strip with the parent row's
  totals still rendered.

## Scope fences (hard)

- **Only these files change:** `dashboard/static/app.js`,
  `dashboard/static/prototype.css`, `tests/test_stitch_closed_trades.py`.
- **Presentation only.** `m.realized_pnl`, `closedTradeCostBasis`,
  `closedTradesEntries`, `closeTsOf`, the KPI envelope and every file under
  `core_brain/` and `dashboard/` outside `static/` stay byte-identical. No
  backend rename of a settlement method.
- **Legacy renderer untouched:** `closedTradesRows`, `marketRowPairHtml`,
  `renderExpandedOrders`. Only the Stitch builders change
  (`stitchClosedTradesRows`, `renderStitchClosedLedgerRows`,
  `stitchLedgerChildRow`).
- **Open Orders and Positions are out of scope.** Shared base rules
  (`.stitch-leg-pill`, `.stitch-pnl-val`, `.stitch-arb-badge`,
  `.stitch-status-badge`) are not edited.
- **Seven columns stay seven**, in the same order; the expanded row keeps
  `colspan="7"`; the `OUTCOME BREAKDOWN` header and the reason chip / winner
  badge stay as they are.
- **No new method-name string list in `app.js`.** The taxonomy guard
  (`tests/test_close_method_taxonomy.py`) flags JS arrays of quoted strings
  holding a lowercase close method. Classification stays the existing
  comparison chain; a display-label → CSS-class map is allowed.
- **No writes to any named `.db`.** No venue-reaching command: no `quote`,
  `complete`, Trader loop, or dashboard START.

## Anti-cheat

- Never skip, xfail, or delete a test to get green; never weaken an assertion,
  and never edit the CSS or HTML just to satisfy a substring assertion.
- The classification order (`merge` → stop → grace → settlement → sale) and the
  null check on `realized_pnl` land unchanged: an explicit `realized_pnl: 0`
  stays `0` even when `proceeds - cost_basis` is non-zero, and counts as neither
  a win nor a loss.
- Green means a win and red means a loss, and nothing else. No exec-type pill
  may reuse those tones; a losing merge must never render green.
- Delete a CSS rule only after a grep proves no `dashboard/static/*.js` string
  still emits its class.

## Performance / architecture ceilings

- One money rule, one source: the parent counts and the ledger rows read the
  same helper. No second classification loop in `stitchClosedTradesRows`.
- No new external dependency, no new file under `dashboard/static/`, no new
  render pass: the parent row keeps computing from data it already has.
- Pure functions only — no globals, no timers, no extra DOM reads.

## Safety (repo rules)

- `data/*.db` are gitignored and must never be committed.
- Verification for the operator is the dashboard click path, never `pytest`,
  `gh`, or `git`.
