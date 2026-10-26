# Plan — Issue #837: Populate the cockpit's "Worst Catalyst & Hypothesized Tweak" column

Branch: `i837/populate-worst-catalyst-tweak-columns` | Issue: #837
Tier: **Standard** (3 shipped files + 1 test harness, one architectural decision: where
annotation text lives) | Types: **Code (Backend/Logic + Design/UI) + UX/Copy**

Base: branched from clean `origin/main` @ `3daa04a0`. No trading settings change. Money at
risk: **$0** — the cockpit opens the tournament DBs read-only and places no orders.

---

## 1. What is actually broken (verified, not assumed)

| # | Finding | Evidence (verbatim, `origin/main`) |
|---|---|---|
| F1 | Plan arms hardcode an empty catalyst, so every #799 row shows `N/A`. | `scripts/tournament_cockpit_server.py:900-901` — `"worst_catalyst": "",` / `"worst_catalyst_desc": "",` |
| F2 | Only `hypothesized_tweak` and `tweak_rationale` are generated, and only from env/thesis. | `:902-903` — `"hypothesized_tweak": _tweak_text(entry.get("env") or record_arm.get("env")),` / `"tweak_rationale": entry.get("thesis") or "",` |
| F3 | The arms config has **no** note stubs. The issue's "with `worst_catalyst`, `hypothesized_tweak`, `tweak_rationale` stubs (lines 1–117)" is false. | `python -c "…json.load('configs/tournaments/i799-tournament-test_arms.json')…"` → keys are exactly `['color','desc','env','label','name','profile','thesis']` |
| F4 | The cell interpolates raw config text into `tr.innerHTML` with no escaping. | `:2115-2120` — `🚨 ${{arm.worst_catalyst \|\| 'N/A'}}` … `💡 Tweak: ${{arm.hypothesized_tweak \|\| 'N/A'}}` |
| F5 | The starting state is dumped into an inline `<script>` unescaped. | `:1423` — `json_embedded = json.dumps(initial_data)`; used at `:2043` (`let state = {json_embedded};`) |
| F6 | `build_plan_arms` looks the arms-config entry up by exact record name, while the merge path strips a `-N` round suffix. | `:890` — `entry = source.get(name) or {}` vs `:1023-1026` — `plan_arms[_strip_round_suffix(a["name"])] = a` |
| F7 | `dashboard/static/index.html` does **not** render these fields; the issue's "Relevant files" entry is wrong. The second renderer is `scripts/tournament_hub.py:164-167,366-367`. | `grep -rn worst_catalyst dashboard/static/` → no matches |
| F8 | `reports/tournament_cockpit.html` is gitignored and written at server start, so it can never appear in `git diff --stat`. | `.gitignore:74` = `/reports/`; server `:2548-2569` writes it |
| F9 | The newest-run page description would become false once notes render. | `:963` — `"length; catalyst columns stay empty until this run reports."` (no test pins this string) |

`analyze_arm_database` returns `{**cfg, ...}` on every branch (`:1056, :1122, :1136,
:1259`), so the four fields already reach `/api/stats` untouched — no stats-layer work.

## 2. Resolved questions (from code, not asked back)

| Question | Answer | Where |
|---|---|---|
| Which value wins, config note or generated text? | Config note wins; generated text is the fallback. Keeps older tournaments identical. | F1/F2 |
| Can notes ride inside `env`? | No — the launcher rejects any non-`BOT_*`/`FILTER_*` env key with `Invalid environment override`. | `scripts/shadow_tournament.py:369-374` |
| Can notes live in the plan record instead of the arms config? | No producer: `ArmPlan.to_dict()` emits only `index,name,db_path,run_id,dash_port,env,shadow_argv,dash_argv`. | `scripts/shadow_tournament.py:80-89` |
| Are `_CENTS` settings uniformly dollar fractions? | No. `BOT_ORDER_REQUOTE_DEAD_BAND_CENTS` is bounded `0.0..1.0` (dollars) and `BOT_RISK_STOP_ADVERSE_DROP_CENTS` `0.01..0.90` (dollars), but `BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS` is integer cents `1..20`. | `core_brain/config.py:393-397, 529-532`; `core_brain/single_leg_lifecycle.py:29-30` |
| What are the shipped defaults the notes cite? | Deadband 3¢ (`0.03`), single-leg stop **5¢** (`0.05`) — the issue's "shipped 4.5¢ drop threshold" is wrong. | `core_brain/config.py:208, 243` |
| Do the presets named in the notes exist? | `conservative` 0.75x/2–5¢, `balanced` 0.50x/2–4¢, `aggressive` 0.25x/2–3¢, `prudent` 0.60x/2–4¢. | `core_brain/config.py:556-589` |
| Does any test pin the F9 copy? | No. | `grep -rn "catalyst columns stay empty" tests/` → 0 |
| Can the analyst metrics be re-derived here? | No `data/tournaments/` record exists in this worktree; the 6.4 h figures stay attributed to the analyst's snapshot. | `ls data/tournaments` → empty |

## 3. Interface contract (locked before build)

Arms-config entry (top level, next to `name`/`env`/`thesis`) — all four optional `str`:

```jsonc
{
  "name": "hair-trigger-2c", "label": "…", "color": "…", "profile": "…",
  "desc": "…", "thesis": "…", "env": { "BOT_RISK_STOP_MODE": "ABSOLUTE_DROP" },
  "worst_catalyst":      "Stop-out rate 44.1% (15/34)",   // short headline
  "worst_catalyst_desc": "6.4 h snapshot: …measured facts…",
  "hypothesized_tweak":  "BOT_RISK_STOP_ADVERSE_DROP_CENTS=0.045",
  "tweak_rationale":     "Hypothesis: …one or two sentences…"
}
```

Cockpit state arm (produced by `build_plan_arms`, consumed by the inline script):

```python
{"name": str, "label": str, "port": int, "db": str, "run_id": str, "color": str,
 "desc": str,
 "worst_catalyst": str,        # "" when no note and no fallback
 "worst_catalyst_desc": str,   # "" when no note
 "hypothesized_tweak": str,    # note, else "<key> = <value> · …" from env, else ""
 "tweak_rationale": str}       # note, else thesis, else ""
```

New JS contract inside the generated page:
`annotationCellHtml(arm) -> string` — pure; returns the whole `<td>` inner markup;
`escHtml(value) -> string` — `& < > " '`; both used by `renderAll()` on first render and
every poll.

## 4. CodeRabbit plan intake (read once, not re-read by Stations III–V)

- **Adopted:** `build_plan_arms` as the single seam; one private trim helper beside
  `_tweak_text`; config-note-first precedence with today's values as fallback; extracting
  the cell into `annotationCellHtml`; escaping cell values; `\u003c` for the embedded
  state; a Node harness modeled on `tests/js/analytics_surface_harness.cjs` + the
  `shutil.which("node")` skip pattern from `tests/test_analytics_surface_mount.py`; flat
  note keys in the arms file; updating `test_newest_run_of_another_issue_owns_the_arm_table`;
  the launcher-equivalence assertion via `build_tournament_plan`.
- **Rejected — record-arm note fallback (its Task 1.1):** it says to read each note from
  the arms config entry "first, then the `plan.json` record arm. This is the same order the
  code already uses for `env`." `env` is in `ArmPlan.to_dict()`, notes are not
  (`scripts/shadow_tournament.py:80-89`), so that branch is unreachable dead code. Notes
  are read from the arms config only.
- **Rejected — "Apply corrections C5–C11":** no C1–C11 list exists anywhere in the comment.
  Dangling reference; the corrections in §1/§2 were derived independently from the code.
- **Rejected — 9 synthetic cases as 9 separate circuits:** they collapse into one test over
  one temporary plan + one temporary arms file.
- **`[UNVERIFIED]` → resolved in planning:** every line it cites was spot-checked on
  `3daa04a0` (`build_plan_arms` 879, merge 1023-1041, cell 2114-2121, `json.dumps` 1423,
  `_plan_arm_source` 834, `_strip_round_suffix` 712). Its "lines 1423–1437" for the state
  dump is right.

## 5. Dependency graph

```
T1 (server sources notes)  ──▶  T3 (10 notes + real-config tests + copy)
T2 (safe render + Node harness)   (independent of T1 and T3)
```

Order is risk-first: **T2 first** is the only engineering unknown (driving a
Python-generated inline script from Node). It is also independent, so a dead end found
there costs nothing already written. T1 is small and mechanical. T3's content depends on
T1's precedence rule and cannot start before it.

---

## T1 — Source the four notes from the arms config (size **S**, `[Backend/Logic]`)

Files: `scripts/tournament_cockpit_server.py`, `tests/test_tournament_cockpit_arms.py`.
Depends on: —

- Add `_note_text(value: Any) -> str` next to `_tweak_text` (`:869`): `str` → `strip()`;
  `None`/non-`str`/blank → `""`.
- In `build_plan_arms` (`:879`): resolve the entry once, suffix-aware —
  `entry = source.get(name) or source.get(_strip_round_suffix(name)) or {}` (closes F6 and
  fixes `label`/`desc`/`color` for numbered runs in the same line).
- Fields:
  - `worst_catalyst`: `_note_text(entry.get("worst_catalyst"))`
  - `worst_catalyst_desc`: `_note_text(entry.get("worst_catalyst_desc"))`
  - `hypothesized_tweak`: `_note_text(entry.get("hypothesized_tweak")) or _tweak_text(entry.get("env") or record_arm.get("env"))`
  - `tweak_rationale`: `_note_text(entry.get("tweak_rationale")) or _note_text(entry.get("thesis"))`
- Do not touch the static arm dicts, the record merge at `:1023-1041`, or metric math.
- Test (one function, one temp plan + temp arms file): `a-full` (H/D/T/R + env
  `{"BOT_X": "1"}` + thesis `Th`) → exactly H/D/T/R; `b-blank` (`""`×4) → `""`,`""`,
  `"x = 1"`, `"Th"`; `c-null` (`null`×4) → as b; `d-space` (`"   "`×4) → as b; `e-padded`
  (`"  H  "`) → `"H"`; `f-nonstring` (`5`) → `""`; `g-thesis-only` (no notes) →
  `""`,`""`,`"x = 1"`,`"Th"`; record name `a-full-1` against source `a-full` → H/D/T/R.
- Verify: `python -m pytest -q tests/test_tournament_cockpit_arms.py`.

## [x] T2 — Render the cell safely, proven by a Node harness (size **M**, `[Design/UI]`)

Files: `scripts/tournament_cockpit_server.py`, `tests/js/cockpit_annotation_harness.cjs`
(new), `tests/test_tournament_cockpit_arms.py`.
Depends on: —

- Add `escHtml(v)` (`&`, `<`, `>`, `"`, `'`) and `annotationCellHtml(arm)` to the inline
  script; move the markup out of the `renderAll()` row (`:2113-2121`) into it and have
  `renderAll()` call it. Trim every value first.
  - catalyst: escaped text, else `N/A`.
  - description: its own `<div>` only when text exists.
  - tweak: `💡 Tweak: <text>`, else `N/A`.
  - rationale: its own `<div>` only when text exists.
- Hardening (closes F5): `json_embedded = json.dumps(initial_data).replace("<", "\\u003c")`
  at `:1423`. `/api/stats` is plain JSON — unchanged.
- New harness `tests/js/cockpit_annotation_harness.cjs`: argv[2] = JSON
  `{"html": <generated page string>, "arms": [...]}`; extract the inline `<script>` blocks
  that have no `src`; append an epilogue exposing
  `{annotationCellHtml, renderAll, setArms}`; run under `vm` with stubs for
  `document.getElementById` (stub elements that remember `innerHTML`),
  `document.createElement` (rows push their `innerHTML` into a captured list),
  `document.querySelectorAll`, `Chart` (no-op class with `destroy`), `fetch` (no-op
  promise), `setInterval` (no-op), `Date`. Print `{"cells": [...], "rows": [...],
  "errors": [...]}` as JSON. Model the stub style on
  `tests/js/analytics_surface_harness.cjs`.
- Python side: call it like `tests/test_analytics_surface_mount.py` (`shutil.which("node")`,
  `check=True`, `pytestmark` skip when Node is absent) and assert: filled H/D/T/R appear in
  order with no `N/A`; each of all-empty / all-null / keys-missing / all-whitespace gives
  exactly two `N/A` and no description or rationale element; empty catalyst + tweak `T`
  gives one `N/A` and shows `T`; `<img src=x onerror=1>` → `&lt;img` present, `<img`
  absent; `renderAll()` with one filled arm puts H and R in the row; `arms: []` raises no
  error; `errors` empty in every case.
- Python-only check for F5: a state arm whose `worst_catalyst` is `</script><b>x</b>`
  produces HTML with no `</script><b>`.
- Verify: `python -m pytest -q tests/test_tournament_cockpit_arms.py`.

## T3 — Write the 10 notes, pin the real config, fix the stale copy (size **M**, `[UX/Copy]`)

Files: `configs/tournaments/i799-tournament-test_arms.json`,
`tests/test_tournament_cockpit_arms.py`, `scripts/tournament_cockpit_server.py` (one
string).
Depends on: T1.

- Add the four keys to all 10 entries, top level (never inside `env`). Pattern per arm:
  headline → "6.4 h snapshot:" measured facts → single `BOT_NAME=value` with the real unit
  → `Hypothesis:` sentence. Leave `name`/`label`/`env`/`thesis`/`profile`/`desc` untouched.
- Facts to preserve (from the issue), with the corrections this plan verified:
  | Arm | Catalyst headline (facts) | Tweak |
  |---|---|---|
  | loose-baseline | 11 stops / 40 trades (27.5%); shipped stop is **5.0¢** (`0.05`), not 4.5¢ | `BOT_ORDER_REQUOTE_DEAD_BAND_CENTS=0.06` (default 3¢) |
  | tight-touch | 14 fills / 30 quotes (46.7%), 6 completed trades | `BOT_QUOTE_TOURNAMENT_PRESET=balanced` (0.50x, 2–4¢) |
  | balanced-mid | 8 stops / 33 (24.2%), two early stops at min 194–205 | `BOT_ORDER_REQUOTE_DEAD_BAND_CENTS=0.06` |
  | wide-shield | 9 stops / 34 (26.5%), avg loss -$0.37 vs -$0.19 | `BOT_RISK_STOP_ADVERSE_DROP_CENTS=0.050` (from 0.080) |
  | prudent-hybrid | gap trade at min 193.8 ($1.19 → $0.49); 7 stops, max -$0.70 | `BOT_ORDER_REQUOTE_DEAD_BAND_CENTS=0.06` |
  | static-floor-2c | -$2.28 single close at min 304.8 (largest in the run) | `BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON=1` |
  | low-churn-deadband | 10 stops / 37 (27.0%), back-to-back -$0.09/-$0.31 at min 280 | `BOT_ORDER_REQUOTE_DEAD_BAND_CENTS=0.06` (from 0.10) |
  | hair-trigger-2c | 15 stops / 34 (44.1%), 19 merges | `BOT_RISK_STOP_ADVERSE_DROP_CENTS=0.045` (from 0.020) |
  | patient-stop-9c | 6 completed trades, -$2.10 at min 360.6 | `BOT_RISK_STOP_ADVERSE_DROP_CENTS=0.060` (from 0.090) |
  | queue-clear-5m | max DD -$1.00, -$0.80 at min 218.9 | `BOT_ORDER_QUEUE_MAX_CLEAR_MIN=3` (from 5) |
- Update `test_newest_run_of_another_issue_owns_the_arm_table` (`:509-517`): the loose arm
  now carries its config note for tweak and rationale, so replace
  `assert loose["hypothesized_tweak"] == ""` and
  `assert loose["tweak_rationale"] == spec["loose-baseline"]["thesis"]` with the config
  values; keep the `db`/`port`/`label`/`desc` assertions as they are.
- New real-config test: exactly these 10 names in this order; only
  `name,label,color,profile,desc,thesis,env` + the four note keys on every entry; each note
  non-empty after trim; each tweak starts `BOT_`; each rationale starts `Hypothesis:`;
  `build_plan_arms` for a real #799 record fills all four fields on all 10 arms; and
  `build_tournament_plan(arms=<file>)` produces identical serialized arms with the four
  keys removed (proves notes never change trading settings).
- Fix F9: `:963` → the columns now come from the run's arms file, e.g. `"…Comparative
  reading happens after the planned run length; catalyst notes come from this run's arms
  file."`
- Verify: `python -m pytest -q tests/test_tournament_cockpit_arms.py
  tests/test_tournament_cockpit_funnel.py tests/test_shadow_tournament.py
  tests/test_tournament_run_number_naming.py`.

## Checkpoints

- **After T2:** `annotationCellHtml` + `escHtml` green in the Node harness — the cell shows
  escaped text and exactly two `N/A` on an empty arm, and the embedded state cannot break
  the script tag. (Progress line, not a stop.)
- **After T3:** the #799 arms file carries 10×4 notes, the launcher-equivalence test is
  green, and the focused suite is green.
- **Station IV:** browser gate on the regenerated cockpit + the operator block below.

## 6. Improvement proposal (Step 5)

**Evidence (verbatim):** `scripts/tournament_cockpit_server.py:2115` —
``🚨 ${{arm.worst_catalyst || 'N/A'}}`` — and `:1423` — `json_embedded = json.dumps(initial_data)`,
embedded at `:2043` as `let state = {json_embedded};`.

**Proposal (edge-case hardening → adopted by default):** once this text comes from an
editable config file instead of Python string literals, the cell must escape its four
values and the starting `state` must be dumped with `<` → `\u003c`. One stray `</script>`
or `<b>` in an analyst note otherwise breaks the whole page — and the operator who wrote
that note has no way to see why. This is inside T2, not new behaviour the issue never
asked for.

**Rejected proposal (recorded so it does not resurface):** moving the #487/#484 static
annotation strings into config files as well. It would rewrite a finished tournament's
analyst prose for no operator benefit; `CONSTRAINTS.md` fences it off and the backlog row
below tracks it as its own ticket.

## 7. Out of scope → see `docs/backlog/i837-cockpit-catalyst-columns.md`

## 8. How to verify (operator, hands-on — no pytest, no gh, no git)

1. In the project folder where the #799 tournament ran (the run's
   `data/tournaments/i799-tournament-test_*/plan.json` must exist — the isolated agent
   worktree has no `data/`), restart only the cockpit:
   `python -m scripts.tournament_cockpit_server` and open `http://127.0.0.1:8800/`.
   The tournament itself and the Trader are not touched; no orders are placed.
2. Confirm the page header says **#799** and the leaderboard lists the ten arms.
3. Read the "Worst Catalyst & Hypothesized Tweak" column: every one of the ten rows shows a
   `🚨` headline, a description line, a `💡 Tweak:` line and a rationale line. A failure
   looks like `N/A`, or a missing description/rationale line, on any row.
4. Leave the page open ~10 seconds for one poll: the same text stays put (it must not
   blank out on refresh).
5. Open `http://127.0.0.1:8800/api/stats` and confirm each of the ten arms carries all four
   fields with text.

Side effect: the restart rewrites the `reports/tournament_cockpit.html` snapshot
(gitignored). Undo: revert the change and restart the cockpit server.
