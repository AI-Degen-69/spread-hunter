# Todo — Issue #838: HYBRID Configuration Blueprint champion spec

Branch: `i838/cockpit-hybrid-blueprint-champion-spec` | Plan: [tasks/plan.md](plan.md) | Constraints: [CONSTRAINTS.md](../CONSTRAINTS.md) | Spec: [SPEC.md](../SPEC.md)

Derived checklist — `tasks/plan.md` is the authoritative completion record.

## T1 — Blueprint regression tests (RED first) ✅
- [x] Slice helpers: blueprint / table / commands / env, with a clear failure when the start marker is missing
- [x] Test 1: nine champion pairs agree across table, commands and `.env`
- [x] Test 1: seven `#799` source arm ids, `rounded up to a whole cent`, `$0.990`
- [x] Test 1: `Performance Forecast` and `Failure Modes &amp; Stress Scenarios` headings
- [x] Test 1: six escaped forecast values and three scenario names
- [x] Test 1: menu + direct `shadow_run` commands present in the command slice
- [x] Test 2: no pair-cap assignment in any form; no `-Preset hybrid`; no `scripts.shadow_tournament`; no `TOURNAMENT_PRESETS["hybrid"]`
- [x] Test 2: `switchTab('hybrid-config'` and all seven pane ids still on the page
- [x] Run the focused suite and confirm the new tests fail for the stated reasons (3 red, 41 green)

## T2 — Banner, table, commands, `.env` ✅
- [x] Banner: keep both benchmark labels; name Tournament #799, ten arms, ports 8801–8810; attribute 289 / 210 / 74
- [x] Status label: `TOURNAMENT_PRESETS["prudent"]` plus explicit overrides; no single shipped preset holds the champion
- [x] Six rows, five columns, exact values, `badge-green` / `badge-amber` as specified
- [x] Pair-cap key cell carries no `=` and says *shipped default, leave unset*
- [x] Command block: nine `$env:` lines, pair-cap `Remove-Item`, `-Preset prudent` menu command, direct `shadow_run`, cockpit command
- [x] `.env` block: nine `KEY=VALUE` lines plus the plain pair-cap comment and the shadow-vs-`.env` note
- [x] Remove `scripts.shadow_tournament` and `-Preset hybrid`
- [x] f-string safety: doubled braces and backslashes, `&amp;`, literal `"` in `<pre>`

## T3 — Two cards ✅
- [x] Performance Forecast card with six escaped figures and their computation lines
- [x] The untested-combination caveat
- [x] `Failure Modes &amp; Stress Scenarios` card with all three scenarios and mitigations
- [x] Trigger-not-cap, estimate-not-deadline and gap-can-be-worse caveats kept

## T4 — Verify and hand off
- [x] Focused suite green: 44 passed
- [x] Regenerate `reports/tournament_cockpit.html` by starting and stopping the cockpit once
- [x] Read the report back from disk: `BOT_ORDER_QUEUE_MAX_CLEAR_MIN=3.0`, `Performance Forecast`, `Failure Modes &amp; Stress Scenarios` all present; `BOT_POS_PRICE_MAX_PAIR_COST_USD=` and `-Preset hybrid` absent
- [x] Backlog note written: `docs/backlog/i838-cockpit-hybrid-blueprint.md` (N1–N8)
- [x] Browser check: the tab opens and renders the table, both cards and both command blocks
- [x] Operator "How to verify" walkthrough included, no `pytest` / `git` / `gh`
- [ ] Open the PR through Station IV
