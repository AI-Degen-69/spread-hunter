# Noticed but not touching — #656

Scope guard for #656. Every row is a defect or drift this station noticed and deliberately
left unchanged. Status stays `open` and Resolved-by stays empty until a follow-up claims the
row. Owner is `you` (the operator) when no existing issue covers it, otherwise the issue
number. Dates are when the row was written.

## Discovered while planning (owner: planner, 2026-10-18)

| Noticed | Why out of scope | Owner | When | Status | Resolved-by |
|---|---|---|---|---|---|
| Root `SPEC.md` ships issue #632's spec ("Standardize parameter & env naming") on `main`, with a `Branch: main (planning)` header. Replaced by this issue's spec, but the pattern means the last merged issue's spec lingers as the repo's apparent current spec. | Artifact-hygiene drift, not this issue's behaviour. Replacing it here fixes one instance, not the pattern. | you | 2026-10-18 | open | |
| `tests/test_dashboard_server.py` — the issue cites `test_app_js_has_expandable_market_rows` at `:734-754`; it is at `:1338-1393`. Both named acceptance tests only grep `app.js`/`styles.css` for expandable-row words, so they pass with or without this feature. | Line drift plus a source-grep gate that does not pin behaviour. Rewriting them is a test-architecture decision beyond this change. | you | 2026-10-18 | open | |
| `dashboard/static/app.js:7539` — `openOrdersRows` renders a third `td.ot-market` with the same shape as the two positions markets, and carries no activity pill. | The issue's acceptance criteria cover positions only; adding the Open Orders pill changes a view nobody asked to change. | you | 2026-10-18 | open | |
| `marketStatusPill` derives QUOTING from `quotes_count`, a whole-run quote counter: a market the engine stopped quoting can still read QUOTING while its run is selected. The new positions pill inherits the same reading. | ACTIVE MARKETS behaves identically today; changing the semantics of a shared status word is a strategy/dashboard decision, not this ticket's markup wiring. | you | 2026-10-18 | open | |
| `dashboard/static/prototype.css:962-1006,1313` gives `.stitch-market-cell` / `.stitch-market-meta` / `.stitch-category-pill` their own styling, so the breathing pill inherits the generic `.pill` look inside a stitch cell rather than a stitch-specific treatment. | Cosmetic parity, and `prototype.css` is a frozen surface for this change; the pill reuses the same class ACTIVE MARKETS already renders in the same layout. | you | 2026-10-18 | open | |
