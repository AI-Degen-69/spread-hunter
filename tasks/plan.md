# Implementation Plan: Retired Environment Variable Shared Module & Vocabulary Alignment
Branch: i695/retired-env-shared-module | Issue: #695 (absorbing #694)

## Problem Statement & Scope
Currently, `RETIRED_ENV_OVERRIDES` in `scripts/shadow_tournament.py:257-294` and the warning loops in `core_brain/config.py:760-781` duplicate the retired `HUNTER_*` keys inventory. Furthermore, `config.py` warns and suggests legacy `HUNTER_*` aliases (Issue #694) while `shadow_tournament.py` provides canonical `BOT_*` replacements.

This plan resolves Issue #695 and absorbs Issue #694 into a single delivery:
1. Introduce a lightweight, zero-dependency leaf module `core_brain/retired_env.py`.
2. Define canonical `BOT_*` replacements and guidance in a single source of truth.
3. Wire `core_brain/config.py` and `scripts/shadow_tournament.py` to import from this module.
4. Align runtime warnings to point to canonical `BOT_*` replacements without altering runtime parsing behavior.
5. Add thorough tests (AST import purity, identity, warning strings, silence rules) and update documentation ledgers.

---

## CodeRabbit Plan Intake
- **Adopted from CodeRabbit:**
  - Data-only shared leaf module `core_brain/retired_env.py` using `from __future__ import annotations` and stdlib-only imports (`dataclasses`, `typing`).
  - Small frozen dataclass `RetiredEnvKey` mapping key name, plan guidance, runtime warning template, and arguments.
  - AST test ensuring `core_brain/retired_env.py` imports only standard library modules.
  - Object identity assertion `scripts.shadow_tournament.RETIRED_ENV_OVERRIDES is core_brain.retired_env.RETIRED_ENV_OVERRIDES`.
  - Runtime warning coverage in tests checking empty-value silence and warning triggers for non-empty values.
- **Enhanced / Resolved Differences:**
  - CodeRabbit's initial outline proposed preserving the legacy `HUNTER_*` alias suggestions in warning templates. Per Issue #694 absorption, we modernize the warning arguments to canonical `BOT_*` targets (`BOT_RISK_STOP_ADVERSE_DROP_CENTS`, `BOT_RISK_STOP_MAX_DRAWDOWN_PCT`, `BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC`, etc.).
  - Added clean iterator `iter_runtime_warnings()` to keep `core_brain/config.py` loop concise and decoupled from dataclass field details.

---

## Interface Contract (`api-and-interface-design`)

### Module: `core_brain/retired_env.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional

@dataclass(frozen=True)
class RetiredEnvKey:
    """Specification of a retired environment variable.
    
    Attributes:
        name: Environment variable name (e.g. 'HUNTER_STOP_LOSS_TRIGGER_USD_CENTS').
        plan_guidance: Guidance for tournament planner / static refusal checks.
        runtime_warning: Optional format string for runtime logger warning. None if purged without warning.
        runtime_warning_args: Arguments passed to runtime logger warning alongside name.
    """
    name: str
    plan_guidance: str
    runtime_warning: Optional[str] = None
    runtime_warning_args: tuple[str, ...] = ()

# Canonical tuple of all retired environment variable records
RETIRED_ENV_KEYS: tuple[RetiredEnvKey, ...] = (
    # 1. HUNTER_SINGLE_BUY_GRACE_SEC (warned, #619) -> BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC
    # 2. HUNTER_STOP_LOSS_TRIGGER_USD_CENTS (warned, #619) -> BOT_RISK_STOP_ADVERSE_DROP_CENTS / BOT_RISK_STOP_MAX_DRAWDOWN_PCT
    # 3. HUNTER_STOP_LOSS_TRIGGER_PCT (warned, #619) -> BOT_RISK_STOP_MAX_DRAWDOWN_PCT
    # 4. HUNTER_REFUSAL_HOLD_POLICY (warned, #622) -> no replacement
    # 5. HUNTER_REFUSAL_HOLD_DRIFT (warned, #622) -> no replacement
    # 6. HUNTER_SINGLE_BUY_MAX_LOSS_PCT (warned, #573/#619) -> BOT_RISK_STOP_MODE + BOT_RISK_STOP_MAX_DRAWDOWN_PCT
    # 7. HUNTER_SINGLE_BUY_MAX_LOSS_USD (warned, #573/#619) -> BOT_RISK_STOP_MODE + BOT_RISK_STOP_ADVERSE_DROP_CENTS
    # 8. HUNTER_REWARD_OFFSET (purged 2026-10-12, no runtime warning)
)

# Exported dictionary mapping variable name to planner guidance string
RETIRED_ENV_OVERRIDES: dict[str, str] = {
    record.name: record.plan_guidance for record in RETIRED_ENV_KEYS
}

def iter_runtime_warnings() -> Iterator[tuple[str, str, tuple[str, ...]]]:
    """Yield (env_var_name, format_template, args) for keys configured with a runtime warning."""
    for record in RETIRED_ENV_KEYS:
        if record.runtime_warning is not None:
            yield (record.name, record.runtime_warning, record.runtime_warning_args)
```

---

## Improvement Proposal (Adopted: Simplification)
- **Evidence:** `core_brain/config.py:760-781` currently writes 3 separate `for` loops across hardcoded tuples, while `scripts/shadow_tournament.py:257-294` writes a separate dictionary literal with divergent phrasing.
- **Proposal:** Export `iter_runtime_warnings()` from `core_brain/retired_env.py`. This collapses the 3 loops in `core_brain/config.py` into 1 unified, readable loop without inspecting internal record fields, and exports `RETIRED_ENV_OVERRIDES` directly for `shadow_tournament.py`.
- **Classification:** Simplification / edge-case hardening (adopted by default).

---

## Task Decomposition

### Task 1: Create Data-Only Shared Module (`core_brain/retired_env.py`)
- **Domain:** `[Backend/Logic]`
- **Size:** S (1 file, ~80 lines)
- **Files:** `core_brain/retired_env.py`
- **Depends on:** None
- **Details:**
  - Create `core_brain/retired_env.py` with `from __future__ import annotations`, standard library imports only (`dataclasses`, `typing`).
  - Implement `RetiredEnvKey`, `RETIRED_ENV_KEYS`, `RETIRED_ENV_OVERRIDES`, and `iter_runtime_warnings()`.
  - Align warning strings to name canonical `BOT_*` targets per Issue #694.
  - Preserve `HUNTER_REWARD_OFFSET` as a planner refusal without a runtime warning.
- **Verification:** Import check via Python command; no circular imports.

### Task 2: Connect Consumers (`core_brain/config.py` & `scripts/shadow_tournament.py`)
- **Domain:** `[Backend/Logic]`
- **Size:** S (2 files modified)
- **Files:** `core_brain/config.py`, `scripts/shadow_tournament.py`
- **Depends on:** Task 1
- **Details:**
  - In `core_brain/config.py`: import `iter_runtime_warnings` from `core_brain.retired_env`. Replace lines 760–781 with a single loop over `iter_runtime_warnings()`. Preserve logger name (`__name__`), warning level, and `if os.environ.get(...)` check.
  - In `scripts/shadow_tournament.py`: remove the local dictionary literal `RETIRED_ENV_OVERRIDES`. Import `RETIRED_ENV_OVERRIDES` from `core_brain.retired_env` right after the existing `core_brain.config` import.
- **Verification:** Run `python -m pytest -q tests/test_stop_loss_config.py tests/test_shadow_tournament.py tests/test_config_single_source.py::test_env_example_documents_only_real_canonical_variables`.

### Task 3: Comprehensive Test Suite & Cross-Check Pinning
- **Domain:** `[Test]`
- **Size:** S (1 new test file, 1 modified test file)
- **Files:** `tests/test_retired_env.py` (new), `tests/test_shadow_tournament.py`
- **Depends on:** Task 2
- **Details:**
  - Create `tests/test_retired_env.py`:
    - AST inspection: `core_brain/retired_env.py` imports only `__future__` and standard libraries.
    - All keys start with `HUNTER_` and are unique.
    - Non-blank guidance for every key in `RETIRED_ENV_OVERRIDES`.
    - Check runtime warnings via `core_brain.config.load()`:
      - All 7 warned keys emit exact warning text naming canonical `BOT_*` targets.
      - Empty string (`""`) is silent.
      - Non-empty whitespace (`" "`) and `"0"` trigger warning.
      - `HUNTER_REWARD_OFFSET` produces no runtime warning.
  - In `tests/test_shadow_tournament.py`:
    - Update `test_retired_env_inventory_matches_the_i492_inventory` to assert `RETIRED_ENV_OVERRIDES is CORE_RETIRED_ENV_OVERRIDES`.
    - Assert runtime warning keys in `RETIRED_ENV_KEYS` match `I492_RETIRED_KNOBS - {"HUNTER_REWARD_OFFSET"}`.
- **Verification:** Run `python -m pytest -q tests/test_retired_env.py tests/test_shadow_tournament.py tests/test_stop_loss_config.py`.

### Task 4: Documentation & Noticed Ledger Update
- **Domain:** `[Docs]`
- **Size:** XS (1 file modified)
- **Files:** `docs/issues/684-noticed-but-not-touching.md`
- **Depends on:** Task 3
- **Details:**
  - In `docs/issues/684-noticed-but-not-touching.md`, update rows 14 & 15:
    - Row 14 (vocabulary alignment): mark `resolved` by `#695 / #694`.
    - Row 15 (duplicate inventory): mark `resolved` by `core_brain/retired_env.py`.
- **Verification:** Review file diff.

---

## Checkpoints
- **Checkpoint 1 (after Task 2):** Consumers successfully imported; existing test suites pass.
- **Checkpoint 2 (after Task 3):** All AST, identity, vocabulary, and warning tests passing green.
- **Checkpoint 3 (after Task 4):** Documentation updated; tree ready for review.
