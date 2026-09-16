# Constraints: Issue #225 — Relocate Scan State Pill to Top Bar & Format Minutes

## Quality & Tests
- Zero regressions on existing test suite: full `python -m pytest -q` must remain green.
- Targeted suites: `tests/test_dashboard_server.py` and `tests/test_live_state_language.py` must pass with 100% success.
- Anti-Cheat: Strictly forbid skipping tests (`@pytest.mark.skip`), deleting assertions, or bypassing linters.
- DOM tests must verify both HTML structure (top nav placement and `#screener-header` structure) and client logic.

## Frontend & UX Boundaries
- `#scan-state-pill` must be located inside `<div class="top-meta">` within `<header>` in `dashboard/static/index.html`.
- `#screener-header` in `dashboard/static/index.html` must remain structurally intact, clean, and not break layout on `data-markets` view.
- Heartbeat formatting in `dashboard/static/app.js`: durations `< 60s` format as `${s}s`, and `>= 60s` format as minutes (e.g. `1m`, `5m`), maintaining compact pill dimensions.
- Responsive layout: top nav header and `.top-meta` must maintain clean alignment and wrapping across standard and mobile breakpoints without overflow.
- No external JavaScript or CSS dependencies.
