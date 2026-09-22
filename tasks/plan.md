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
- `README.md` (title + folder tree), `DESIGN.md`, `AGENTS.md`, `CLAUDE.md` headers
- `metadata.json` name, `docs/design/preview.html` title + h1
- `scripts/spread-hunter-menu.ps1` — banner, exit text, comments, folder-path comment
- `scripts/spread-hunter-menu.ps1:430,583` — pid-file `strategy` value
  (write-only: no reader in repo, verified by grep) → `"spread-hunter"`
- `package.json` name → `"spread-hunter"` (not published; local dev only)
- `.gbrain-source` + gbrain examples in AGENTS.md/CLAUDE.md → `spread-hunter`
  (follow with `gbrain sync --source spread-hunter`)

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
  - GREEN: scans in-scope files for banned variants
    (`Spread Hunter Live`, `SPREAD HUNTER LIVE`, `spread-hunter-live`
    except the URL allow-list) and asserts zero hits.
- Run focused file only during dev; CI is the merge gate.

## Acceptance
- [x] No banned variant in any in-scope file (test green)
- [x] GitHub URLs untouched and still resolve
- [x] Dashboard brand reads SPREAD HUNTER; explainer footer matches
- [x] `strategy` pid value reads `spread-hunter`; nothing reads it back

## How to verify (hands-on, operator)
1. Open `http://127.0.0.1:8799` — brand shows SPREAD HUNTER.
2. Open `dashboard/static/strategy_explainer.html` — title + footer without Live.
3. If the stack is running: open the pid file under `runtime/` — strategy is `spread-hunter`.
