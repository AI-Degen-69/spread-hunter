# Plan — #487 First 10-arm tournament from the current setup

Branch: `i487/Plan-the-first-10-arm-tournament-from-the-current-setup` | Issue: #487
Station: II (Define & Plan) | Tier: **Standard** | Task type: **Code** (+ Docs: runbook)
Worktree: `.worktrees/i487-plan-the-first-10-arm-tournament-fr` — every later station runs here.

## Tier rationale

Four touched surfaces (`configs/tournaments/*.json`, `scripts/tournament_cockpit_server.py`,
two test modules, one `docs/runs/` runbook), one architectural decision (where the
committable arm spec lives versus where the generated plan record lands), no new
dependency, no public API change, no schema change. Standard, not Large.

## Intake notes

**CodeRabbit plan comment (2026-10-11, 74,937 chars).** Costed once, not re-read.

- *Adopted:* the skeleton — a ten-arm spec file, 720 minutes, ports 8801–8810; hypotheses
  carried as metadata the cockpit reads; the residue rule that an arm's env must differ
  from the control in exactly one setting; tests built on seeded temporary databases in
  the `tests/test_shadow_tournament.py` style.
- *Rejected — the premise.* It plans arms by reconstructing "the five issue-485 baselines"
  and tweaking each arm's worst catalyst (Snap-Stop grace, Deadband-Wide multiplier, the
  stop and grace knobs). The operator declared that premise stale on 2026-10-21, and the
  repo proves it: the knobs it tweaks (`single_buy_grace_sec`, `stop_loss_trigger_usd_cents`)
  were retired by #619/#622, and the run its forensics would come from does not exist.
- *Rejected — the file name.* It writes the spec as
  `runtime/tournaments/486_catalyst_tweaks_12hr.json` (its own numbering is off by one for
  this issue) and proposes a `.gitignore` exception to track it. `runtime/` is not in git by
  design; see Decision 1.
- *Rejected — the new analysis script.* It specifies `scripts/tournament_catalyst_report.py`.
  The cockpit already owns per-arm metrics. A second analyser is invention (Rule 4).
- *Rejected — the object-shaped arms file.* It proposes extending `--arms-file` to accept a
  `{tournament, arms}` object so the file carries its own minutes and ports. That is a CLI
  schema change for values two existing flags already carry. The bare list stays.
- *Verified, not assumed:* `ARMS_CONFIG_487` and `TOURNAMENTS["487"]` already exist in
  `scripts/tournament_cockpit_server.py` (:187, :670) holding the superseded premise;
  `build_tournament_plan` is `scripts/shadow_tournament.py:267`; `TOURNAMENT_PRESETS` is
  `core_brain/config.py:526`; `resolve_tournament_arms` is
  `scripts/tournament_cockpit_server.py:691`. All four confirmed live at those paths.
- *`[UNVERIFIED]`: none.* Every path, symbol and line the comment relied on was opened.

**Other intake.**

- **Stale plan set replaced.** Root `CONSTRAINTS.md` and `SPEC.md` both belonged to the
  merged `#749`. Cleared through the terminal and rewritten for `#487` (task T1, done).
- **Blocked label — the code half is satisfied.** The issue's own gate comment names
  #708 → #686 → #687 → #646 → #659 → #471. The five code tickets are **closed**; the four
  table rows closed 2026-10-20 (#708 12:51Z, #686 13:28Z, #687 16:01Z, #646 16:23Z). #471
  is still OPEN, and it is a *live* statistical GO-gate run — this issue's own scope
  excludes first-run-dependent analysis, so it gates the **verdict**, not the preparation.
  The `🚧 Blocked` label is therefore stale and is the operator's to remove.
- **"No tournament has run yet" — true only for the current code.** #608 ran ten arms for
  720 minutes on 2026-10-19 (before the 2026-10-20 changes) and produced results. Its four
  empty-`env` arms and retired-knob descriptions are the concrete reason a clean one-knob
  rerun is worth a 12-hour window.
- **Branch reused, not created.** The worktree and branch already exist at main's HEAD
  (`bcd0bff`). The branch keeps its title-cased slug — a repo-created name outranks the
  canonical `i<number>/<slug>` form.
- **No `quick-fix` label** on the issue, so Step 0C ran no gate and offered no lane.

## Decisions

**Decision 1 — the committable arm spec lives in `configs/tournaments/`; the named runtime
path is the plan record.** The issue names `runtime/tournaments/487_current_setup_10arm.json`
as the executable plan. `runtime/` is explicitly *not in git* ("Runtime state across the
rename", `docs/agents/architecture.md`; `/runtime/**/*.json` in `.gitignore`), so a spec
committed there needs a carve-out and still is not reviewable in a checkout. The launcher's
`--stamp current_setup_10arm` already produces exactly
`runtime/tournaments/487_current_setup_10arm.json` as the plan record at launch. Committed
spec: `configs/tournaments/i487_current_setup_10arm_arms.json` — the same split `#492` uses.

**Decision 2 — the hypothesis text stays in `ARMS_CONFIG_487`.** The cockpit's leaderboard
already renders `hypothesized_tweak`/`tweak_rationale` and its static arms config already
owns the per-arm prose for `#484`, `#492` and `#608`. Carrying a new `hypothesis` field
through `build_tournament_plan` → the plan record → the cockpit would add a field to two
dataclasses for text the cockpit can hold itself. Env lives in the spec, prose lives in the
cockpit — one owner per fact, zero schema change.

**Decision 3 — the ten arms.** Control runs today's configuration untouched. Each variation
sets exactly one canonical `BOT_*` variable; no arm name may be a `TOURNAMENT_PRESETS` key,
or the launcher would append `BOT_QUOTE_TOURNAMENT_PRESET` and turn a one-knob arm into a
six-knob one.

| # | Arm | Port | One changed setting | Current → arm | Source of the value |
| --- | --- | --- | --- | --- | --- |
| 1 | `control` | 8801 | *(none — the current setup)* | — | shipped defaults + the operator's `.env` |
| 2 | `offset-mult-025` | 8802 | `BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER` | 0.50 → 0.25 | preset `aggressive` |
| 3 | `offset-min-2c` | 8803 | `BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS` | 1 → 2 | preset `conservative` |
| 4 | `offset-max-3c` | 8804 | `BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS` | 20 → 3 | preset `aggressive` |
| 5 | `offset-static` | 8805 | `BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON` | on → off | preset `hybrid` |
| 6 | `deadband-6c` | 8806 | `BOT_ORDER_REQUOTE_DEAD_BAND_CENTS` | 0.03 → 0.06 | preset `hybrid` |
| 7 | `hold-queue-500` | 8807 | `BOT_ORDER_REQUOTE_HOLD_QUEUE_SHARES` | 0.0 → 500 | every preset |
| 8 | `hold-below-8c` | 8808 | `BOT_ORDER_REQUOTE_HOLD_BELOW_TARGET_CENTS` | 0.05 → 0.08 | every preset |
| 9 | `stop-adverse-5c` | 8809 | `BOT_RISK_STOP_MODE` | `NONE` → `ABSOLUTE_DROP` | no preset ships this knob |
| 10 | `book-window-4c` | 8810 | `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID` | 15 → 4 (cents) | no preset ships this knob |

Arms 9 and 10 carry their own honest note. Arm 9 flips the single-leg stop from off to on;
the shipped `0.05` adverse-drop threshold travels with the mode, so the arm changes one
variable and two behaviours — that is stated in its description rather than hidden. Arm 10
tightens the per-market quoting window from 15¢ to 4¢; it is the one arm that changes which
markets get quoted at all.

**Decision 4 — resolve the named plan record.** `resolve_tournament_arms("487")` today only
matches `487_<YYYYMMDD>-<HHMMSS>.json`. The named stamp is added to that pattern as an
explicit alternative — narrow, not a generalisation.

## Dependency graph

```
T1 ──┬─> T2 ──> T3 ──┬─> T7
     │               └─> T8
     └─> T4 ──> T5 ──┬─> T6
                     └─> T9  (also needs T3)
```

Ordering is risk-first: the arm design (T2/T3) is the piece most expensive to be wrong
about, and it lands before the cockpit work that describes it.

## Tasks

### T1 — [x] Replace the stale planning set with #487's  `[Docs]` `XS`
Files: `CONSTRAINTS.md`, `SPEC.md` (both replaced; the `#749` originals were cleared)
Built: the per-issue constraint and specification set for #487.
Skill: `spec-driven-development`, `constraint-driven-development`
Verification: both file headers name `#487` and this branch, and no `#749` text survives.

### T2 — [x] Write the arms-spec test  `[Code]` `S`
Files: `tests/test_shadow_tournament.py`
Built: a test that loads `configs/tournaments/i487_current_setup_10arm_arms.json` and asserts:
ten arms in the order above; every name matches `^[a-z0-9-]{1,24}$` and is **not** a
`TOURNAMENT_PRESETS` key; `control` has an empty `env`; each of the other nine carries
exactly one `BOT_*` key and its `env` differs from the control in exactly that key; the seven
preset-sourced values equal the live `TOURNAMENT_PRESETS` entries (read from the table, not
re-typed); and `build_tournament_plan(issue=487, arms=..., base_dir=tmp_path, check_ports=False)`
yields ports `8801`–`8810`, ten distinct DB paths and ten distinct run ids.
Depends on: T1 | Skill: `test-driven-development`
Verification: **lands RED** — the spec file does not exist yet, so the test fails on load.

### T3 — [x] Create the arm spec  `[Code]` `S`
Files: `configs/tournaments/i487_current_setup_10arm_arms.json`
Built: the ten-arm bare-list spec of Decision 3, shape identical to
`configs/tournaments/i492_tournament-1_arms.json`.
Depends on: T2 | Skill: `incremental-implementation`
Verification: T2 goes GREEN.

### ▶ Checkpoint 1 — the spec exists and validates
Report: the ten arm/env rows and the dry-run port/DB/run-id assignment. Nothing else.

### T4 — [ ] Rewrite the cockpit's `#487` arm table  `[Code]` `S`
Files: `scripts/tournament_cockpit_server.py` (`ARMS_CONFIG_487`, currently :187–:329)
Built: ten entries named exactly as T3's arms, port 8801–8810, a colour and a label each,
`db`/`run_id` as the post-launch placeholders in the legacy scheme, `desc` stating what the
arm is, `hypothesized_tweak` stating the one setting it changes, `tweak_rationale` stating
the hypothesis it tests, and `worst_catalyst`/`worst_catalyst_desc` **empty** — there is no
forensic run to fill them and inventing numbers is the failure this issue removes.
Depends on: T1 | Skill: `incremental-implementation`
Verification: module imports; the ten entries match T3's arm names one for one.

### T5 — [ ] Point the cockpit's `#487` round at the current-setup run  `[Code]` `S`
Files: `scripts/tournament_cockpit_server.py` (`TOURNAMENTS["487"]` at :670 and the `487`
branch of `resolve_tournament_arms` at :691)
Built: `TOURNAMENTS["487"]` title/subtitle/description/badge describing the current-setup
run instead of the superseded "ROUND 2 TWEAKED" catalyst round; and the `487` plan-record
pattern extended to accept `487_current_setup_10arm.json` alongside the legacy
`487_<stamp>.json` form.
Depends on: T4 | Skill: `incremental-implementation`
Verification: `resolve_tournament_arms("487")` returns the ten new arms; `"492"` and `"608"`
resolution is byte-identical to before.

### T6 — [ ] Test the cockpit side  `[Code]` `S`
Files: `tests/test_tournament_cockpit_arms.py` (new)
Built: three assertions — `ARMS_CONFIG_487` holds ten arms whose names match the spec and
whose `hypothesized_tweak` is non-empty; `resolve_tournament_arms("487")` reads a
`487_current_setup_10arm.json` written into a temporary tournaments directory and merges its
`db_path`/`run_id`/`dash_port` over the static config; and the `492`/`608` branches still
resolve from their own record forms.
Depends on: T5 | Skill: `test-driven-development`
Verification: lands RED against the old resolver, GREEN after T5.

### ▶ Checkpoint 2 — the cockpit serves the run
Report: start the cockpit, open `http://127.0.0.1:8800/487`, confirm ten rows with their
hypotheses and `NO_DATA` metrics. One line.

### T7 — [ ] Operator runbook  `[Docs]` `S`
Files: `docs/runs/2026-10-21-487-current-setup-tournament.md` (new; mirrors
`docs/runs/2026-10-18-492-tournament-rerun.md`)
Built: why this run exists (the #608 run predates the 2026-10-20 code), the ten-arm table
with each arm's one knob and motive, the launch command, the artifact list, what to watch on
ports 8801–8810, the ~1.1 GB storage note, and — stated plainly — that the comparison is
read from the cockpit and `reports/tournaments/487_current_setup_10arm_results.json` **after**
the run, and that no production winner may be chosen from modelled shadow results.
Depends on: T3, T4 | Skill: `documentation-and-adrs`
Verification: the command in the runbook is the command validated in T9.

### T8 — [ ] Refresh the superseded-premise comment  `[Code]` `XS`
Files: `tests/test_shadow_tournament.py` (`I487_TWEAKS`, :981–:995)
Built: the comment only. It currently reads "#487 hypothesises one changed catalyst per
arm", which the 2026-10-21 reframe makes false; the values themselves stay as the guard that
keeps `#492` arms free of `#487` tweaks.
Depends on: T3 | Skill: `incremental-implementation`
Verification: `test_i492_arms_carry_no_487_catalyst_tweak` still passes unchanged.

### T9 — [ ] Validate the plan end to end  `[Code]` `XS`
Files: none — evidence only.
Built: the dry run of the exact launch command, and the recorded output.
Depends on: T3, T4, T5 | Skill: `verification-before-completion`
Verification: `python -m scripts.shadow_tournament --issue 487 --arms-file configs/tournaments/i487_current_setup_10arm_arms.json --minutes 720 --stamp current_setup_10arm --dry-run`
prints ten arms on ports 8801–8810 with distinct stores and distinct run ids, exits 0, and
starts nothing.

## Improvement proposal

**Adopted (edge-case hardening).** Evidence, verbatim from the issue's own acceptance
criteria: *"10 arms defined out of the current parameter set: one control at current
settings + 9 one-parameter variations fetched live from #484's presets."* The presets ship
seven single-knob values that differ from the current setup. The remaining two arms cannot
be "fetched live from #484's presets" because no preset ships a different value for any
other knob — `bot_quote_book_max_spread_from_mid` (0.15) and `bot_quote_gate_widen_on`
(False) both already equal the current setup, so reusing them would produce two arms
identical to the control. Rather than pad the set with those two no-ops — which is exactly
what left `#608` with four byte-identical arms — arms 9 and 10 test the two levers no preset
covers (the single-leg stop, and the per-market quoting window) and their descriptions say
so. The test still asserts live-preset equality for the seven that have it, so the "fetched
live" intent is enforced wherever it is achievable.

**Not proposed (would be scope expansion).** Adding an arm to guard against duplicate
controls. The one-knob test makes a duplicate arm impossible in this spec, so no question is
needed and no arm is added.

## Rejected alternatives

| Alternative | Why rejected |
| --- | --- |
| Commit the spec at `runtime/tournaments/487_current_setup_10arm.json` with a `.gitignore` exception | `runtime/` is runtime state by design; the carve-out makes a generated directory part reviewable and part not. The launcher already writes that exact path as the plan record. |
| Extend `--arms-file` to accept a `{tournament, arms}` object carrying minutes/ports | A CLI schema change for values `--minutes` and `--dashboards` already carry. |
| New `scripts/tournament_catalyst_report.py` | The cockpit already computes the per-arm metrics; a third analyser splits the verdict surface. |
| Carry `hypothesis` through `build_tournament_plan` into the record so the cockpit reads it there | Adds a field to two dataclasses and a reader for prose the cockpit already owns for three other tournaments. |
| Reuse `#608`'s character names | `#608` ran on pre-2026-10-20 code with retired knobs and four duplicate controls. Reusing its names would imply a comparison the data cannot support. |
| One arm per `TOURNAMENT_PRESETS` name (six presets as six arms) | Each preset changes four to nine settings at once; six such arms answer "which preset won", not "which knob matters", and the issue asks for one-parameter variation. |
| Remove the `🚧 Blocked` label from the issue in this station | Label state is the operator's call. This plan records the evidence that the code gate is satisfied and leaves the label alone. |

## Changed assumptions (recorded for the next session)

- The issue says "no tournament has run yet". A ten-arm 720-minute tournament **did** run
  (`#608`, 2026-10-19) — on code that predates every 2026-10-20 gate fix. The plan treats
  "no tournament on the current code" as the operative fact.
- The issue's flat "9 one-parameter variations fetched live from #484's presets" is not
  achievable as written; see the improvement proposal.



