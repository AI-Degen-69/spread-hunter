"""Tests for Option 7: Global Stop &| Clean in spread-hunter-menu.ps1."""
from __future__ import annotations

import re
from pathlib import Path
import pytest

MENU = Path(__file__).resolve().parents[1] / "scripts" / "spread-hunter-menu.ps1"


def _menu_source() -> str:
    return MENU.read_text(encoding="utf-8")


def test_menu_label_and_description():
    src = _menu_source()
    assert 'V = "Global Stop &| Clean"' in src
    assert '1. Stop All (default) | 2. Stop & CLEAN Data' in src


def test_action_map_routes_stop_all():
    src = _menu_source()
    body = src.split("$actionMap = @{", 1)[1].split("}", 1)[0]
    pairs = dict(re.findall(r'"([^"]+)"\s*=\s*"([^"]+)"', body))
    assert pairs.get("stop-all") == "7"
    assert pairs.get("stopall") == "7"
    assert pairs.get("clean") == "7"
    assert pairs.get("reset") == "7"


def test_stop_all_function_defined():
    src = _menu_source()
    assert "function Stop-AllSpreadHunterProcesses {" in src
    # Must stop recorded services
    assert "Stop-BotStack" in src
    assert "Stop-ShadowSession" in src
    assert "Stop-ShadowRun" in src
    assert "Stop-Dashboard" in src
    assert "Stop-ShadowDashboard" in src
    # Must preserve data in Stop-All
    assert "Never touches any data files or databases!" in src


def test_option_7_dispatch_branches():
    src = _menu_source()
    dispatch = src.split('function Invoke-LiveAction {', 1)[1].split('\nfunction ', 1)[0]
    branch_7 = dispatch.split('"7" {', 1)[1].split('"8" {', 1)[0]

    # Displays dual choice with default 1 and red option 2
    assert "GLOBAL STOP &| CLEAN" in branch_7
    assert "Stop All" in branch_7
    assert "Stop & CLEAN Data" in branch_7
    assert "default 1" in branch_7
    assert "Stop-AllSpreadHunterProcesses" in branch_7
    assert 'Reset-Environment -Mode "none"' in branch_7


def test_clear_runtime_state_preserves_presets_and_configs():
    src = _menu_source()
    clear_func = src.split('function Clear-RuntimeState {', 1)[1].split('\nfunction ', 1)[0]
    # Preset files, configs, and parameters must never be removed
    removal_lines = [line for line in clear_func.splitlines() if "Remove-Item" in line]
    for line in removal_lines:
        assert "preset" not in line.lower()
        assert ".env" not in line.lower()

    assert "data/orders.db untouched" in clear_func
    # Tournament run directories and runtime logs are cleaned
    assert "data/tournaments" in clear_func
    assert "runtime/logs" in clear_func
