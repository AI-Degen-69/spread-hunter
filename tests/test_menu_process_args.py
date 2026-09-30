"""Tests for subprocess argument quoting in scripts/spread-hunter-menu.ps1.

Verifies that Format-ProcessArgs exists and wraps arguments containing spaces
in quotes, preventing Python argparse from failing on paths with spaces.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

MENU = Path(__file__).resolve().parents[1] / "scripts" / "spread-hunter-menu.ps1"


def _menu_source() -> str:
    return MENU.read_text(encoding="utf-8")


def test_format_process_args_helper_defined_in_menu():
    """Format-ProcessArgs helper must be defined in spread-hunter-menu.ps1."""
    src = _menu_source()
    assert "function Format-ProcessArgs" in src, "Format-ProcessArgs helper must be defined"


def test_start_shadow_dashboard_uses_format_process_args():
    """Start-ShadowDashboard must pass its arguments through Format-ProcessArgs."""
    src = _menu_source()
    start_shadow_dash = src.split("function Start-ShadowDashboard {", 1)[1].split("\nfunction ", 1)[0]
    assert "Format-ProcessArgs" in start_shadow_dash, "Start-ShadowDashboard must format process args"


def test_resume_shadow_run_uses_format_process_args():
    """Resume-ShadowRun must format process args for shadow_run, observer, and guardrail."""
    src = _menu_source()
    resume_body = src.split("function Resume-ShadowRun {", 1)[1].split("\nfunction ", 1)[0]
    assert "Format-ProcessArgs $shadowArgs" in resume_body
    assert "Format-ProcessArgs" in resume_body


def test_format_process_args_pwsh_execution():
    """Format-ProcessArgs must quote space-containing items when evaluated by PowerShell."""
    src = _menu_source()
    assert "function Format-ProcessArgs {" in src
    func_body = src.split("function Format-ProcessArgs {", 1)[1].split("\n}\n", 1)[0]
    ps_cmd = (
        f"function Format-ProcessArgs {{\n{func_body}\n}}\n"
        "$argsList = @('-m', 'dashboard.server', '--db', 'C:\\Path With Spaces\\store.db', '--port', '8801'); "
        "$formatted = Format-ProcessArgs $argsList; "
        "$formatted | ConvertTo-Json"
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", ps_cmd],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    items = json.loads(result.stdout.strip())
    assert items[0] == "-m"
    assert items[1] == "dashboard.server"
    assert items[2] == "--db"
    assert items[3] == '"C:\\Path With Spaces\\store.db"'
    assert items[4] == "--port"
    assert items[5] == "8801"
