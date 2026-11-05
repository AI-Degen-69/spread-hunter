"""Tests for Tournament Reconcile arm selection menu and option 'c' removal in spread-hunter-menu.ps1."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
import pytest

MENU = Path(__file__).resolve().parents[1] / "scripts" / "spread-hunter-menu.ps1"
PWSH = shutil.which("pwsh")
requires_pwsh = pytest.mark.skipif(PWSH is None, reason="pwsh (PowerShell 7) required")


def _menu_source() -> str:
    return MENU.read_text(encoding="utf-8")


def test_option_c_removed_from_menu_keys_and_groups():
    """Option 'c' must not exist in $script:MenuKeys or $groups."""
    src = _menu_source()
    # Check MenuKeys
    m = re.search(r"\$script:MenuKeys\s*=\s*@\(([^)]*)\)", src)
    assert m, "Must define $script:MenuKeys"
    keys = re.findall(r"'([^']+)'", m.group(1))
    assert "c" not in keys

    # Check $groups
    groups_match = re.search(r"\$groups\s*=\s*@\((.*?)\n\s*\)", src, re.DOTALL)
    assert groups_match, "Must define $groups"
    assert '"c"' not in groups_match.group(1)
    assert "'c'" not in groups_match.group(1)

    # Check header usage
    header = src.split("# Usage:", 1)[1].split("# the same code path", 1)[0]
    assert "cockpit" not in header
    assert "tournament" in header


def test_tournament_reconcile_option_t_present():
    """Option 't' must exist and describe tournament selection/reconcile."""
    src = _menu_source()
    assert '"t"' in src
    assert "Invoke-TournamentReconcileAction" in src
    assert "Get-TournamentConfigs" in src


@requires_pwsh
def test_get_tournament_configs_discovers_configured_arms():
    """Get-TournamentConfigs must discover the 8 configured tournament files."""
    pwsh_cmd = """
$ProjectPath = (Get-Location).Path
$src = Get-Content -Raw -LiteralPath 'scripts/spread-hunter-menu.ps1'
$fnDef = $src.Substring($src.IndexOf('function Get-TournamentConfigs {'))
$fnDef = $fnDef.Substring(0, $fnDef.IndexOf('function Invoke-TournamentReconcileAction {'))
Invoke-Expression $fnDef

$configs = Get-TournamentConfigs
if ($configs.Count -ne 8) {
    Write-Error ("Expected 8 configs, got " + $configs.Count)
    exit 1
}
$issues = ($configs | ForEach-Object { $_.Issue }) -join ','
if ($issues -ne '799,843,905,917,923,929,938,940') {
    Write-Error ("Expected issues 799,843,905,917,923,929,938,940, got " + $issues)
    exit 1
}
$armCounts = ($configs | ForEach-Object { $_.ArmCount }) -join ','
if ($armCounts -ne '10,4,5,6,2,6,10,10') {
    Write-Error ("Expected arm counts 10,4,5,6,2,6,10,10, got " + $armCounts)
    exit 1
}
Write-Host 'DISCOVERY_SUCCESS'
"""
    res = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", pwsh_cmd],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "DISCOVERY_SUCCESS" in res.stdout
