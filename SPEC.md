# SPEC — #562: Command palette hotkey loses to the browser, no mouse entry point

Branch: `i562/command-palette-hotkey-loses-to-the-browser-and-pa` | Tier: Standard | Type: Debug + Design/UI

Supersedes the #477 spec in this file's history (kept in git).

## Goals

1. Ctrl+K (and Cmd+K, `/` outside a text field) opens the palette in a live
   Chrome session at the dashboard URL — the page handler wins over the
   browser default.
2. A magnifier trigger button in the top nav opens the palette with the mouse
   and puts focus in search; closing returns focus to the button (the existing
   opener-restore path).
3. The palette hint line names the working hotkeys so they are discoverable.

## Out of scope (from the issue — binding)

- No Ctrl+Backslash binding: confirmed with the operator that `/` was meant;
  no such contract ever shipped, and none is added.
- #504 owns the nav reorganization — this issue adds one minimal button to the
  existing strip; #504 may relocate it.
- No new palette entries or commands; nothing about the palette once open changes.
- No edits to `app.js`, `prototype.js`, `server.py`, or any existing test file
  outside the palette suite; serving (fingerprinted assets, no-store HTML) is
  verified correct and stays untouched.

## Resolved open questions (operator + code, 2026-10-14)

1. **"Ctrl+\" was `/`.** The operator confirmed they meant the documented `/`
   alias. Both dead hotkeys (Ctrl+K and `/`) share one listener, so one cause
   explains both — no separate key work.
2. **Stale-browser-cache theory is dead.** `index()` serves HTML `no-store`
   (`server.py:4062-4082`) and `_fingerprint_static_refs` rewrites every
   `/static/` ref to a content hash (`server.py:3994-4008`) — no author bump
   needed, no stale markup possible from a current server.
3. **Prime suspect: the serving stack predates #561.** The operator-facing
   checkout (`main`) sits at `5cfd53f`, one commit before the palette merge
   (`0c82aad`). A page served from there has no palette markup, so
   `initPalette` early-returns before registering the window keydown listener
   (`command-palette.js:425-434`) — exactly the symptom: Ctrl+K falls through
   to the URL bar, `/` does nothing. T1 proves the served bytes first.
4. **Button wiring stays inside the palette file.** The browser IIFE exposes
   no globals (exports exist only under node `module.exports`, `:440-452`),
   so `initPalette` binds the trigger by id itself — no inline `onclick`, no
   `app.js` edits.
