"""Checkout ownership classifiers for the menu script (#881 T2).

`Test-PathInCheckout` proves a path belongs to a checkout without claiming
nested checkouts; `Get-CommandLineCheckoutOwner` reduces a Win32 command line
to Owned / Foreign / Unknown. Unknown and Foreign processes keep running.
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


def _owner(command_line: str, root: Path) -> str:
    cl = command_line.replace("'", "''")
    rt = str(root).replace("'", "''")
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        _lift("Test-PathInCheckout", "Get-CommandLineCheckoutOwner"),
        f"$v = Get-CommandLineCheckoutOwner -CommandLine '{cl}' -Root '{rt}'",
        "$out = @{ verdict = [string]$v }",
        "$out | ConvertTo-Json -Compress",
    ])
    out = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)["verdict"]


def _in_checkout(path: str, root: Path) -> bool:
    p = path.replace("'", "''")
    rt = str(root).replace("'", "''")
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        _lift("Test-PathInCheckout"),
        f"$v = Test-PathInCheckout -Path '{p}' -Root '{rt}'",
        "$out = @{ owned = [bool]$v }",
        "$out | ConvertTo-Json -Compress",
    ])
    out = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return bool(json.loads(out.stdout)["owned"])


def _make_checkout(root: Path) -> Path:
    (root / "core_brain").mkdir(parents=True, exist_ok=True)
    (root / "dashboard").mkdir(parents=True, exist_ok=True)
    (root / "core_brain" / "order_manager.py").write_text("# marker", encoding="utf-8")
    (root / "dashboard" / "server.py").write_text("# marker", encoding="utf-8")
    return root


def test_path_inside_checkout_is_owned(tmp_path):
    main = _make_checkout(tmp_path / "main")
    assert _in_checkout(str(main / "data" / "orders.db"), main) is True


def test_prefix_sibling_is_not_owned(tmp_path):
    main = _make_checkout(tmp_path / "main")
    _make_checkout(tmp_path / "main2")
    assert _in_checkout(str(tmp_path / "main2" / "data" / "orders.db"), main) is False


def test_case_and_slash_differences_are_normalized(tmp_path):
    main = _make_checkout(tmp_path / "main")
    raw = str(main / "data" / "orders.db")
    mangled = raw.swapcase().replace("\\", "/")
    assert _in_checkout(mangled, main) is True
    dotted = str(main / "data" / ".." / "data" / "orders.db")
    assert _in_checkout(dotted, main) is True


def test_nested_worktree_belongs_only_to_itself(tmp_path):
    main = _make_checkout(tmp_path / "main")
    wt = _make_checkout(tmp_path / "main" / "wt")
    assert _in_checkout(str(wt / "data" / "orders.db"), main) is False
    assert _in_checkout(str(wt / "data" / "orders.db"), wt) is True
    assert _in_checkout(str(main / "data" / "orders.db"), wt) is False


def test_relative_command_line_is_unknown(tmp_path):
    main = _make_checkout(tmp_path / "main")
    assert _owner("python -m dashboard.server --port 8799", main) == "Unknown"
    assert _owner("python -m core_brain.shadow_run --db relative.db", main) == "Unknown"


def test_absolute_args_decide_owner(tmp_path):
    main = _make_checkout(tmp_path / "main")
    other = _make_checkout(tmp_path / "other")
    db = main / "data" / "s.db"
    cmd = f'"C:\\Python\\python.exe" -m core_brain.shadow_run --db "{db}"'
    if "/" in str(db):
        cmd = f'"/usr/bin/python3" -m core_brain.shadow_run --db "{db}"'
    assert _owner(cmd, main) == "Owned"
    assert _owner(cmd, other) == "Foreign"


def test_mixed_paths_are_unknown(tmp_path):
    main = _make_checkout(tmp_path / "main")
    other = _make_checkout(tmp_path / "other")
    cmd = f'python shadow.py --watch "{other / "x.db"}" --data-dir "{main / "data"}"'
    assert _owner(cmd, main) == "Unknown"


def test_interpreter_path_is_ignored(tmp_path):
    main = _make_checkout(tmp_path / "main")
    interp = main / ".venv" / "python.exe"
    assert _owner(f"{interp} -m scripts.filter_loop", main) == "Unknown"


def test_absolute_script_path_is_owned(tmp_path):
    main = _make_checkout(tmp_path / "main")
    script = main / "scripts" / "global_stop_loss.py"
    assert _owner(f"python {script} --interval 5", main) == "Owned"
