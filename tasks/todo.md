# Issue #294 â€” Click-to-sort the Orders & Trades tables

Branch: `i294/sort-orders-trades-columns`

- [x] **T1** Sort model + comparators (`otSortSpec`, `compareOtv`, harness passthrough, exports) â€” RED tests first
- [x] **T2** All four builders sort their backing groups before pairing; no-sort output byte-identical
- [x] **T3** Sortable `<th>` controls: real button per column, `aria-sort`, one indicator, delegated listener, per-view state
- [x] **T4** CSS: hover, `:focus-visible`, `cursor: pointer` using existing DESIGN.md tokens
- [x] **Checkpoint** Focused suite green (`python -m pytest -q tests/test_orders_trades_table.py`, baseline 62 passed)
- [x] **Checkpoint** Hands-on: click a header, click again to flip, switch tabs and back, watch a 2s poll re-render
- [x] STATUS `<th>` vocabulary test still passes unmodified

