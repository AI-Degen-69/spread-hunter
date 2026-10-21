# SPEC — First 10-arm tournament from the current setup (#487)

## Goal

Turn "run a 10-arm shadow tournament from today's configuration" into an artifact a
launcher and a cockpit can both read: one **tracked arm spec** holding ten arm
definitions (one control at the current settings, nine one-knob variations), and a
cockpit that shows those ten arms with the hypothesis each one tests.

No tournament is launched by this issue. The launch is the operator's command.

## Why the premise was reset

The issue's original framing ("tweak the single worst-performing catalyst per arm" from
first-run forensics) is stale, and the operator said so on 2026-10-21. Two facts from the
repo, verified live:

- Tournament **#608** did run 10 arms for 720 minutes, 2026-10-19 03:51 → 15:51
  (`runtime/tournaments/i608_tournament-1.json`, `reports/tournaments/i608_tournament-1_results.json`,
  10 × ~110 MB stores in `data/`). Four of its ten arms carried an empty `env`, so they
  were the same run four times, and the arms its descriptions named (`stop_loss_trigger_usd_cents`,
  `single_buy_grace_sec`) had already been retired as inert knobs.
- Every code-gate ticket the issue's own blocking comment named closed on **2026-10-20**
  (#708 12:51Z, #686 13:28Z, #687 16:01Z, #646 16:23Z; #659 closed), and the config
  vocabulary was canonicalised the same day (#734, #739). The #608 run predates all of it.

So: a tournament has never run on the current code, and the arm vocabulary it would have
used has changed name. That is the gap this issue closes.

## Acceptance criteria

1. Ten arms are defined, resolved from the current configuration: one **control** whose
   `env` is empty (the shipped defaults plus the operator's `.env`), plus nine arms each
   setting **exactly one** `BOT_*` variable to a different value.
2. Where `core_brain.config.TOURNAMENT_PRESETS` (the `#484` presets) ships the knob, the
   arm's value is read **from the live preset table** and the test asserts that equality
   rather than a re-typed literal. The presets ship five single-knob values that differ
   from the current setup; the remaining four arms move levers no preset ships at a
   different value and say so in their own description. (Amended in Station III with the
   operator's approval — see the deviation note in
   `docs/runs/2026-10-21-487-current-setup-tournament.md`.)
3. The arm spec validates through `build_tournament_plan` with no port collision and no
   naming collision: ports `8801`–`8810`, unique DB paths, unique run ids.
4. The Tournament Cockpit serves the ten arms with their hypothesis. `ARMS_CONFIG_487`
   and `TOURNAMENTS["487"]` describe the current-setup run, not the superseded catalyst
   round, and `resolve_tournament_arms("487")` finds the run's plan record at
   `runtime/tournaments/487_current_setup_10arm.json`.
5. The run's launch and analysis path is documented and dry-run validated at 720 minutes
   across ports 8801–8810.

## Artifacts

| Artifact | Path | Tracked |
| --- | --- | --- |
| Arm spec (source of truth for env) | `configs/tournaments/i487_current_setup_10arm_arms.json` | yes |
| Plan record (written at launch) | `runtime/tournaments/487_current_setup_10arm.json` | no — `runtime/` is not in git |
| Launcher log | `runtime/tournaments/487_current_setup_10arm.log` | no |
| Per-arm stores | `data/487_tournament_<NN>_<arm>_current_setup_10arm.db` | no |
| Results | `reports/tournaments/487_current_setup_10arm_results.json` | no |
| Runbook | `docs/runs/2026-10-21-487-current-setup-tournament.md` | yes |

The issue names `runtime/tournaments/487_current_setup_10arm.json` as "the executable
tournament plan". That path is honoured **as the plan record** the launcher writes, reached
with the existing `--stamp current_setup_10arm` flag. It is not committed: `runtime/` is
runtime state by design (`docs/agents/architecture.md`, "Runtime state across the rename",
and the `/runtime/**/*.json` rule in `.gitignore`). The committed half — the part that has
to survive a clean checkout and be reviewable — is the arm spec under `configs/tournaments/`,
which is the same split `#492` already uses.

## Edge cases

- **Arm name vs. preset name.** `build_tournament_plan` appends
  `BOT_QUOTE_TOURNAMENT_PRESET=<arm name>` whenever an arm's name is a key of
  `TOURNAMENT_PRESETS`. That would silently turn a one-knob arm into a six-knob arm. No arm
  name may equal a preset name (`conservative`, `balanced`, `aggressive`, `prudent`,
  `hybrid`, `baseline`). The spec's test asserts the disjointness, so a future rename cannot
  reintroduce it.
- **Arm name shape.** `ARM_NAME_PATTERN` is `^[a-z0-9-]{1,24}$`. The test pins it.
- **An unknown preset selector is a planned failure.** The launcher refuses at plan time;
  no arm sets `BOT_QUOTE_TOURNAMENT_PRESET`.
- **Two plan records for `487`.** The obsolete static paths (`data/i487_tournament_*.db`)
  live only in the current `ARMS_CONFIG_487`; they are replaced by the new arm slugs. The
  resolver falls back to the static config when no record exists, and those static
  db/run_id values are placeholders until the run launches — same contract as `#492`.
- **A missing plan record is not an error.** Before launch the cockpit shows the ten arms
  with `NO_DATA`; after launch it resolves db/run_id/port from the record.

## Out of scope

- Launching the 12-hour run. Operator's command; see the runbook.
- Any comparison or verdict. Comparative evaluation happens after the run finishes.
- A new analysis script. The cockpit already computes the per-arm metrics
  (`analyze_arm_database`, `generate_cockpit_html`); a third parallel analyser is invention.
- Touching `data/orders.db`, any live-money path, or the strategy modules.
- Any change to `TOURNAMENT_PRESETS`, `PICKER_PROFILE` or shipped `MakerConfig` defaults.
