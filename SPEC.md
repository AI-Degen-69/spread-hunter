# SPEC - #656: Surface active trading breathing green pills in Traded Positions

Branch: i656/active-trading-green-pills | Issue: #656

## Goal
A held market in ORDERS & TRADES → OPEN POSITIONS shows the same green
`RESTING` / `QUOTING` pill ACTIVE MARKETS already shows when the engine is
working that market, so the operator stops leaving the tab to find out.
Both positions renderers carry it: `stitchPositionsRows` (the served page) and
`positionsRows` (the classic layout the issue names). Full detail lives in
`tasks/plan.md` (seams, interface contract, tasks); this file is the stable
pointer so Station III never re-derives scope.

## Non-behavior contract
One new helper, `marketActivityPill(m, orders)`, whose show/not-show condition is
the ACTIVE MARKETS membership test term for term. No CSS, keyframe, server, engine
or payload change. Idle held markets emit byte-identical markup to today. No new
column, no `data-cell`, no `live` class, no IDLE pill in either positions table.
Market Scan / Kanban cards are deferred to a follow-up.

## Out of scope
`renderScreener` and the Kanban board; `styles.css` and `hb-blink`;
`dashboard/server.py`; `core_brain/**`; `data/orders.db`; Active Markets
renderers; opening commands.
