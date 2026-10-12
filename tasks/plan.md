# Plan — #478 [IMPROVE] Toast notifications for operator actions

Branch: i478/toast-notifications-for-operator-actions | Issue: #478
Size: Standard | Type: Code + Design/UI (Frontend Component & Harness TDD)
Stack: JavaScript / HTML / CSS / Python (Node harness + pytest wrapper)
Labels: ready-for-agent

## Overview
Add a toast notification system so every operator action (sync, start/stop, reset, errors) gets visible feedback instead of silence.
Toasts auto-dismiss after a configurable timeout, stack vertically in the bottom-right corner, feature a 44px accessible dismiss button, respect `prefers-reduced-motion`, and map to the existing semantic CSS variables (`ok` -> green, `warn` -> amber, `error` -> red, `neutral` -> surface raised).

## Locked Constraints (CONSTRAINTS.md)
- Target files for modification:
  - `dashboard/static/index.html` (mount `#toast-container`)
  - `dashboard/static/styles.css` (toast container and toast styles using existing tokens)
  - `dashboard/static/app.js` (implement `showToast`, export `showToast`, wire into Sync, Master Toggle, Reset)
  - `tests/js/toast_harness.cjs` (NEW - Node stub harness)
  - `tests/test_dashboard_toasts.py` (NEW - pytest wrapper)
- Files that must NOT be modified:
  - `dashboard/server.py`, `server.ts`, `data/orders.db`, `core_brain/*`, `scripts/*`.
- Dependencies: Zero new dependencies (vanilla JS/CSS).
- Accessibility: `role="alert"` for error toasts, `role="status"` for ok/warn/neutral. Focus must never be trapped or moved by a toast.
- Sanitization: Toast text must use `textContent`, never `innerHTML`.

## Dependency Graph
- Task 1 (Core Toast Component & Action Wiring in dashboard) — unblocks Task 2.
- Task 2 (Node Stub Harness, Pytest Test Suite & Verification) — depends on: Task 1.

## Tasks

### [x] Task 1 — Implement Toast Container, Styling & Button Wiring (`dashboard/static/index.html`, `styles.css`, `app.js`) [Size: M]
- Domain: Design/UI + Frontend Engineering.
- In `dashboard/static/index.html`:
  - Add `<div id="toast-container" class="toast-container" aria-live="polite" aria-atomic="true"></div>` before `#reset-modal`.
- In `dashboard/static/styles.css`:
  - Add `.toast-container`: fixed at bottom 24px, right 24px, max-width calc(100vw - 32px), z-index 1100, pointer-events: none, flex column, gap 8px.
  - Add `.toast`: pointer-events: auto, display flex, align-items center, justify-content space-between, gap 12px, padding 10px 14px, border-radius 6px, font-family JetBrains Mono / IBM Plex Sans, font-size 12px, border 1px solid, background surface raised tint, opacity & transform transitions (disabled under `@media (prefers-reduced-motion: reduce)`).
  - Tones:
    - `.toast-ok`: border `var(--green-border)`, bg `var(--green-bg)`, text `var(--text-primary)`, accent `var(--green-ok)`.
    - `.toast-warn`: border `var(--amber-border)`, bg `var(--amber-bg)`, text `var(--text-primary)`, accent `var(--amber-warn)`.
    - `.toast-error`: border `var(--red-border)`, bg `var(--red-bg)`, text `var(--text-primary)`, accent `var(--red-alert)`.
    - `.toast-neutral`: border `var(--border-strong)`, bg `var(--bg-surface-raised)`, text `var(--text-primary)`.
  - Add `.toast-close`: min 44px x 44px tap target, border none, background transparent, cursor pointer, color `var(--text-secondary)`, hover color `var(--text-primary)`, focus outline.
- In `dashboard/static/app.js`:
  - Implement `showToast(message, opts = {})`:
    - `opts`: `{ tone = 'neutral', timeout = 4000 }` (tone: 'ok' | 'warn' | 'error' | 'neutral').
    - Container capped at 4 toasts max (remove oldest when 5th arrives).
    - `div.toast`: role='alert' if tone === 'error' else role='status'.
    - Message in `span.toast-text` via `textContent` (safe against HTML/XSS).
    - Close button with `aria-label="Dismiss notification"`.
    - Timeout: `setTimeout` calls `dismiss()`, returns handle `{ dismiss() }`. `timeout: 0` disables auto-dismiss.
    - Export `showToast` in `module.exports`.
  - Wire SYNC (`syncBtn` click):
    - When clicked, show neutral toast "Venue sync started" and keep handle.
    - In `finally`: dismiss started toast, then show result toast:
      - `ok` when `syncOk` and `!isWarning`: "Dashboard synced with Polymarket (read-only)."
      - `warn` when `isWarning`: with message / summary text.
      - `error` when failed / caught: "Sync failed — venue may be unreachable. Retrying on next poll."
  - Wire MASTER TOGGLE (`masterToggleBtn` click):
    - Before `controlFetch()`: show neutral toast "Start request sent" or "Stop request sent. Waiting for services to shut down."
    - On result:
      - `error` for `!res.ok`, no JSON, or `data.ok !== true`.
      - `warn` for success with notes (e.g. `forced`).
      - `ok` for clean success (START says running if `data.status?.bot_state === 'RUNNING'` else accepted; STOP says stopped).
      - In `catch`: show `error` toast with existing error message.
  - Wire RESET modal & Escape:
    - Track `resetInFlight` flag.
    - On `data.ok`: show `ok` toast: "Reset finished. Check the reset steps for warnings."
    - On failure/catch: show `error` toast with "Failed: ..." or "Error: ...".
    - User dismissal function called from Close click and Escape key:
      - If modal was open:
        - if `!resetInFlight`: neutral "Reset cancelled. Nothing was changed."
        - if `resetInFlight`: neutral "Dialog closed. The reset is still running."
        - No cancel toast on 2s success auto-close.
- Verify: Syntax and export check in node.

### [ ] Task 2 — Test Harness, Pytest Suite & Regression Verification [Size: M]
- Domain: Testing/TDD.
- Create `tests/js/toast_harness.cjs`:
  - Extend DOM stub for `#toast-container`, child appending/removal, fake timers, `focus()` tracking.
  - Simulate clicks on Sync, Master Toggle, Reset Modal buttons & Escape key.
  - Execute scenarios and print JSON verdicts.
- Create `tests/test_dashboard_toasts.py`:
  - Test roles & tones: `ok`, `warn`, `neutral` get `role="status"`; `error` gets `role="alert"`.
  - Test toast stack & cap: maximum 4 visible toasts, oldest removed on 5th.
  - Test close button: dismisses toast, idempotent dismiss.
  - Test auto dismiss: timer triggers dismissal; `timeout: 0` does not auto-dismiss.
  - Test focus preservation: focus is never moved to toast.
  - Test text sanitization: HTML tags rendered as textContent, no innerHTML.
  - Test SYNC action toasts: started -> success/warn/error.
  - Test Master Toggle toasts: sent -> running/stopped/error.
  - Test Reset modal toasts: confirm ok/error, cancel via close / Escape.
- Run tests:
  - `python -m pytest -q tests/test_dashboard_toasts.py`
  - `python -m pytest tests/ -k "live_state or input_lag or analytics_surface or start_toggle or dashboard" -q`
  - `npx tsc --noEmit server.ts`
