@coderabbitai summary

Closes #586.

## Why

The top nav mixed three visual vocabularies. Operator review of the live header (merge `942a8e9`) found four inconsistencies: amber meant both execution-state and registry-mode at once (`SHADOW STOPPED` vs `DB SHADOW`); the DB run-switcher used unrelated `.db-run-row` markup; the dropdown meta slot mixed alert counts with full sentences that wrapped and squeezed names into ellipsis (`Guardra…`, `Qu…`); and the liveness dot was emitted with no separator so the ENGINE dot touched its letter.

## What changed

- **One grammar, three surfaces.** Every header pill, SERVICES dropdown row, and DB switcher row now reads `label` (service/mode name — gray, 9px, uppercase, never colored) + `value` (state only — green/gray/red/amber) + `detail` (meta only — muted, ellipsized, never a sentence).
- **Mode rides the tint, not the text.** `.mode-live`/`.mode-shadow` set the shell wash (emerald/amber); the value text color comes from `state-*` only. `mode-shadow.state-stopped` value is now stopped gray, not mode-amber — amber stops meaning two things at once.
- **Fixed meta vocabulary.** `pid · uptime`, `N alerts`, `auto-watch`, `telemetry down`, `stopped`, `rehearsal ended`. The `live only (shadow uses in-process client)` / `shadow only (live uses venue engine)` context moved from the visible meta slot to the row `title` tooltip.
- **Rows are grids.** Dropdown rows: `150px | 1fr | 140px`; switcher rows match. Panel widened 340→380px. Long meta ellipsizes and can no longer squeeze the name column or wrap.
- **Dot spacing fixed.** Dropdown row pills emit a space between the liveness dot and the label.
- **Demo row** adopts the same `DEMO` + `LOADED`/`AVAILABLE` pill grammar.

No change to state computation (`stateKey`, `aggregateServiceState`, `dbModeVerdict`), panel placement/dismissal, or which services exist.

## Test output

```
python -m pytest tests/test_ui_top_nav_consistency.py tests/test_ui_top_nav_groups.py \
  tests/test_ui_278_status_strip.py tests/test_ui_277_touch_targets.py tests/test_sidebar_prototype.py -q
89 passed in 2.13s

python -m pytest tests/ -k "live_state or dashboard" -q
340 passed, 3941 deselected in 39.77s
```

New `tests/test_ui_top_nav_consistency.py` (135 lines) pins label/value/detail roles, the meta allow-list, tooltip relocation, grid columns, ellipsis rules, dot separation, and the mode-tint refactor.

## How to verify

1. Open the dashboard (demo): `python -m dashboard.server --db data/demo_orders.db --port 8802` → http://127.0.0.1:8802
2. Header: `SHADOW STOPPED` now reads gray label `SHADOW` + gray `STOPPED` on an amber-wash shell; `DB SHADOW` label gray + mode on amber shell with filename detail.
3. Click **SERVICES** → each row is `WATCHDOG / SCAN / ENGINE / SHADOW / DECIDE` label + state value in a fixed pill column; name in the middle; meta right-aligned and ellipsized. Hover a live-only/shadow-only row → context in the tooltip, not wrapped text.
4. Click the **DB** badge → switcher rows and the demo row use the same pill grammar.
