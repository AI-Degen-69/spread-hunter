# Plan — Issue #492: Re-run the original 10-arm shadow tournament on fixed code

Branch: test/tournament-rerun-fixed-code-492 | Issue: #492
Size Tier: Standard | Task Types: Config, Code, Test, Docs

## CodeRabbit Plan Intake (Station II Step 0A)

- **Adopted**
  1. Gate the rerun on the two fixes being present on the run revision — verified as
     ancestors of `origin/main`: `e7ddc45` (hard-stop sells into the book read inside the
     exit call; `trigger_bid` / `fresh_bid` on every close) and `bec4589` (#490, merge legs
     attributed by quoted side). CodeRabbit's "the fixes are not on this revision" was
     already stale when it was written.
  2. Per-issue arms-file lookup, so `--issue 492 --run-number 1 --dry-run` prints the ten-arm
     plan without an extra flag (Design Choice 3, option 1).
  3. Record the arms source in the plan record (`arms_source`) so a run traces back to the
     definition file it used. The hash CodeRabbit also proposed was rejected: every arm's
     `env` is embedded verbatim in the same record, so a hash is a second identity for the
     same bytes.
  4. One ten-arm run in one 12-hour window with `--dashboards`, so the launch-time port check
     covers 8801–8810 (Design Choice 5, option 1).
  5. Monitoring through the existing hub on the plan record (Design Choice 4, option 2). No
     cockpit change.
- **Rejected**
  1. `scripts/tournament_arms/<issue>.json` — this repo already keeps per-issue arms under
     `configs/tournaments/` (`i608_tournament-1_arms.json`, `i622_tournament-1_arms.json`). A
     second location splits one convention into two.
  2. "Stop the rerun because `484_12hr.json` / `485_12hr.json` are missing." The arm
     definitions were already committed for this issue in `ba96f94`, and their values are the
     first run's — independently corroborated by #487's own catalyst list ($0.045 control,
     $0.030 conservative, $0.065 aggressive). Nothing was rebuilt from presets or cockpit
     copy.
  3. Hard-stop audit counts and a summary-only mode inside the launcher — the comparison is a
     reporting step (`scripts/pair_fill_report.py`), and this issue asks for the run.
  4. "Byte-for-byte identical env" — impossible on this code; see contradictions 1 and 2.

## Contradictions found between the issue and the code

1. **The stop knob is retired.** `HUNTER_STOP_LOSS_TRIGGER_USD_CENTS` (and, before #573,
   `HUNTER_SINGLE_BUY_MAX_LOSS_USD`) no longer exists: `core_brain/config.py:760-768` names it
   and ignores it with a warning; #619 replaced it with the entry-relative `ABSOLUTE_DROP`
   trigger. Keeping it would run every arm with **no strategy stop** (`bot_risk_stop_mode`
   defaults to `NONE`) — a bigger change than the two fixes this rerun isolates.
   **Locked decision:** re-express the same threshold per arm as
   `HUNTER_STOP_LOSS_MODE=ABSOLUTE_DROP` + `HUNTER_DROP_CENTS=<same dollars>`
   (4.5¢→0.045, 3.0¢→0.030, 5.0¢→0.050, 6.5¢→0.065, 3.8¢→0.038; `HUNTER_DROP_CENTS` is
   dollars per share despite its name, range 0.01–0.90). The operator may veto this; the only
   other valid option is no stop at all.
2. **The grace knob is retired.** `HUNTER_SINGLE_BUY_GRACE_SEC` has no like-for-like
   replacement (`aged_out_rescue_lead_sec` is a different mechanism, 900 s default), so it is
   dropped from the five queue arms. Consequence: snapstop now differs from aggressive only
   by its 3.0¢ stop; its 0 s grace cannot be expressed on this code.
3. **`control` could not load.** The file carried `HUNTER_TOURNAMENT_PRESET=control`;
   `TOURNAMENT_PRESETS` is now `{conservative, balanced, aggressive, prudent, hybrid,
   baseline}` and `config.from_env` raises on an unknown name (`config.py:580-584`). The
   control child would have died at startup while the other nine kept posting. Dropped;
   control is the shipped defaults plus `HUNTER_DYNAMIC_OFFSET=0`, which is what #484's
   control measured.
4. **`HUNTER_REWARD_OFFSET` is dead.** Purged 2026-10-12. The field default
   (`bot_quote_spread_static_offset_cents = 0.020`) already equals the 0.02 the control arm
   wrote, so dropping the env line changes nothing.
5. **The acceptance command loaded no arms file.** `main()` read arms only from
   `--arms-file`, so `--issue 492 --run-number 1 --dry-run` printed the four default presets
   (`arms=4`). Fixed by the convention lookup.
6. **The referenced plan files do not exist.** `runtime/tournaments/484_12hr.json` and
   `485_12hr.json` are absent from the working tree and from git history. Logged in
   `docs/issues/492-noticed-but-not-touching.md`.

## Improvement Proposal (Station II Step 5)

- **Evidence:** the shipped file carried `"HUNTER_TOURNAMENT_PRESET": "control"` while
  `core_brain/config.py:581-584` raises
  `ValueError(f"HUNTER_TOURNAMENT_PRESET={preset_name!r} is unknown…")`. Nothing in
  `build_tournament_plan` noticed, so the failure lands in a child after the run has started.
- **Proposal (edge-case hardening — adopted):** validate each arm's
  `HUNTER_TOURNAMENT_PRESET` against `TOURNAMENT_PRESETS` in `build_tournament_plan` and
  refuse the plan up front.


---

## Tasks

### [x] T1 — Restore the ten arm definitions as env this code can actually read
- **Size:** S | **Domain:** `[Config]` | **Skill:** `constraint-driven-development`
- **Depends on:** none
- **Target files:** `configs/tournaments/i492_tournament-1_arms.json`
- **What is built:** ten arms in #484→#485 order. Retired stop knob → `ABSOLUTE_DROP` +
  `HUNTER_DROP_CENTS`; grace and `HUNTER_REWARD_OFFSET` dropped; `control` no longer names a
  preset. Every live value (offset mult/min/max, dead band, hold queue, hold-below) is the
  first run's, unchanged.
- **Verification:** `test_i492_arms_restore_the_first_run_dimensions`,
  `test_i492_arms_carry_no_retired_knob`, `test_i492_arms_carry_no_487_catalyst_tweak`.

### [x] T2 — Per-issue arms-file lookup and arms provenance
- **Size:** S | **Domain:** `[Backend/Logic]` | **Skill:** `code-simplification`
- **Depends on:** T1
- **Target files:** `scripts/shadow_tournament.py`
- **What is built:** when `--arms-file` is absent and `--run-number` is given, `main()` reads
  `configs/tournaments/i<issue>_tournament-<run>_arms.json` if it exists (the explicit flag
  still wins; no file keeps the shipped presets), logs the source, and records `arms_source`
  in the plan JSON.
- **Verification:** `test_run_numbered_dry_run_reads_the_conventional_arms_file`,
  `test_explicit_arms_file_beats_the_convention`.

### [x] T3 — Refuse an unknown preset selector at plan time
- **Size:** XS | **Domain:** `[Backend/Logic]` | **Skill:** `test-driven-development`
- **Depends on:** T1
- **Target files:** `scripts/shadow_tournament.py`
- **What is built:** `build_tournament_plan` raises `ValueError` naming the arm and the valid
  presets when `HUNTER_TOURNAMENT_PRESET` is unknown.
- **Verification:** `test_unknown_preset_selector_is_refused_at_plan_time`.

### [x] T4 — Pin the plan shape and per-arm loadability
- **Size:** M | **Domain:** `[Test]` | **Skill:** `verification-before-completion`
- **Depends on:** T1, T2, T3
- **Target files:** `tests/test_shadow_tournament.py`
- **What is built:** ten arms load through `core_brain.config.load()`; slugs `<base>-1`,
  ports 8801–8810, ten distinct stores and run ids, no `orders.db`; dry-run writes nothing.
- **Verification:** `test_every_i492_arm_loads_its_own_config`,
  `test_i492_plan_names_ten_fresh_stores_and_ports`.

### [x] T5 — Operator run sheet
- **Size:** XS | **Domain:** `[Docs]` | **Skill:** `documentation-and-adrs`
- **Depends on:** T1–T4
- **Target files:** `docs/runs/2026-10-18-492-tournament-rerun.md`
- **What is built:** the exact launch command, expected artifacts, the deviation table (stop
  translation, lost grace), and what the comparison must report.

### [x] T6 — Anomaly log
- **Size:** XS | **Domain:** `[Docs]`
- **Depends on:** none
- **Target files:** `docs/issues/492-noticed-but-not-touching.md`
- **What is built:** the missing 484/485 plan files, other arms files still carrying retired
  knobs, the unreachable `_preset_to_env` branch, cockpit copy frozen on retired knobs.

## Checkpoints
- **CP1 (after T1+T3):** all ten arms build a plan and every arm's env loads — the class of
  failure that would have silently cost an arm is now either refused up front or green.
- **CP2 (after T2+T4):** the issue's acceptance command exits 0 with ten arms, and the focused
  suites (shadow tournament, run-number naming, hub) are green.

## How to verify (operator — no tests)

1. Open `configs/tournaments/i492_tournament-1_arms.json`: ten entries, `control` first,
   `snapstop` last, every arm carrying `HUNTER_STOP_LOSS_MODE` + `HUNTER_DROP_CENTS`, and no
   grace key anywhere.
2. Run `python -m scripts.shadow_tournament --issue 492 --minutes 720 --run-number 1 --dry-run`
   and read the JSON: `arms` has the ten slugs `control-1` … `snapstop-1`, ports 8801–8810,
   and `record_path` is `runtime/tournaments/i492_tournament-1.json`.
3. When you are ready to spend the twelve hours, launch with `--dashboards`, then open
   `http://127.0.0.1:8800` for the overview and 8801–8810 for one dashboard per arm; ten
   `data/i492_tournament-1_<slug>-1.db` files must appear and keep growing.

## Deferred (not this change)
- The 12-hour run itself and the rerun-vs-first-run comparison report — the operator launches,
  and the report needs the finished stores. The comparison must state the stop translation and
  call the grace dimension unreproducible, or it will credit the fixes with a stop change.
