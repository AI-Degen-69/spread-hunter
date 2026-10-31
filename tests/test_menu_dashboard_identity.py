"""Dashboard ownership verdicts for the menu script (#881 T2).

`Get-DashboardOwnership` is read-only: Owned only when the listener's db_path
is this checkout's data/orders.db, its registry_path sits under this
checkout's runtime/ or run/, and services.dash.pid owns the port. Everything
else is Foreign (with the served path) or Unknown — and `status` never writes
records while labeling the listener.
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


def _scenario(status: dict | None, port_pid: int, project: Path,
              throw: bool = False, run_stop: bool = False,
              procs_file: str = "") -> dict:
    proj = str(project).replace("'", "''")
    pf = procs_file.replace("'", "''")
    payload = "null" if status is None else json.dumps(status)
    invoked = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        _lift("Test-PathInCheckout", "Get-CommandLineCheckoutOwner",
              "Get-DashboardOwnership", "Stop-BotStack"),
        f"$ProjectPath = '{proj}'",
        "$Port = 8799",
        f"$ProcsFile = '{pf}'",
        "$script:MockStatusJson = @'",
        payload,
        "'@",
        f"$script:MockThrow = ${'true' if throw else 'false'}",
        f"$script:MockPortPid = {port_pid}",
        "$script:ControlCalls = @()",
        "function Invoke-RestMethod { if ($script:MockThrow) { throw 'no listener' }; "
        "if ($script:MockStatusJson -eq 'null') { return $null }; "
        "return ($script:MockStatusJson | ConvertFrom-Json) }",
        "function Get-PortPid { param([int]$PortNumber = 0) return $script:MockPortPid }",
        "function Test-Port { return $true }",
        "function Invoke-Control { param([string]$Endpoint) "
        "$script:ControlCalls += $Endpoint; return @{ ok = $true; message = 'stopped' } }",
        "function Lsh-Step($m) { }",
        "function Lsh-Ok($m) { }",
        "function Lsh-Fail($m) { }",
        "function Lsh-Warn($m) { }",
        "$o = Get-DashboardOwnership",
        "$out = @{ verdict = [string]$o.Verdict; served = [string]$o.ServedPath }",
        ("$null = Stop-BotStack; $out['controls'] = @($script:ControlCalls)" if run_stop else "$out['controls'] = @()"),
        "$out | ConvertTo-Json -Compress",
    ])
    out = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", invoked],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def _status(db_path: Path, reg_path: Path, pid: int | None = 1234,
            production: bool = False) -> dict:
    services: dict = {}
    if pid is not None:
        services = {"dash": {"pid": pid}}
    return {"db_path": str(db_path), "registry_path": str(reg_path),
            "services": services, "db_is_production": production}


def _make_checkout(root: Path) -> Path:
    (root / "core_brain").mkdir(parents=True, exist_ok=True)
    (root / "dashboard").mkdir(parents=True, exist_ok=True)
    (root / "core_brain" / "order_manager.py").write_text("# marker", encoding="utf-8")
    (root / "dashboard" / "server.py").write_text("# marker", encoding="utf-8")
    return root


def test_matching_dashboard_is_adoptable(tmp_path):
    main = _make_checkout(tmp_path / "main")
    st = _status(main / "data" / "orders.db", main / "runtime" / "processes.json", 1234)
    got = _scenario(st, 1234, main)
    assert got["verdict"] == "Owned"


def test_sibling_dashboard_with_production_flag_is_refused(tmp_path):
    main = _make_checkout(tmp_path / "main")
    sib = _make_checkout(tmp_path / "sib")
    st = _status(sib / "data" / "orders.db", sib / "runtime" / "processes.json",
                 1234, production=True)
    got = _scenario(st, 1234, main)
    assert got["verdict"] == "Foreign"
    assert str(sib) in got["served"]


def test_pid_mismatch_is_unknown(tmp_path):
    main = _make_checkout(tmp_path / "main")
    st = _status(main / "data" / "orders.db", main / "runtime" / "processes.json", 1234)
    got = _scenario(st, 9999, main)
    assert got["verdict"] == "Unknown"


def test_db_override_with_local_registry_is_not_owned(tmp_path):
    main = _make_checkout(tmp_path / "main")
    st = _status(tmp_path / "custom.db", main / "runtime" / "processes.json", 1234)
    got = _scenario(st, 1234, main)
    assert got["verdict"] == "Foreign"


def test_status_request_failure_is_unknown(tmp_path):
    main = _make_checkout(tmp_path / "main")
    got = _scenario(None, 1234, main, throw=True)
    assert got["verdict"] == "Unknown"


def test_missing_fields_are_unknown(tmp_path):
    main = _make_checkout(tmp_path / "main")
    got = _scenario({"db_path": str(main / "data" / "orders.db")}, 1234, main)
    assert got["verdict"] == "Unknown"


def test_stop_bot_stack_skips_foreign_listener(tmp_path):
    main = _make_checkout(tmp_path / "main")
    sib = _make_checkout(tmp_path / "sib")
    st = _status(sib / "data" / "orders.db", sib / "runtime" / "processes.json", 1234)
    got = _scenario(st, 1234, main, run_stop=True,
                    procs_file=str(tmp_path / "no-such-registry.json"))
    assert got["verdict"] == "Foreign"
    assert got["controls"] == []


def test_stop_bot_stack_controls_owned_listener(tmp_path):
    main = _make_checkout(tmp_path / "main")
    st = _status(main / "data" / "orders.db", main / "runtime" / "processes.json", 1234)
    got = _scenario(st, 1234, main, run_stop=True,
                    procs_file=str(tmp_path / "no-such-registry.json"))
    assert got["verdict"] == "Owned"
    assert got["controls"] == ["/api/system/stop"]


def test_status_labels_cover_all_verdicts_and_write_nothing():
    src = MENU.read_text(encoding="utf-8")
    body = src.split("function Show-Status {", 1)[1].split("\nfunction ", 1)[0]
    assert "ON (owned)" in body
    assert "this checkout, not adopted - use host/open to adopt" in body
    assert "other checkout:" in body
    assert "unknown owner" in body
    assert "Save-DashInstance" not in body
