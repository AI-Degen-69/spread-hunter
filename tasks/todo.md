# Checklist: Issue #695 (absorbing #694)

- [x] Task 1: Create data-only shared module `core_brain/retired_env.py`
  - [x] Define `RetiredEnvKey` frozen dataclass
  - [x] Define `RETIRED_ENV_KEYS` with canonical `BOT_*` replacements (#694)
  - [x] Define `RETIRED_ENV_OVERRIDES` dictionary
  - [x] Define `iter_runtime_warnings()` helper
- [x] Task 2: Connect consumers to `core_brain/retired_env.py`
  - [x] In `core_brain/config.py`: import `iter_runtime_warnings` and replace 3 loops with 1 loop
  - [x] In `scripts/shadow_tournament.py`: import `RETIRED_ENV_OVERRIDES` from `core_brain.retired_env`
  - [x] Verify `test_env_example_documents_only_real_canonical_variables` and existing tests pass
- [x] Task 3: Comprehensive test suite & cross-check pinning
  - [x] Create `tests/test_retired_env.py` (AST imports check, vocabulary check, runtime warning checks, silence checks)
  - [x] Update `tests/test_shadow_tournament.py` (assert dictionary object identity and runtime warning subset)
  - [x] Run targeted tests: `tests/test_retired_env.py`, `tests/test_shadow_tournament.py`, `tests/test_stop_loss_config.py`
- [x] Task 4: Documentation & noticed ledger update
  - [x] Update `docs/issues/684-noticed-but-not-touching.md` lines 14-15 to mark #694 and #695 resolved
