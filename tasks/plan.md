# Plan — Issue #283: Rename Spread Hunter Live → Spread Hunter

## Scope: Small (strings only, no logic)
Direction: `Spread Hunter Live` → `Spread Hunter`,
`SPREAD HUNTER LIVE` → `SPREAD HUNTER`,
`spread-hunter-live` → `spread-hunter` (IDs only where listed).

## In scope (∼18 files)
**A. Display strings (zero risk):**
- `dashboard/static/index.html` — `<title>`, og:title, brand `SPREAD HUNTER LIVE`
- `dashboard/static/strategy_explainer.html` — h1 + footer line
- `dashboard/static/{app,prototype,styles}.css/.js` — header comments only
- `dashboard/server.py` — FastAPI title + argparse description
- `server.ts` — header comment
- `core_brain/order_manager.py:1612` — latency probe print label
- `README.md` title, `DESIGN.md`, `AGENTS.md`, `CLAUDE.md` headers
  (folder trees and checkout paths keep the real dir name — see below)
- `metadata.json` name, `docs/design/preview.html` title + h1
- `scripts/spread-hunter-menu.ps1` — banner, exit text, comments
  (the folder-path comment keeps the real checkout dir)
- `scripts/spread-hunter-menu.ps1:430,583` — pid-file `strategy` value
  (write-only: no reader in repo, verified by grep) → `"spread-hunter"`
- `package.json` name → `"spread-hunter"` (not published; local dev only)
- `.gbrain-source` + gbrain examples in AGENTS.md/CLAUDE.md → `spread-hunter`
  — **DEFERRED, not part of this change.** The gbrain index is registered as
  `spread-hunter-live` (`~/.gbrain/backup-status.json`), so the pin follows the
  GitHub/folder rename, not the product name. Flipping it now would point
  `gbrain sync`/`query` at a source id that does not exist. Those lines are
  allow-listed in the guard test with this reason recorded.

## Explicitly OUT (would break things)
- GitHub URLs (`github.com/AI-Degen-69/spread-hunter-live`) in prototype.js,
  README badge, docs/agents/git-workflow.md, and the URL assert in
  tests/test_sidebar_prototype.py — the repo itself keeps its name until the
  owner renames it in settings; renaming links first = dead links.
- History: docs/runs, old plans/specs, old showcase pages.
- The GitHub repo rename itself — owner action, second URL pass after.

## Test plan (TDD)
- New `tests/test_project_name.py`:
  - RED: fails now (finds `Spread Hunter Live` in in-scope files).
  - GREEN: scans in-scope files for any `hunter<sep>live` variant
    (case-insensitive, `space`/`-`/`_`) except the allow-list, and asserts
    zero hits plus that the known carriers were actually scanned.
- Run focused file only during dev; CI is the merge gate.

## Acceptance
- [x] No banned variant in any in-scope file (test green)
- [x] GitHub URLs untouched and still resolve
- [x] Dashboard brand reads SPREAD HUNTER; explainer footer matches
- [x] `strategy` pid value reads `spread-hunter`; nothing reads it back
- [ ] gbrain pin + gbrain examples in AGENTS.md/CLAUDE.md — deferred to the
      repo-rename pass (see In scope above); allow-listed in the guard

## How to verify (hands-on, operator)
1. Open `http://127.0.0.1:8799` — brand shows SPREAD HUNTER.
2. Open `dashboard/static/strategy_explainer.html` — title + footer without Live.
3. If the stack is running: open the pid file under `runtime/` — strategy is `spread-hunter`.
