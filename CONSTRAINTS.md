# Quality Guardrails & Constraints — Issue #695 (absorbing #694)
Branch: `i695/retired-env-shared-module` | Issue: `#695`

## 1. Zero Regressions & Verification Gates
- **Targeted Test Suites:**
  - `python -m pytest -q tests/test_stop_loss_config.py tests/test_shadow_tournament.py tests/test_retired_env.py`
  - `python -m pytest -q tests/test_config_single_source.py::test_env_example_documents_only_real_canonical_variables`
- **Full Suite in CI:** Local verification runs only the targeted suites matching the touched modules. The full test suite runs in GitHub CI on Ubuntu and Windows.

## 2. Architecture & Dependency Invariants
- **Leaf Module Isolation:** `core_brain/retired_env.py` must be a pure data-and-typing leaf module.
- **Imports Constraint:** `core_brain/retired_env.py` must ONLY import from `__future__` and Python standard library modules (`dataclasses`, `typing`). No imports from `core_brain`, `scripts`, or third-party packages. An AST test will enforce this.
- **Zero Circular Dependencies:** Neither `core_brain.config` nor any other module may create a circular dependency with `core_brain.retired_env`.
- **Runtime Purity:** `core_brain/retired_env.py` must perform NO I/O, NO logging, and NO environment reads (`os.environ`) at import time.

## 3. Behavioral Invariants & Single Source
- **Single Source of Truth:** `RETIRED_ENV_OVERRIDES` must be defined once in `core_brain/retired_env.py`. `scripts/shadow_tournament.py` must import it by name.
- **Runtime Warning Parity & Modernization (#694):**
  - `core_brain/config.py` must import warning records/iterator from `core_brain.retired_env`.
  - Warnings must modernize replacement vocabulary to canonical `BOT_*` targets instead of legacy `HUNTER_*` aliases (Issue #694).
  - Both canonical and legacy environment variable names must continue to parse identically at runtime.
- **Refusal Consistency:** Tournament arm planning in `scripts/shadow_tournament.py` must continue to refuse all 8 keys in `I492_RETIRED_KNOBS` with the exact actionable guidance.
- **Exclusion Invariant:** `HUNTER_REWARD_OFFSET` is planner-only (purged with no replacement) and must NEVER produce a runtime warning in `core_brain/config.py`.

## 4. Anti-Cheat & Quality Rules
- **No Test Disabling:** No skipping, muting, or removing assertions in existing test files.
- **Independent Test Sets:** `tests/test_shadow_tournament.py` maintains its test-owned `I492_RETIRED_KNOBS` and `RETIRED_GUIDANCE` sets to ensure independent verification against drift.
- **Object Identity:** Test must assert `scripts.shadow_tournament.RETIRED_ENV_OVERRIDES is core_brain.retired_env.RETIRED_ENV_OVERRIDES`.

## 5. Scope Guard
- Touch only:
  - `core_brain/retired_env.py` (new)
  - `core_brain/config.py`
  - `scripts/shadow_tournament.py`
  - `tests/test_retired_env.py` (new)
  - `tests/test_shadow_tournament.py`
  - `docs/issues/684-noticed-but-not-touching.md`
- Any unrelated discovery must be recorded in `docs/issues/695-noticed-but-not-touching.md`.
