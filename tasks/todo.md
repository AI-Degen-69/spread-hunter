# Todo — Issue #838: HYBRID Configuration Blueprint champion spec

Branch: `i838/cockpit-hybrid-blueprint-champion-spec` | Plan: [tasks/plan.md](plan.md) | Constraints: [CONSTRAINTS.md](../CONSTRAINTS.md) | Spec: [SPEC.md](../SPEC.md)

## T1 — Blueprint regression tests (RED first)
- [ ] Slice helpers: blueprint / table / commands / env, with a clear failure when the start marker is missing
- [ ] Test 1: nine champion pairs agree across table, commands and `.env`
- [ ] Test 1: seven `#799` source arm ids, `rounded up to a whole cent`, `$0.990`
- [ ] Test 1: `Performance Forecast` and `Failure Modes &amp; Stress Scenarios` headings
- [ ] Test 1: six escaped forecast values and three scenario names
- [ ] Test 1: menu + direct `shadow_run` commands present in the command slice
- [ ] Test 2: no pair-cap assignment in any form; no `-Preset hybrid`; no `scripts.shadow_tournament`; no `TOURNAMENT_PRESETS["hybrid"]`
- [ ] Test 2: `switchTab('hybrid-config'` and all seven pane ids still on the page
- [ ] Run the focused suite and confirm both new tests fail for the stated reasons

## T2 — Banner, table, commands, `.env`
- [ ] Banner: keep both benchmark labels; name Tournament #799, ten arms, ports 8801–8810; attribute 289 / 210 / 74
- [ ] Status label: `TOURNAMENT_PRESETS["prudent"]` plus explicit overrides; no single shipped preset holds the champion
- [ ] Six rows, five columns, exact values, `badge-green` / `badge-amber` as specified
- [ ] Pair-cap key cell carries no `=` and says *shipped default, leave unset*
- [ ] Command block: nine `$env:` lines, pair-cap `Remove-Item`, `-Preset prudent` menu command, direct `shadow_run`, cockpit command
- [ ] `.env` block: nine `KEY=VALUE` lines plus the plain pair-cap comment and the shadow-vs-`.env` note
- [ ] Remove `scripts.shadow_tournament` and `-Preset hybrid`
- [ ] f-string safety: doubled braces and backslashes, `&amp;`, literal `"` in `<pre>`

## T3 — Two cards
- [ ] Performance Forecast card with six escaped figures and their computation lines
- [ ] The untested-combination caveat
- [ ] `Failure Modes &amp; Stress Scenarios` card with all three scenarios and mitigations
- [ ] Trigger-not-cap, estimate-not-deadline and gap-can-be-worse caveats kept

## T4 — Verify and hand off
- [ ] Focused suite green: `python -m pytest -q tests/test_tournament_cockpit_arms.py tests/test_tournament_cockpit_funnel.py`
- [ ] Regenerate `reports/tournament_cockpit.html` by starting and stopping the cockpit once
- [ ] Read the report back from disk: `BOT_ORDER_QUEUE_MAX_CLEAR_MIN=3.0`, `Performance Forecast`, `Failure Modes &amp; Stress Scenarios`
- [ ] Backlog note written: `docs/backlog/i838-cockpit-hybrid-blueprint.md`
- [ ] Operator "How to verify" block included, five steps or fewer, no `pytest` / `git` / `gh`
- [ ] Commit on the branch and open the PR through Station IV
