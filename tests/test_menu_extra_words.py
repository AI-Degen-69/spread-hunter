"""Tests for friendly errors when the menu gets more words than one action.

`scripts/spread-hunter-menu.ps1` accepts a single action per call
(e.g. `audit`). Extra words such as `audit and` must fail with the menu's
own guidance — never with a raw PowerShell parameter-binding error.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

MENU = Path(__file__).resolve().parents[1] / "scripts" / "spread-hunter-menu.ps1"

RAW_BINDING_MARKERS = (
    "A parameter cannot be found",
    "A positional parameter cannot be found",
)


def _run_menu(*words: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(MENU), *words],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def test_extra_words_print_friendly_error():
    """`audit and` must print menu guidance, not a binding error."""
    result = _run_menu("audit", "and")
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "one action at a time" in combined
    for marker in RAW_BINDING_MARKERS:
        assert marker not in combined


def test_dash_word_reports_friendly_error():
    """`resume -and prudent` (the reported shape) must print menu guidance."""
    result = _run_menu("resume", "-and", "prudent")
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "one action at a time" in combined
    for marker in RAW_BINDING_MARKERS:
        assert marker not in combined


def test_single_unknown_word_keeps_existing_guidance():
    """A single unknown word must still hit the unknown-action guidance."""
    result = _run_menu("and")
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Unknown action" in combined
