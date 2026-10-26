# Implementation Plan — Issue #843: Rehearse the balanced preset and the 5-minute queue-clear gate together as one arm

Branch: `i843/rehearse-balanced-preset-queue-gate` | Issue: #843

## Summary

Tournament #799 identified two single-knob champions: `balanced-mid` (leading in realized PnL and trade count via dynamic 2¢–4¢ offset) and `queue-clear-5m` (leading in Sharpe proxy, profit factor, and drawdown mitigation via 5-minute queue clearance filter). The Tournament Cockpit HYBRID blueprint specified both knobs simultaneously. However, because both knobs interact with resting quote wait times, their interaction is untested—they may complement each other, overlap redundantly, or induce volume starvation.
This work creates `configs/tournaments/i843-pair-test_arms.json` with 4 isolated arms, builds unit tests in `tests/test_tournament_arms_i843.py`, establishes a pre-registered decision rule and statistical power threshold document in `docs/runs/2026-10-26-843-balanced-queue5-pair.md`, and validates dry-run execution.

---

## Step 0A: CodeRabbit Plan Intake & Analysis

- **Adopted:**
  - 4-arm tournament specification isolating `control` (`{}`), `balanced-only`, `queue5-only`, and `balanced-queue5`.
  - Exact environment keys: `BOT_QUOTE_TOURNAMENT_PRESET=balanced`, `BOT_ORDER_GATE_QUEUE_CLEAR_ON=1`, `BOT_ORDER_QUEUE_MAX_CLEAR_MIN=5`.
  - Exclusion of deployment-layer overrides (stop 4.5¢, deadband 6¢, rescue lead 45s) to avoid confounding factors.
  - Dedicated unit test pinning the 4 arms, their keys, and checking plan build via `scripts.shadow_tournament.build_tournament_plan`.
  - Pre-registered decision rule document committed before run execution with statistical power requirements from `scripts/stat_gate.py`.
- **Rejected:**
  - 50+ lines of speculative interaction regression equations in markdown: Existing run reports (e.g. `docs/runs/2026-10-16-622-refusal-hold-tournament.md`) provide the concise, standard format.
  - Modification of any core configuration file (`core_brain/config.py` is frozen for this issue).
- **Verified:**
  - `configs/tournaments/i799-tournament-test_arms.json` exists and validates arms schema.
  - `scripts/shadow_tournament.py` supports `--arms-file`, `--dry-run`, `--issue`, `--stamp`.
  - `core_brain/config.py` lines 400 & 414 parse `BOT_ORDER_GATE_QUEUE_CLEAR_ON` and `BOT_ORDER_QUEUE_MAX_CLEAR_MIN`.
  - `scripts/stat_gate.py` runs `--dry-calc --sigma <s>` cleanly.

---

## Step 5: Improvement Proposal

- **Proposal:** Comprehensive programmatic validation in `tests/test_tournament_arms_i843.py`.
- **Evidence:** In `scripts/shadow_tournament.py` lines 345-385, arms definitions with unexpected keys or misconfigured preset names can trigger validation errors at runtime or quietly inherit default presets.
- **Classification:** Simplification / edge-case hardening (adopted by default).
- **Impact:** Ensures the arms JSON is not only valid JSON, but builds a clean `TournamentPlan` through `build_tournament_plan` where `balanced-queue5` contains exactly the expected keys and no unexpected keys, and verifies subprocess `--dry-run` invocation exits with code 0.

---

## Tasks

### Task 1: [Design/Config] Define 4-Arm Tournament Configuration File [x]
- **Size:** S
- **Target Files:** `configs/tournaments/i843-pair-test_arms.json`
- **Helper Skill:** `source-driven-development`
- **Depends on:** None
- **Details:**
  - Create `configs/tournaments/i843-pair-test_arms.json` containing an array of 4 arm objects:
    1. `control`: label "Control (Loose Baseline)", color `#00e676`, env `{}`.
    2. `balanced-only`: label "Balanced Only", color `#e040fb`, env `{"BOT_QUOTE_TOURNAMENT_PRESET": "balanced"}`.
    3. `queue5-only`: label "Queue Clear 5m Only", color `#18ffff`, env `{"BOT_ORDER_GATE_QUEUE_CLEAR_ON": "1", "BOT_ORDER_QUEUE_MAX_CLEAR_MIN": "5"}`.
    4. `balanced-queue5`: label "Balanced + Queue Clear 5m", color `#ff6d00`, env `{"BOT_QUOTE_TOURNAMENT_PRESET": "balanced", "BOT_ORDER_GATE_QUEUE_CLEAR_ON": "1", "BOT_ORDER_QUEUE_MAX_CLEAR_MIN": "5"}`.
  - Ensure all names match `^[a-z0-9-]{1,24}$`.
  - Include descriptive fields (`desc`, `thesis`, `profile`) consistent with `i799-tournament-test_arms.json`.
- **Verification:** `python -c "import json; json.loads(open('configs/tournaments/i843-pair-test_arms.json').read())"`

### Task 2: [Testing/TDD] Create Unit Test Suite for Arms Configuration & Plan Resolution [x]
- **Size:** S
- **Target Files:** `tests/test_tournament_arms_i843.py`
- **Helper Skill:** `test-driven-development`
- **Depends on:** Task 1
- **Details:**
  - Test file `tests/test_tournament_arms_i843.py` asserting:
    1. Arms JSON file existence and valid schema.
    2. Exactly 4 arms with exact expected names.
    3. Exact environment variables for each arm (and strictly verifying that no arm contains `BOT_RISK_STOP_ADVERSE_DROP_CENTS`, `BOT_ORDER_REQUOTE_DEAD_BAND_CENTS`, or other safety overrides).
    4. Integration with `build_tournament_plan(issue=843, arms=..., dry_run=True)` succeeds and assigns distinct database paths.
    5. Subprocess `--dry-run` invocation test (`python -m scripts.shadow_tournament --issue 843 --arms-file configs/tournaments/i843-pair-test_arms.json --dry-run`) returns code 0 and valid JSON.
- **Verification:** `python -m pytest -q tests/test_tournament_arms_i843.py`

### Task 3: [Docs/Research] Write Pre-Registered Decision Rule and Run Document [x]
- **Size:** S
- **Target Files:** `docs/runs/2026-10-26-843-balanced-queue5-pair.md`
- **Helper Skill:** `documentation-and-adrs`
- **Depends on:** Task 1
- **Details:**
  - Create `docs/runs/2026-10-26-843-balanced-queue5-pair.md` following the proven pattern from `docs/runs/2026-10-16-622-refusal-hold-tournament.md`.
  - State clearly: `Status: RULE LOCKED, RESULTS PENDING.`
  - Section 1: Pre-registered decision rule:
    - Primary hypothesis: `balanced-queue5` combines the fill speed of the balanced offset band with the adverse-selection filter of the queue gate.
    - Competing hypothesis: Both knobs constrain quote duration, leading to volume starvation or mutual redundancy.
    - Separation standard: "Clearly different" defined across Sharpe proxy ($\pm 25\%$), profit factor, and maximum drawdown.
    - Statistical power threshold: Quote sample sizes required from `stat_gate.py` for $\sigma=0.37$ and $\sigma=0.87$; explicit rule that if required $n$ is not achieved in 24h, no arm is declared winner on nominal PnL.
  - Section 2: Exact commands to launch and measure (PowerShell `scripts.shadow_tournament`, `analyze_arm_database`, `queue_report.py`, `stat_gate.py`).
  - Section 3: Metric recording tables (placeholder for post-run results).
- **Verification:** Document inspection and verification of embedded CLI commands.

### Task 4: [Verification] Execute Dry-Run & Run Regression Suite [x]
- **Size:** XS
- **Target Files:** None (Execution only)
- **Helper Skill:** `verification-before-completion`
- **Depends on:** Tasks 1, 2, 3
- **Details:**
  - Execute `python -m scripts.shadow_tournament --issue 843 --arms-file configs/tournaments/i843-pair-test_arms.json --dry-run`.
  - Run targeted test suite:
    `python -m pytest -q tests/test_tournament_arms_i843.py tests/test_shadow_tournament.py tests/test_tournament_cockpit_arms.py tests/test_tournament_run_number_naming.py`
- **Verification:** Zero errors, all tests pass.

---

## Checkpoints

- **Checkpoint 1 (after Tasks 1 & 2):** Arms configuration file created and verified by unit tests.
- **Checkpoint 2 (after Task 3):** Pre-registered decision rule document locked.
- **Checkpoint 3 (after Task 4):** Dry-run execution verified and full targeted test suite green.
