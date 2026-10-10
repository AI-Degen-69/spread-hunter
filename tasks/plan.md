# Plan — #472 Multi-arm shadow tournament (profile comparison)

Branch: i472/multi-arm-shadow-tournament-profile-comparison | Issue: #472
Size: Standard | Type: Code [Backend/Logic]
Stack: Python, pytest (`tests/test_shadow_tournament.py` + focused regression
suites); no new dependencies.
Labels: ready-for-agent (no quick-fix label: no gate, straight to planning)

## CodeRabbit plan intake

- **Adopted:** the 2-task vertical split (T1 five-profile defaults + selector;
  T2 read-only results reader/writer + launch integration), the exact arm
  order `[control, conservative, balanced, aggressive, prudent]`, ports
  8801–8805, `mode=ro` reader following `pair_fill_report.py`, `run_id`
  filtering following `run_scorer.py`, the fixed results note, and the full
  new-test list.
- **Rejected:** extending `_preset_to_env` field-by-field for queue-hold
  settings — `HUNTER_TOURNAMENT_PRESET` already applies the whole profile in
  `config.py:1354`, so setting the selector per arm is the smaller change;
  the kept offset fields agree numerically with the presets, so they stay as
  display values. Over-split phases merged into 2 tasks; no new abstractions.
- **[UNVERIFIED] resolved from code:** `scripts/__init__.py` EXISTS — no
  package-marker change needed. `kpi.MERGE_METHODS` (`kpi.py:879`) and
  `kpi.NON_TRADE_CLOSE_METHODS` (`kpi.py:882`) are importable — import them,
  do not copy names. `core_brain/runtime_paths.py` has no reports helper —
  anchor `reports/tournaments/` to `ROOT` the same way `record_path` anchors
  `runtime/tournaments/`. `HUNTER_TOURNAMENT_PRESET` is real
  (`config.py:1354`, tested in `test_dynamic_offset.py:205-232`). Test
  fixtures seed via direct SQL with the real registry schema (pattern from
  `tests/test_run_scorer.py:100`).

## Locked constraints (CONSTRAINTS.md)

- Zero regressions: `tests/test_shadow_tournament.py`,
  `tests/test_dynamic_offset.py`, `tests/test_pair_fill_report.py`,
  `tests/test_run_scorer.py`, `tests/test_run_attribution.py`,
  `tests/test_shadow_guard.py` stay green.
- Anti-cheat: no skipping/disabling tests, no deleting assertions, no
  suppressing linters; no new deps.
- Reader is `mode=ro` read-only; unavailable arms report status with error
  text, never zero-filled counts.
- Protected (do not modify): `core_brain/config.py`, `core_brain/quotes.py`,
  shadow execution/safety files, registry/KPI/scorer logic, dashboard,
  stat-gate files, `data/orders.db`. No arm ranking, no winner naming.

## Interface contracts

- `read_arm_results(db_path: Path, run_id: str) -> dict` — pure reader.
  Returns `fill_events` (rows in `fills`), `filled_orders` (distinct
  `order_uuid`), `close_events` (trade closes only, excluding
  `NON_TRADE_CLOSE_METHODS`), `merges` (`MERGE_METHODS`), `single_leg_exits`
  (other trade closes), `realized_pnl_usd` (sum over trade closes, labeled
  modeled shadow P&L), `status` (`ok` | `no_fills` | `unavailable` + `error`).
  Every query filters by `run_id`.
- `write_tournament_results(plan: TournamentPlan, exit_codes: list[int]) -> Path`
  — pure writer, separate from subprocess code. Writes
  `reports/tournaments/<issue>_<stamp>_results.json` with issue, stamp,
  minutes, markets path, per-arm (name, db path, run id, dash port, exit
  code, reader values), and the fixed note: "Shadow results are modeled. Do
  not choose a production winner from this file. The stat gate decides."
- `TournamentPlan.to_dict()` gains `results_path`; `--dry-run` prints it and
  writes nothing.

## Dependency graph

```
T1 (five-profile defaults + selector) ──> T2 (results reader/writer + launch hook)
```

T1 first (the results step and its verification need five arms). T2 depends
on the T1 arm layout for full verification but touches separate functions.

## Tasks

- [x] **T1** [M] [Backend/Logic] — Five-profile defaults with full preset
  application.
  - Files: `scripts/shadow_tournament.py`, `tests/test_shadow_tournament.py`.
  - Change: `default_order` becomes
    `[control, conservative, balanced, aggressive, prudent]`; each default
    arm env sets `HUNTER_TOURNAMENT_PRESET` to its own name; keep existing
    `_preset_to_env` offset fields, port checks, 8799 refusal, custom
    `--arms-file` handling, and `TOURNAMENT_PRESETS` untouched.
  - Tests: update default-membership test to exact five-name order (existing
    `len == 4` assertion becomes 5); new: selector-equals-name per arm,
    offset env numerically agrees with `TOURNAMENT_PRESETS` (prudent 0.60x/4c,
    control dynamic flag `"0"`), ports `[8801..8805]`, five distinct DB paths
    each containing `_tournament_0N_<name>_`, five distinct run ids.
    Existing invalid-name / duplicate / `HUNTER_` prefix / 8799 /
    occupied-port / existing-DB tests unchanged and passing.
  - Verify: `python -m pytest -q tests/test_shadow_tournament.py`
  - Depends on: none

- [x] **T2** [M] [Backend/Logic] — Read-only per-arm results + launch hook.
  - Files: `scripts/shadow_tournament.py`, `tests/test_shadow_tournament.py`.
  - Change: `read_arm_results` (`mode=ro`, `run_id`-filtered, imports
    `MERGE_METHODS` + `NON_TRADE_CLOSE_METHODS` from `core_brain.kpi`);
    `write_tournament_results` building the JSON path like `record_path`
    (mkdir parents); `launch_tournament` calls the writer for every arm
    (including failed ones) after workers exit; plan JSON gains
    `results_path`; stdout prints a short per-arm table with no ranking.
  - Tests (direct-SQL fixtures with real schema): run-A/run-B filtering;
    two partial fills → `fill_events == 2`, `filled_orders == 1`;
    `shadow_merge` + exit → `merges == 1`, `single_leg_exits == 1`,
    `close_events == 2`; sentinel (`venue_sync`) excluded; P&L equals
    trade-close sum; no-fill DB → `no_fills` with zero counts; missing DB →
    `unavailable` with error text, no zero-filled counts, no file created;
    writer with exit codes 0/1 → per-arm codes + fixed note;
    dry-run prints `results_path` and writes nothing.
  - Verify: `python -m pytest -q tests/test_shadow_tournament.py`
  - Depends on: T1

## One improvement proposal (evidence-based)

The issue promises arms run "on the same live books", but the launcher only
shares the market *list* — evidence: issue text "compare quote-placement
profiles (control, conservative, balanced, aggressive, prudent) on the same
live books" versus `scripts/shadow_tournament.py`, which has only
`--markets-path` (a market-list feed re-read per cycle) and spawns one
independent `core_brain.shadow_run` worker per arm with no shared book
fetcher. **Classification: edge-case hardening → adopt-by-default** (folded
into T2 + run instructions): recommend a frozen `--markets-path` copy for
the run and record the residual timing skew in the results note, so the
"same books" claim is never overstated.

## Checkpoints

- After T1: bare `--dry-run` shows five arms on ports 8801–8805.
- After T2: results JSON + per-arm table exist; no ranking, no winner.
