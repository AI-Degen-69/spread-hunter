# CONSTRAINTS.md — Issue #837: Cockpit catalyst & tweak column

Branch: `i837/populate-worst-catalyst-tweak-columns` | Issue: #837

Locked during Station II planning. Stations III–V hold every line below.
Tighten only with operator approval; never loosen silently.

## Zero regressions

- Focused suites for the touched module, green before and after review fixes:
  `tests/test_tournament_cockpit_arms.py`. Regression neighbours that must pass:
  `tests/test_tournament_cockpit_funnel.py tests/test_shadow_tournament.py
  tests/test_tournament_run_number_naming.py`. The full `python -m pytest -q` suite is
  the GitHub CI merge gate; it is not re-run locally during review stations.
- Pinned and unchanged: the #487 static table contract
  (`test_i487_arm_table_names_the_ten_spec_arms_with_their_hypotheses` — its
  `worst_catalyst == ""` assertions describe a forensic run that does not exist, so they
  stay `""`), the #492/#608 frozen archive resolver, `analyze_arm_database`'s metric
  outputs, and the funnel counts.
- Every new behaviour needs a test that fails without it: note sourcing precedence and
  trimming, the round-suffix arm lookup, the two-`N/A`-only rule, markup escaping, the
  embedded-state `</script>` guard, and the launcher-equivalence of an annotated arms file.

## Scope fences (hard)

- **Only these files change:** `configs/tournaments/i799-tournament-test_arms.json`,
  `scripts/tournament_cockpit_server.py`, `tests/test_tournament_cockpit_arms.py`,
  and the new `tests/js/cockpit_annotation_harness.cjs`.
- **No `core_brain/` edit.** The note keys live at the top level of an arm entry, never
  inside `env`: the launcher rejects any env key that is not `BOT_*`/`FILTER_*`
  (`scripts/shadow_tournament.py:369-374`).
- **No trading setting changes.** Regenerating a plan from the annotated arms file must
  produce identical `env`, ports, run ids and db paths.
- **No metric math.** `analyze_arm_database`, the funnel, sorting, scoring and readiness
  verdicts are untouched; the four fields ride along inside the `{**cfg, ...}` spread.
- **The #487 / #484 static annotation strings stay byte-identical** — no retro-editing a
  finished tournament's analyst prose.
- **No new dependency, no new grid/column, no layout change.** One cell, same width.
- **No writes to any `.db`, no venue-reaching command** (`quote`, `complete`, Trader loop,
  dashboard START). The cockpit opens databases read-only.

## Anti-cheat

- Never skip, xfail, or delete a test to get green; never weaken an assertion.
- `N/A` keeps exactly one meaning: no data. Do not repurpose it, and do not invent a
  second placeholder ("no note", "—") to make a blank look intentional.
- Do not satisfy a substring assertion by editing the template copy instead of the
  behaviour (e.g. hardcoding an escape sequence in the CSS or a note in the HTML).
- Analyst numbers are the analyst's 6.4-hour snapshot. A claim that cannot be reproduced
  in this worktree (no `data/tournaments/` run record exists here) stays attributed
  ("6.4 h snapshot"), never silently upgraded to a measured fact.
- Units are read from the code, not from the issue: `BOT_ORDER_REQUOTE_DEAD_BAND_CENTS`
  and `BOT_RISK_STOP_ADVERSE_DROP_CENTS` are dollar fractions (`0.06` = 6¢), while
  `BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS` is integer cents.

## Performance / architecture ceilings

- One source, one code path: notes are read once in `build_plan_arms`; the cell markup is
  produced by one `annotationCellHtml(arm)` used by both the first and the 5s polled
  render. No second render pass, no client-side re-fetch, no extra `/api/stats` field set.
- No extra per-render DOM reads; the helper is pure (string in, string out).
- `/api/stats` response shape is unchanged apart from values that were already there.

## Safety (repo rules)

- `data/*.db` and `reports/` are gitignored and must never be committed;
  `reports/tournament_cockpit.html` is a runtime snapshot rewritten on server start.
- Verification for the operator is the running cockpit page, never `pytest`, `gh`, or
  `git`.
