"""Shadow dashboards get run-id-derived ports (#288).

The menu hardcoded the shadow dashboard to :8799 -- the live port -- while a
second hand-launched dashboard sat on :8801 with no menu support. Ports now
derive from the run id (shadow-01 -> 8801, shadow-02 -> 8802), so parallel
instances never fight over one port and the number tells you who is answering.

Journeys under test:
1. As the operator, shadow-01/-02/-03 dashboards land on 8801/8802/8803.
2. As the operator, the unnumbered "shadow-resume" fallback id gets :8899 --
   outside any numbered instance, never the live :8799.
3. As the operator, a garbage run id fails loudly instead of silently
   landing back on :8799 beside the live stack.
4. As the operator, the dashboard URL and PID-file path are built from the
   same derivation, so status/stop/open-browser cannot disagree about where
   an instance lives.
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
    """Lift named function definitions out of the menu script.

    The menu script starts a live control centre when it is dot-sourced, so
    each test runs the functions under test on their own.
    """
    text = MENU.read_text(encoding="utf-8")
    parts = []
    for name in names:
        match = re.search(r"^function %s \{.*?^\}" % re.escape(name), text,
                          re.MULTILINE | re.DOTALL)
        assert match, "%s is no longer defined in the menu script" % name
        parts.append(match.group(0))
    return "\n".join(parts)


def _invoke(run_id: str, expression: str, tmp_path: Path) -> dict:
    run_dir = str(tmp_path).replace("'", "''")
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        _lift("Get-ShadowDashPort", "Get-ShadowDashUrl",
              "Get-ShadowDashPidFile"),
        f"$RunDir = '{run_dir}'",
        f"$RunId = '{run_id}'",
        "try {",
        f"  $v = {expression}",
        "  $out = @{ threw = $false; value = [string]$v; message = $null }",
        "} catch {",
        "  $out = @{ threw = $true; value = $null; message = $_.Exception.Message }",
        "}",
        "$out | ConvertTo-Json -Compress",
    ])
    out = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def test_numbered_instances_derive_their_port(tmp_path):
    assert _invoke("shadow-01", "Get-ShadowDashPort $RunId", tmp_path)["value"] == "8801"
    assert _invoke("shadow-02", "Get-ShadowDashPort $RunId", tmp_path)["value"] == "8802"
    assert _invoke("shadow-03", "Get-ShadowDashPort $RunId", tmp_path)["value"] == "8803"


def test_unnumbered_resume_id_gets_its_own_port_off_the_live_one(tmp_path):
    result = _invoke("shadow-resume", "Get-ShadowDashPort $RunId", tmp_path)

    assert result["threw"] is False, result["message"]
    assert result["value"] == "8899"


def test_garbage_run_id_fails_loudly_instead_of_landing_on_live(tmp_path):
    for bad in ("", "live", "shadow-", "shadow-1x"):
        result = _invoke(bad, "Get-ShadowDashPort $RunId", tmp_path)

        assert result["threw"] is True, bad
        assert "8799" not in (result["message"] or ""), bad


def _roundtrip(run_id: str, tmp_path: Path) -> dict:
    """Save a fake-but-live process record, read it back as an instance."""
    run_dir = str(tmp_path).replace("'", "''")
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        _lift("Get-ShadowDashPort", "Get-ShadowDashPidFile",
              "Save-ShadowDashInstance", "Get-ShadowDashInstance",
              "_ReadShadowDashRecord"),
        f"$RunDir = '{run_dir}'",
        f"$RunId = '{run_id}'",
        "$self = Get-Process -Id $PID",
        "$fake = [pscustomobject]@{ Id = $self.Id; HasExited = $false; StartTime = $self.StartTime }",
        "try {",
        "  Save-ShadowDashInstance -DashProcess $fake -RunId $RunId -Port (Get-ShadowDashPort $RunId)",
        "  $inst = Get-ShadowDashInstance -RunId $RunId",
        "  $out = @{ threw = $false; pid = [string]$inst.pid; port = [string]$inst.port; message = $null }",
        "} catch {",
        "  $out = @{ threw = $true; pid = $null; port = $null; message = $_.Exception.Message }",
        "}",
        "$out | ConvertTo-Json -Compress",
    ])
    out = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def test_instance_record_roundtrips_with_its_own_port(tmp_path):
    # Arrange / Act — a stand-in for a dashboard the menu launched itself.
    result = _roundtrip("shadow-02", tmp_path)

    # Assert — readable back under its own run id, carrying its own port.
    assert result["threw"] is False, result["message"]
    assert result["port"] == "8802"
    assert result["pid"] is not None


def test_instance_records_do_not_share_one_file(tmp_path):
    # Arrange — two instances recorded side by side.
    _roundtrip("shadow-01", tmp_path)
    result = _roundtrip("shadow-02", tmp_path)

    # Act
    first = _roundtrip("shadow-01", tmp_path)

    # Assert — each run id still reads its own port back.
    assert result["port"] == "8802"
    assert first["port"] == "8801"


def test_url_and_pidfile_derive_from_the_same_port(tmp_path):
    url = _invoke("shadow-02", "Get-ShadowDashUrl $RunId", tmp_path)
    pidfile = _invoke("shadow-02", "Get-ShadowDashPidFile $RunId", tmp_path)

    assert url["threw"] is False, url["message"]
    assert url["value"] == "http://127.0.0.1:8802"
    assert pidfile["threw"] is False, pidfile["message"]
    assert pidfile["value"].endswith("shadow-dash-shadow-02.pids.json")
