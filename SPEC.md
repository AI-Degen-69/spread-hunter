# SPEC — #925 Trim the open positions table

## Goal

Make the stitch OPEN POSITIONS table answer three questions per row without duplicated columns: what is held, what both sides are worth right now, and whether the pair is up or down.

## Acceptance criteria

- [ ] OPEN POSITIONS renders exactly six columns in this order: MARKET & CLASSIFICATION, CATEGORY, HELD INVENTORY, CURRENT PRICE, UNREALISED P&L, LAST UPDATED.
- [ ] TOTAL COST, MARK VALUE and HEDGE STATUS are gone from the stitch header and rows.
- [ ] The `N sh total` subline is gone with TOTAL COST; no share total is printed anywhere on the row.
- [ ] A resting hedge leg in HELD INVENTORY reads `<size> resting @ $<price>` with no hedge-working wording.
- [ ] CURRENT PRICE always shows two lines, YES first then NO; each line shows its own current price (fresh live mid, otherwise quote mid, otherwise `--`) and its own live dot only for a fresh mark, whether or not that side is held.
- [ ] UNREALISED P&L is one signed dollar value plus one percentage in parentheses for the pair, coloured positive / negative / flat. Per-leg P&L lines and the `Total` label are gone; the number still comes from the par-based mark.
- [ ] The live repaint updates the same two price lines and the same single value/percentage pair in place, with no MARK VALUE lookup left behind and no per-leg P&L branch it can no longer find.
- [ ] Sorting still works for the remaining columns; removed columns cannot be sorted by from the header or through a leftover sort key.
- [ ] The empty state `colspan` matches the new column count.
- [ ] Classic `positionsRows`, `OT_COLUMNS`, Orders, Active Markets and Closed Trades are unchanged.

## Edge cases

- A side with no quote prints `--`; do not infer a price from the other side.
- A fresh live mark wins over the quote mid for that side only; the other side keeps its own fallback.
- Zero total cost basis prints no percentage; the dollar value still prints if the par mark is computable.
- Missing par mark prints `--`, an empty percentage and the flat tone.
- A full pair is valued at $1.00 per share regardless of whether leg mids sum above or below $1.00.
- A lone held leg with its hedge resting remains tinted `is-hedge-pending`; the tint is the remaining signal.
- Leftover sort keys for removed columns fall back to the default order and show no `aria-sort`.

## Out of scope

- The classic (non-`proto-body`) `positionsRows` table and `OT_COLUMNS`.
- Orders, Active Markets, Closed Trades, and the classic header machinery.
- `positionMarkValue` and the par-based valuation itself.
- New columns, tooltips, hover cards, drill-downs, or layout redesign beyond removing the three columns.
- `.stitch-status-*` badge CSS and the `is-hedge-pending` row tint.
