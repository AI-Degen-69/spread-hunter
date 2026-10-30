"""Checkout selection for the menu script (#881 T1).

The menu serves the checkout the operator is standing in: the exact current
folder wins when it holds both spread-hunter marker files, otherwise the
script's own checkout wins. No parent-folder walk. All worker and timebox
launches pin `-WorkingDirectory $ProjectPath`.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

MENU = Path(__file__).resolve().parents[1] / "scripts" / "spread-hunter-menu.ps1"

PWSH = shutil.which("pwsh") or shutil.which("powershell")

pytestmark = pytest.mark.skipif(PWSH is None,
                                reason="no PowerShell host on this machine")


def _lift(*names: str) -> str:
    text = MENU.read_text(encoding="utf-8")
    parts = []
    for name in names:
        match = re.search(r"^function %s \{.*?^\}" % re.escape(name), text,
                          re.MULTILINE | re.DOTALL)
        assert match, "%s is no longer defined in the menu script" % name
        parts.append(match.group(0))
    return "\n".join(parts)


def _resolve(current: Path, script_checkout: Path) -> dict:
    cur = str(current).replace("'", "''")
    sco = str(script_checkout).replace("'", "''")
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        _lift("Resolve-ProjectRoot"),
        f"$r = Resolve-ProjectRoot -CurrentPath '{cur}' -ScriptCheckout '{sco}'",
        "$out = @{ path = [string]$r.Path; source = [string]$r.Source }",
        "$out | ConvertTo-Json -Compress",
    ])
    out = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def _make_checkout(root: Path) -> Path:
    (root / "core_brain").mkdir(parents=True, exist_ok=True)
    (root / "dashboard").mkdir(parents=True, exist_ok=True)
    (root / "core_brain" / "order_manager.py").write_text("# marker", encoding="utf-8")
    (root / "dashboard" / "server.py").write_text("# marker", encoding="utf-8")
    return root


def test_current_folder_with_both_markers_is_selected(tmp_path):
    wt = _make_checkout(tmp_path / "wt")
    main = _make_checkout(tmp_path / "main")
    got = _resolve(wt, main)
    assert got["source"] == "current folder"
    assert Path(got["path"]) == wt


def test_folder_with_one_marker_falls_back(tmp_path):
    partial = tmp_path / "partial"
    (partial / "core_brain").mkdir(parents=True)
    (partial / "core_brain" / "order_manager.py").write_text("# marker", encoding="utf-8")
    main = _make_checkout(tmp_path / "main")
    got = _resolve(partial, main)
    assert got["source"] == "script location"
    assert Path(got["path"]) == main


def test_subfolder_falls_back_without_parent_walk(tmp_path):
    wt = _make_checkout(tmp_path / "wt")
    main = _make_checkout(tmp_path / "main")
    got = _resolve(wt / "scripts", main)
    assert got["source"] == "script location"
    assert Path(got["path"]) == main


def test_unrelated_folder_falls_back(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    main = _make_checkout(tmp_path / "main")
    got = _resolve(elsewhere, main)
    assert got["source"] == "script location"
    assert Path(got["path"]) == main


def test_path_with_spaces_and_trailing_separator_is_normalized(tmp_path):
    spaced = _make_checkout(tmp_path / "my checkout")
    main = _make_checkout(tmp_path / "main")
    trailing = str(spaced) + str(Path("/"))
    cur = trailing.replace("'", "''")
    sco = str(main).replace("'", "''")
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        _lift("Resolve-ProjectRoot"),
        f"$r = Resolve-ProjectRoot -CurrentPath '{cur}' -ScriptCheckout '{sco}'",
        "$out = @{ path = [string]$r.Path; source = [string]$r.Source }",
        "$out | ConvertTo-Json -Compress",
    ])
    out = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    got = json.loads(out.stdout)
    assert got["source"] == "current folder"
    assert got["path"] == str(spaced)
    assert not got["path"].endswith(("\\", "/"))


def test_timebox_launches_pin_working_directory():
    src = MENU.read_text(encoding="utf-8")
    launches = re.findall(r"Start-Process -FilePath \"powershell\"[\s\S]*?-WindowStyle Hidden[^\r\n]*",
                          src)
    assert len(launches) == 3
    for launch in launches:
        assert "-WorkingDirectory $ProjectPath" in launch


def test_psscriptroot_used_exactly_once():
    src = MENU.read_text(encoding="utf-8")
    assert len(re.findall(r"\$PSScriptRoot", src)) == 1
