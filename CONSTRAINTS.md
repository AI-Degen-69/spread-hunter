# CONSTRAINTS.md — Issue #838: Tournament Cockpit HYBRID Configuration Blueprint

Branch: `i838/cockpit-hybrid-blueprint-champion-spec` | Issue: #838

Locked during Station II planning. Stations III–V must hold every line below.
Tighten only with operator approval; never loosen silently.

## Zero regressions

- Focused suites for the touched module must be green after the change:
  `python -m pytest -q tests/test_tournament_cockpit_arms.py tests/test_tournament_cockpit_funnel.py`.
  The two new tests must fail on the pre-change markup and pass after it. The
  full `python -m pytest -q` regression suite is the GitHub CI merge gate
  (Ubuntu + Windows); it is not re-run locally during review.
- Pinned and unchanged, all in `scripts/tournament_cockpit_server.py`:
  the tab button `switchTab('hybrid-config', this)` (line 1788); every other
  `id="tab-…"` pane (`tab-overview` 1797, `tab-pnl-chart` 1959,
  `tab-risk-stats` 1972, `tab-execution` 1996, `tab-monte-carlo` 2009,
  `tab-recommendations` 2022); `analyze_arm_database` (1052),
  `collect_all_stats` (1305), `generate_cockpit_html` (1421),
  `run_cockpit_server` (2547) and `CockpitHandler`; the CSS block and the
  `switchTab` script.
- Pinned and unchanged in the existing tests: `tests/test_tournament_cockpit_funnel.py:153`
  (renders `tourney_key="492"`) and `tests/test_tournament_cockpit_arms.py:249`,
  `:416`, `:425`, `:440`, `:570`. Those tests are the regression proof that the
  other six panes still render; they must pass unedited.
- Every new behaviour needs a test that fails without it: the nine champion
  `KEY=VALUE` pairs agreeing across the table, the command block and the `.env`
  block; the source-arm attributions; the two new cards; the safety and tab
  isolation checks.

## Scope fences (hard)

- **Only these files change:** `scripts/tournament_cockpit_server.py` (the
  `tab-hybrid-config` pane only, lines 1857–1956) and
  `tests/test_tournament_cockpit_arms.py` (new test functions appended). Station
  II also writes `docs/backlog/i838-cockpit-hybrid-blueprint.md`.
- **Never edit:** `core_brain/**` (this issue is presentation only — no
  execution, risk, or configuration behaviour changes), `.gitignore`,
  `.env.example`, `configs/tournaments/**`, `scripts/shadow_tournament.py`,
  `scripts/spread-hunter-menu.ps1`, `tests/test_tournament_cockpit_funnel.py`,
  and every pane other than `tab-hybrid-config`.
- **No new CSS, no new JavaScript, no new Python function, helper, constant or
  data structure.** The pane reuses the existing classes: `table-box`,
  `tabular`, `positive`, `badge`, `badge-green`, `badge-amber`,
  `decision-grid`, `decision-box`. Markup only.
- **`reports/tournament_cockpit.html` is generated output.** Never hand-edit it,
  never commit it, never add or change a `.gitignore` rule to track it
  (`docs/agents/architecture.md` rule 2 forbids hiding a misplaced file behind
  an ignore rule). Regenerate it by starting the cockpit once, then stopping it.
- **No money at risk and no venue contact.** No `quote`, `complete`, `cancel`,
  Trader loop, or dashboard START. No write to any named `data/*.db`.
- `tests/conftest.py`'s autouse `block_real_state_writes` fails any test that
  writes under `data/`, `runtime/` or `reports/`. The new tests render HTML in
  memory only; they point at nothing on disk.

## Anti-cheat

- Never skip, xfail, or delete a test to get green; never weaken an assertion.
- Never edit the HTML merely to satisfy a substring assertion, and never edit an
  assertion to match whatever markup happened to be produced. The tests fix the
  expected values; the markup is written to meet them.
- **The pair cap must not be re-added to satisfy "match exactly."** Acceptance
  criterion 4 of the issue conflicts with the engine: an explicit
  `BOT_POS_PRICE_MAX_PAIR_COST_USD` raises `ValueError` outside a rehearsal
  (`core_brain/config.py:705` `resolve_pair_cost_trial`), and `core_brain/venue.py:24`
  loads the config at import time. The blueprint therefore shows `$0.990` as the
  shipped default and prints no assignment. Resolving that conflict by printing
  the crashing line is forbidden; so is resolving it by lowering the shown value
  below `$0.990`.
- All of the issue's own numbers survive: the six parameter values, the six
  forecast ranges, and the three failure scenarios. Honesty is added by
  labelling provenance, never by deleting a requested figure.

## Presentation contract (f-string safety)

The pane lives inside the f-string `generate_cockpit_html` returns, so:

- Double every literal `{` and `}` (`{{` / `}}`).
- Double every backslash in a PowerShell path: the source carries
  `.\\scripts\\spread-hunter-menu.ps1`, which renders `.\scripts\spread-hunter-menu.ps1`.
- Write `&amp;` for a literal `&` in text and in a heading (the second card's
  heading is `Failure Modes &amp; Stress Scenarios`).
- Write `&gt;` for the forecast `>` signs that the issue specifies.
- Write literal `"` inside `<pre>` blocks, never `&quot;`.
- Use `KEY=VALUE` with no spaces in the table and `.env` blocks, and
  `$env:KEY = "VALUE"` with spaces in the PowerShell block. Those two forms are
  what the tests assert.

## Performance / architecture ceilings

- One source of truth per figure: the parameter table, the PowerShell block and
  the `.env` block carry the same nine values, written once each. No duplicated
  prose paragraph restating the table.
- The page stays static markup: no extra `fetch`, no new poll, no new chart, no
  change to `collect_all_stats` cost.
- No new external dependency, no new file under `dashboard/static/`, no
  template engine.

## Safety (repo rules)

- `data/*.db` are gitignored and must never be committed.
- Operator verification is the cockpit click path and the static file on disk —
  never `pytest`, `gh`, or `git`.
