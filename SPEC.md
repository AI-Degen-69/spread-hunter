# SPEC: Issue #259 — Equity tooltip shows trade facts

## Goal
Hovering a Portfolio equity-curve close point must show trade facts in words:
which market (human title), dollar + percent PnL, close method (MERGED badge),
and hold time from first quote to close. Never a bare 64-hex condition_id as
the primary line.

## Acceptance criteria (from issue)
- [ ] Tooltip first line is the human market title (falls back to slug, then
  short `0x…` only when unresolvable) — never a bare 64-hex address as primary
- [ ] Tooltip shows dollar PnL AND percent PnL (`realized_pnl / cost_basis`),
  with `--` / unmeasured when cost_basis is missing or non-positive — never a
  fabricated 0%
- [ ] Tooltip shows the close method (`MERGED` for merge closes, otherwise the
  method label) as a distinct row/badge
- [ ] Tooltip shows hold duration from first quote (`quotes.ts` for that
  condition_id) to close (`closes.ts`), human format (e.g. `3h 12m`); `--`
  when no quote exists
- [ ] Harness test pins all four rows on a fixture close (fails without change)
- [ ] `tests/test_portfolio_card_basis.py` passes; full suite green in GitHub CI

## Scope
### In scope
- `core_brain/kpi.py` equity_series close points: add `title`, `cost_basis`,
  `method`, `hold_seconds` (reuse `_resolve_market_meta()`, in-memory quotes)
- `dashboard/static/app.js` `buildBrokerEquitySeries()` passthrough +
  tooltip rows + `methodBadge()` helper
- `tests/js/portfolio_card_harness.cjs` hover capture (`tooltip_html`)
- `tests/test_portfolio_card_basis.py` row + fallback assertions

### Out of scope (per issue)
- Trading/quoting/sizing behavior; START anchor (#252); time-axis
  geometry (#257); tooltip styling beyond content rows.

## Interface contracts
- Backend close point gains optional fields:
  `title: str | None`, `cost_basis: float | None`, `method: str | None`,
  `hold_seconds: float | None` (`None` when no quote or negative delta).
  Mark points unchanged.
- Frontend `buildBrokerEquitySeries()` copies the four fields only when
  present (not `null`/`undefined`), same conditional-copy as `pnl`/`market`.
- `methodBadge(method)` returns an HTML badge string; `merge`/`shadow_merge`
  → `MERGED`; unknown/missing → neutral fallback (never throws).
- Tooltip rows use existing `broker-tooltip-row` markup; strings via `esc()`,
  percent via `fmtPct(pnl / cost_basis)`, hold via hold formatter.

## Edge cases
- Unresolvable title → slug → short `0x…` (resolver's existing fallback).
- `cost_basis` missing / ≤ 0 / non-numeric → percent renders `--`.
- No quote for condition_id, or close ts before first quote → `hold_seconds`
  `None` → tooltip `--`.
- Fixtures without the new fields render exactly the old point shape
  (existing exact-match assertions keep passing).
