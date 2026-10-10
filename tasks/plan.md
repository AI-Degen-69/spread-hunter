# Plan — #475 Rework control-center menu navigation and retire the depth-bar trial option

Branch: i475/rework-control-center-menu-navigation-and-retire-depth-trial | Issue: #475
Size: Standard | Type: Code (menu surface + tests + docs)
Stack: PowerShell 7 (`scripts/spread-hunter-menu.ps1`), pytest focused files (text-level +
`pwsh` subprocess tests via `shutil.which("pwsh")` skip guard).
Labels: enhancement, ready-for-agent (no quick-fix label → no lane gate, straight to planning)
## CodeRabbit plan intake (adopted in full)

Adopted from the issue thread (the `@coderabbitai plan` reply), with all five design choices
accepted as the chosen options:

- **DC1 — clean removal** of the single-bar depth-bar trial launcher, no menu replacement.
- **DC2 — keep** manifest replay in `Resume-ShadowRun` unchanged (old trial stores resume as
  before; `Resume-ShadowRun` reads depth only from `$trial.trial_depth_usd`, never `$TrialDepth`).
- **DC3 — single-letter keys** `a` (Storage Audit) and `p` (Storage Prune); CLI words
  `audit`/`storage-audit`/`retention` → `a`, `prune`/`storage-prune` → `p`.
- **DC4 — Enter redraws**, end-of-input/no-console leaves with exit 0, a thrown action error is
  shown and the menu returns.
- **DC5 — relabel** shadow keys 5/6 only (no behaviour change).

**[UNVERIFIED] resolved from code during planning (all confirmed present):**
- `$TrialDepth` param at line 39; `Start-ShadowTrial` at 1469–1603; `t` grid row at 3011;
  `"t"` branch at 3211–3224; `shadow-trial`/`trial-shadow` map entries at 3291–3292; `t` in the
  allow-list at 3317; `shadow-trial` header line at 16.
- `audit`/`prune` switch branches at 3225–3230 (`Invoke-StorageAudit`, `Invoke-StoragePrune
  -Force:$Yes`); action-map `audit`/`storage-audit`/`retention` and `prune`/`storage-prune` at
  3301–3305.
- `Show-MenuGrid` 2987–3032 (r row 3010, t row 3011); prompt `Select [1-9, q]` at 3336;
  invalid message "choose 1-9, or q" at 3233; `Read-MenuChoice` at 3239–3261; entry point
  3333–3341.
- Brace-matching helper to copy from `tests/test_menu_live_state.py` (IndexOf + depth counter,
  lines 34–45). The function-splitting helper pattern (`src.split("function NAME",1)[1]
  .split("\nfunction ",1)[0]`) from `tests/test_menu_shadow_trial.py`.
- `docs/agents/architecture.md` §"Trial feeds (shadow-03, #291)" at 165–176.
- `docs/runs/2026-09-28-paired-depth-experiment.md` exists (paired `$250 vs $500` arm run).

## One improvement proposal (Step 5)

**Adopted (simplification):** none beyond the CodeRabbit plan — the plan is already the minimal
boring solution (100 lines instead of 1000), and every seam is verified. No scope expansion is
proposed. (DC5's optional relabel is kept because the issue explicitly asks for "self-describing"
navigation; it is text-only.)

## Locked constraints (CONSTRAINTS.md)

- Focused suites that must pass after each task:
  `tests/test_menu_shadow_trial.py`, `tests/test_menu_extra_words.py`,
  `tests/test_menu_resume_action.py`, `tests/test_menu_navigation.py`.
- Full-repo sweep stays with CI; locally run only those four.
- Protected (do not modify): `scripts/filter_markets.py`, `scripts/filter_loop.py`,
  `core_brain/config.py`, the `Resume-ShadowRun` body, `Invoke-WithRehearsalTrialEnv`, all other
  action bodies and prompts, `data/orders.db`.
- Never run key `1`, `start`, or any live action. Tests use copied functions + stubs only; never
  load the whole script in a test.

## Dependency graph

- Task 1.1 (retire trial launcher) — no deps. Unblocks 1.2 (the allow-list and branches it
  rewrites must not still contain `t`).
- Task 1.2 (one key set) — Depends on: 1.1. Adds `a`/`p` rows, remaps branches, builds the key
  list, writes `tests/test_menu_navigation.py` (text + one `pwsh` dispatch test).
- Task 1.3 (interactive loop) — Depends on: 1.2. Wraps the entry in `Invoke-InteractiveMenu`,
  changes `Read-MenuChoice`, adds the `pwsh` loop tests.


## Tasks

### Task 1.1 — Retire the depth-bar trial launcher (Size: S)
- Domain: menu/tests. Files: `scripts/spread-hunter-menu.ps1`, `tests/test_menu_shadow_trial.py`,
  `tests/test_menu_resume_action.py`, `docs/agents/architecture.md`.
- Delete: `[double]$TrialDepth = 250` (39), the whole `Start-ShadowTrial` (1469–1603), the `t`
  grid row (3011), the `"t"` switch branch (3211–3224), `shadow-trial`/`trial-shadow` map entries
  (3291–3292), `t` in the allow-list (3317), the `shadow-trial` header line (16). Remove any other
  launcher-only references to `Start-ShadowTrial`/`$TrialDepth`.
- In `test_menu_shadow_trial.py`: delete the six launcher tests; keep the three
  resume/manifest tests; add `test_depth_bar_trial_launcher_is_gone` (Start-ShadowTrial absent;
  regex `\$(script:)?TrialDepth\b` no match; no `shadow-trial`/`trial-shadow` keys and no value
  `"t"`; no `"t"` branch; allow-list has no `t`; `Depth-Bar Trial` absent) and
  `test_trial_aliases_are_unknown_actions` (subprocess over `shadow-trial`, `trial-shadow`, `t`;
  non-zero rc + `Unknown action`).
- In `test_menu_resume_action.py`: drop `"Start-ShadowTrial"` from the launcher tuple.
- In `architecture.md`: state the launcher is retired and no new trial stores come from the menu;
  keep manifest-replay / shared-feed / not-global-filter / `--out-dir` / `--markets-path` claims;
  point paired runs at `docs/runs/2026-09-28-paired-depth-experiment.md`.
- Helper skill: `test-driven-development`. Depends on: —.
- Verify: `python -m pytest -q tests/test_menu_shadow_trial.py tests/test_menu_resume_action.py
  tests/test_menu_extra_words.py` (green); `pwsh -NoProfile -File .\scripts\spread-hunter-menu.ps1
  shadow-trial` exits non-zero with the unknown-action error.

### Task 1.2 — One key set for grid, prompt, error, allow-list and header (Size: M)
- Domain: menu/tests. Files: `scripts/spread-hunter-menu.ps1`, `tests/test_menu_navigation.py`
  (new), `tests/test_menu_extra_words.py` (must stay green).
- Define `$script:MenuKeys = 1..9, r, a, p, q` near the menu functions + a hint formatter
  (`1-9, r, a, p, q`). Build the prompt (`Select [1-9, r, a, p, q]`), the invalid message
  (`choose 1-9, r, a, p, or q`) and the CLI allow-list from it.
- `Show-MenuGrid`: draw `r`, `a`, `p`, `q` in the number-key row format; add `a` "Storage Audit"
  and `p` "Storage Prune" rows to MAINTENANCE & STATUS; relabel shadow `5`→"Stop Shadow Run",
  `6`→"Open Shadow Dashboard".
- `Invoke-LiveAction`: rename `"audit"`/`"prune"` branches to `"a"`/`"p"`, bodies unchanged
  (`Invoke-StorageAudit`, `Invoke-StoragePrune -Force:$Yes`). Action map: `audit`/`storage-audit`/
  `retention`→`a`, `prune`/`storage-prune`→`p`.
- Rewrite the usage header: "loops until q or Ctrl+C"; list `r`, `a`, `p` + their CLI words; one
  line each for `-Watch` and `-Yes`.
- `test_menu_navigation.py` text tests: `test_menu_key_set_is_identical_everywhere`,
  `test_usage_header_names_every_key`, `test_storage_words_map_to_letter_keys`,
  `test_every_key_has_a_branch`. One `pwsh` test (skipped without pwsh) copying
  `Invoke-LiveAction` + key-list helper, stubbing `Invoke-StorageAudit`/`Invoke-StoragePrune`
  (record `-Force`)/`Lsh-*`/`Start-Sleep`: `a`→audit once, `p` passes `Force` per `$Yes`, `x`
  warns with `1-9, r, a, p, or q`.
- Helper skill: `test-driven-development`. Depends on: 1.1.
- Verify: the four focused suites green; `test_menu_extra_words.py` still prints "one action at a
  time" for `audit and`.

### Task 1.3 — Make the interactive menu loop (Size: M)
- Domain: menu/tests. Files: `scripts/spread-hunter-menu.ps1`, `tests/test_menu_navigation.py`.
- Move entry (3333–3341) into `Invoke-InteractiveMenu` (function at column 0, `}` at column 0);
  entry runs it then `exit 0` when no `$Action`.
- `Read-MenuChoice`: Enter→`""`, end-of-input→`$null` (incl. `Read-Host`→`$null` and the
  no-console catch), else the key.
- Each pass: banner+grid+prompt; read choice ($null→return, ""→next pass); save `Minutes`,
  `Hours`, `Watch`, `ResumeDb`, `ShadowPreset`; run `Invoke-LiveAction` in try/catch/finally
  (catch→`Lsh-Fail`; finally restores inputs, clears `ShadowRunId`/`ShadowDbPath`/`StatsDbPath`);
  pause "Press any key to return to the menu"; return on end of input.
- Keep `q`→`exit 0`, key-`1` START confirmation every pass, all other bodies/prompts, Ctrl+C.
  CLI path unchanged.
- `pwsh` loop tests (skipped without pwsh), brace-matching helper from `test_menu_live_state.py`,
  scripted `Read-MenuChoice` queue, stubbed side-effects, `LOOP-ENDED` marker: the eight cases
  (`8,8,q`; `x,8,q`; `a,p,q`; `"",8,q`; `8` then `$null`; first-call throw; `4,4,q` preset reset;
  `1,q` with `no`). Add `test_read_menu_choice_returns_null_at_end_of_input` and
  `test_cli_path_still_single_shot`.
- Helper skill: `test-driven-development`. Depends on: 1.2.
- Verify: the four focused suites green.

## Checkpoints

- After 1.1: trial is gone, aliases error out, resume still replays manifests.
- After 1.2: one key set everywhere; `a`/`p` selectable; header matches.
- After 1.3: menu loops, survives errors, exits on `q`/Ctrl+C/EOF; CLI single-shot intact.
