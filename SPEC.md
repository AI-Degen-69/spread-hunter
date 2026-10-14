# SPEC — #564: Command palette lists sidebar links with their rail icons

Branch: `i564/command-palette-lists-sidebar-links-with-their-rai` | Tier: Standard | Type: Code + Design/UI

Supersedes the #562 spec in this file's history (kept in git).

## Goals

1. The four `RAIL_LINKS` entries (Tournament Hub, Tournament Cockpit,
   Polymarket, Repository) appear in the palette under a Links group;
   activating one opens its `href` in a new tab with `noopener` — exactly what
   a rail click does today.
2. Every palette row (pages and links) shows the same icon as its rail item,
   read from the rail item's own `<svg>` node — the palette keeps tracking the
   rail automatically, no hardcoded lists, mirroring the DOM-driven pages rule.
3. Filter, highlight, Enter, and focus behavior for link rows match page rows.

## Out of scope (from the issue — binding)

- No service-dot liveness in palette rows; dots stay in the rail.
- No launcher logic: the entry opens the URL like the rail does. A stopped
  Hub/Cockpit server behaves exactly as a rail click behaves today.
- No new commands beyond the rail; #504 owns the nav strip (untouched — rows
  render inside the palette overlay).
- No edits to `prototype.js` or `app.js`; the rail stays the single owner of
  its items.

## Resolved open questions (code, 2026-10-14)

1. **Icon source.** Rail svgs are `aria-hidden`, 16x16, `currentColor`
   (`prototype.js:239-251`), built per item by `buildIcon`. Cloning the node
   preserves all of it; nothing to redraw.
2. **The old exclusion pin flips.** `test_palette_lists_six_pages_and_two_actions`
   (`tests/test_dashboard_command_palette.py:198-208`) asserts the external
   link is excluded — that expectation is repinned to inclusion by this issue,
   not "fixed" as a regression.
3. **Harness gaps to close.** Stub elements have no `querySelector` and the stub
   `window` has no `open` spy — both are test-infra additions in this issue,
   guarded in production (`typeof` checks) so other pages never throw.
