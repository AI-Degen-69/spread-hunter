# Todo — #477: Command palette for pages, actions, and market search

Branch: `spread-hunter-i477` | Issue: [#477](https://github.com/AI-Degen-69/spread-hunter/issues/477)

- [ ] T1 [L] [UX/UI] Palette shell — markup, styles, open/close, keyboard ownership
  - [ ] `#command-palette` overlay beside `#reset-modal` + script tag after `prototype.js`
  - [ ] IIFE with frozen exports; capture-phase keydown; focus trap/restore; backdrop close
  - [ ] harness (capture/bubble dispatch, focus, fetch/EventSource stubs, background spies)
  - [ ] pytest wrapper with node-skip pattern; RED tests for shell behaviors
- [ ] T2 [M] [UX/UI] Entries — pages, actions, filtering, arrows
  - [ ] dynamic page entries (exclude external links); SYNC VENUE; RESET via typed-confirm
  - [ ] filter + bounded arrows + activation + "No matches."
  - [ ] harness evaluates real `prototype.js`; RED tests for entry behaviors
- [ ] T3 [M] [UX/UI] Market search from loaded state
  - [ ] three-source dedupe; label mirror of `orderGroupMarket`; input-only computation
  - [ ] selection: home + `#ot-search-input` prefill [PROPOSED — operator opt-in]
  - [ ] RED tests for market behaviors
- [ ] Verification
  - [ ] new pytest file alone — zero skips
  - [ ] issue's `-k` selection green, unchanged
  - [ ] tsc parity vs base branch (same single pre-existing TS1343)
  - [ ] PR How-to-verify block (hands-on, zero dollar risk)
