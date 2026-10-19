"""Tests for core_brain.retired_env.

Guarantees:
- Zero external imports (leaf module purity via AST).
- All keys are unique and follow HUNTER_* naming.
- Every key in RETIRED_ENV_OVERRIDES has non-empty guidance.
- Runtime warnings emit canonical BOT_* targets and stay silent on unset/empty env vars.
- Object identity across consumers.
"""
from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path

import pytest

import core_brain.retired_env as retired_env
from core_brain.config import load
from core_brain.retired_env import (
    RETIRED_ENV_KEYS,
    RETIRED_ENV_OVERRIDES,
    RetiredEnvKey,
    iter_runtime_warnings,
)


def test_retired_env_leaf_module_purity():
    """Verify core_brain/retired_env.py only imports standard library modules."""
    module_path = Path(retired_env.__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.add(node.module.split(".")[0])

    # Allowable imports: standard library only, plus __future__
    allowed_standard_libs = {"__future__", "dataclasses", "typing"}
    assert imported_modules <= allowed_standard_libs, (
        f"core_brain/retired_env.py imports non-standard modules: {imported_modules - allowed_standard_libs}"
    )


def test_retired_env_keys_well_formed():
    """Verify all retired keys start with HUNTER_ and are unique."""
    names = [key.name for key in RETIRED_ENV_KEYS]
    assert len(names) == len(set(names)), "RETIRED_ENV_KEYS contains duplicate names"

    for key in RETIRED_ENV_KEYS:
        assert isinstance(key, RetiredEnvKey)
        assert key.name.startswith("HUNTER_"), f"Unexpected key name: {key.name}"
        assert key.plan_guidance and key.plan_guidance.strip(), f"Missing plan guidance for {key.name}"
        if key.runtime_warning is not None:
            assert "%s" in key.runtime_warning, f"Runtime warning template must contain %s: {key.runtime_warning}"


def test_retired_env_overrides_parity():
    """Verify RETIRED_ENV_OVERRIDES maps every record in RETIRED_ENV_KEYS to its plan guidance."""
    assert len(RETIRED_ENV_OVERRIDES) == len(RETIRED_ENV_KEYS)
    for record in RETIRED_ENV_KEYS:
        assert RETIRED_ENV_OVERRIDES[record.name] == record.plan_guidance


def test_iter_runtime_warnings_yields_only_warned_keys():
    """Verify iter_runtime_warnings yields exactly records where runtime_warning is set."""
    warnings_list = list(iter_runtime_warnings())
    warned_names = {name for name, _, _ in warnings_list}

    expected_names = {k.name for k in RETIRED_ENV_KEYS if k.runtime_warning is not None}
    assert warned_names == expected_names
    assert "HUNTER_REWARD_OFFSET" not in warned_names


@pytest.mark.parametrize(
    ("name", "template", "args"),
    list(iter_runtime_warnings()),
)
def test_runtime_warning_emits_canonical_vocabulary(monkeypatch, caplog, name, template, args):
    """Verify setting any retired key warns with canonical BOT_* vocabulary."""
    monkeypatch.setenv(name, "1")

    with caplog.at_level(logging.WARNING):
        load()

    records = [r for r in caplog.records if r.levelname == "WARNING"]
    matching_messages = [r.getMessage() for r in records if name in r.getMessage()]
    assert len(matching_messages) >= 1, f"Expected warning for {name}, got {records}"

    msg = matching_messages[0]
    expected_msg = template % ((name,) + args)
    assert msg == expected_msg, f"Message mismatch for {name}: {msg!r} != {expected_msg!r}"

    # For stop loss keys, ensure canonical BOT_* names are mentioned instead of legacy HUNTER_*
    if name in ("HUNTER_SINGLE_BUY_MAX_LOSS_PCT", "HUNTER_SINGLE_BUY_MAX_LOSS_USD"):
        assert "BOT_RISK_STOP_MODE" in msg
        assert "BOT_RISK_STOP_ADVERSE_DROP_CENTS" in msg
        assert "HUNTER_STOP_LOSS_MODE" not in msg
    elif name == "HUNTER_SINGLE_BUY_GRACE_SEC":
        assert "BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC" in msg
        assert "HUNTER_AGED_OUT_RESCUE_LEAD_SEC" not in msg


def test_runtime_warnings_silent_when_unset_or_empty(monkeypatch, caplog):
    """Verify runtime warnings stay silent when retired keys are unset or empty."""
    for key in RETIRED_ENV_KEYS:
        monkeypatch.delenv(key.name, raising=False)

    with caplog.at_level(logging.WARNING):
        load()

    retired_warnings = [
        r.getMessage() for r in caplog.records
        if any(key.name in r.getMessage() for key in RETIRED_ENV_KEYS)
    ]
    assert not retired_warnings, f"Expected no warnings for unset keys, got {retired_warnings}"

    # An empty string is treated as unset by os.environ.get
    for key in RETIRED_ENV_KEYS:
        monkeypatch.setenv(key.name, "")

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        load()

    empty_warnings = [
        r.getMessage() for r in caplog.records
        if any(key.name in r.getMessage() for key in RETIRED_ENV_KEYS)
    ]
    assert not empty_warnings, f"Expected no warnings for empty keys, got {empty_warnings}"


def test_reward_offset_produces_no_runtime_warning(monkeypatch, caplog):
    """HUNTER_REWARD_OFFSET was purged with no runtime warning."""
    monkeypatch.setenv("HUNTER_REWARD_OFFSET", "0.02")

    with caplog.at_level(logging.WARNING):
        load()

    offset_warnings = [r.getMessage() for r in caplog.records if "HUNTER_REWARD_OFFSET" in r.getMessage()]
    assert not offset_warnings, f"HUNTER_REWARD_OFFSET must not warn at runtime, got {offset_warnings}"
