"""Shadow dashboards get run-id-derived ports (#288).

The menu hardcoded the shadow dashboard to :8799 -- the live port -- while a
second hand-launched dashboard sat on :8801 with no menu support. Ports now
derive from the run id (shadow-01 -> 8801, shadow-02 -> 8802), so parallel
instances never fight over one port and the number tells you who is answering.

Journeys under test:
1. As the operator, shadow-01/-02/-03 dashboards land on 8801/8802/8803.
2. As the operator, the unnumbered "shadow-resume" fallback id gets :8900 --
   outside every numbered instance (01-99 land on 8801-8899), never the live
   :8799.
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
    assert _invoke("shadow-99", "Get-ShadowDashPort $RunId", tmp_path)["value"] == "8899"


def test_unnumbered_resume_id_gets_its_own_port_off_the_live_one(tmp_path):
    result = _invoke("shadow-resume", "Get-ShadowDashPort $RunId", tmp_path)

    assert result["threw"] is False, result["message"]
    assert result["value"] == "8900"


def test_garbage_run_id_fails_loudly_instead_of_landing_on_live(tmp_path):
    for bad in ("", "live", "shadow-", "shadow-1x", "shadow-1", "shadow-00", "shadow-100"):
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
    _roundtrip("shadow-02", tmp_path)

    # Act — read both records without rewriting either.
    one = json.loads((tmp_path / "shadow-dash-shadow-01.pids.json").read_text(encoding="utf-8-sig"))
    two = json.loads((tmp_path / "shadow-dash-shadow-02.pids.json").read_text(encoding="utf-8-sig"))

    # Assert — saving shadow-02 left shadow-01's record intact.
    assert one["dash"]["port"] == 8801
    assert two["dash"]["port"] == 8802


def test_url_and_pidfile_derive_from_the_same_port(tmp_path):
    url = _invoke("shadow-02", "Get-ShadowDashUrl $RunId", tmp_path)
    pidfile = _invoke("shadow-02", "Get-ShadowDashPidFile $RunId", tmp_path)

    assert url["threw"] is False, url["message"]
    assert url["value"] == "http://127.0.0.1:8802"
    assert pidfile["threw"] is False, pidfile["message"]
    assert pidfile["value"].endswith("shadow-dash-shadow-02.pids.json")


def _session_file(run_id_expr: str, tmp_path: Path) -> dict:
    run_dir = str(tmp_path).replace("'", "''")
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        _lift("Get-ShadowSessionFile"),
        f"$RunDir = '{run_dir}'",
        f"$ShadowSessionFile = '{run_dir}/shadow-session.json'",
        "try {",
        f"  $v = Get-ShadowSessionFile {run_id_expr}",
        "  $out = @{ threw = $false; value = [string]$v; message = $null }",
        "} catch {",
        "  $out = @{ threw = $true; value = $null; message = $_.Exception.Message }",
        "}",
        "$out | ConvertTo-Json -Compress",
    ])
    out = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def test_session_files_are_per_instance_with_a_legacy_fallback(tmp_path):
    first = _session_file("-RunId 'shadow-01'", tmp_path)
    second = _session_file("-RunId 'shadow-02'", tmp_path)
    legacy = _session_file("", tmp_path)

    assert first["value"].endswith("shadow-session-shadow-01.json"), first
    assert second["value"].endswith("shadow-session-shadow-02.json"), second
    assert legacy["value"].endswith("shadow-session.json"), legacy
    assert first["value"] != second["value"]


def test_ts_bridge_proxies_an_explicit_dashboard_url():
    # Arrange — static pin: the bridge must take the instance URL as a
    # parameter instead of reading the hardcoded :8799 global, or the
    # bridged view silently shows the wrong instance (or the live stack).
    text = MENU.read_text(encoding="utf-8")
    match = re.search(r"^function Start-TsBridge \{.*?^\}", text,
                      re.MULTILINE | re.DOTALL)
    assert match, "Start-TsBridge is no longer defined in the menu script"
    body = match.group(0)

    # Act / Assert
    assert "PyDashUrl" in body
    assert "$ShadowDashUrl" not in body


def _stop_one_instance(tmp_path: Path) -> dict:
    """Two fabricated single-file sessions, stop only shadow-01's.

    PIDs are dead, so the stubbed killer only records calls -- nothing can
    actually die. Returns the kill log plus which session files survived.
    """
    run_dir = str(tmp_path).replace("'", "''")
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        _lift("Stop-ShadowSession", "Get-ShadowSessionFile"),
        f"$RunDir = '{run_dir}'",
        f"$ShadowSessionFile = '{run_dir}/shadow-session.json'",
        "$killed = @()",
        "function Kill-RecordedPid { param($Name, $TargetPid, $StartedTicks)",
        "  $script:killed += [int]$TargetPid; return $true }",
        "function Stop-TsBridge { return $false }",
        "function Stop-ShadowDashboard { return $false }",
        "function Lsh-Step([string]$t) {}",
        "function Lsh-Ok([string]$t) {}",
        "function Lsh-Warn([string]$t) {}",
        "function Lsh-Fail([string]$t) {}",
        "@{ run_id = 'shadow-01'; screener = @{ pid = 111 };",
        "   loop = @{ pid = 222 }; observer = $null; watcher = $null } |",
        "  ConvertTo-Json -Depth 4 |",
        f"  Set-Content -Path '{run_dir}/shadow-session-shadow-01.json' -Encoding UTF8",
        "@{ run_id = 'shadow-02'; screener = $null;",
        "   loop = @{ pid = 333 }; observer = $null; watcher = $null } |",
        "  ConvertTo-Json -Depth 4 |",
        f"  Set-Content -Path '{run_dir}/shadow-session-shadow-02.json' -Encoding UTF8",
        "try {",
        "  $r = Stop-ShadowSession -RunId 'shadow-01'",
        "  $out = @{ threw = $false; killed = @($script:killed);",
        "            kept02 = (Test-Path '" + run_dir + "/shadow-session-shadow-02.json');",
        "            kept01 = (Test-Path '" + run_dir + "/shadow-session-shadow-01.json') }",
        "} catch {",
        "  $out = @{ threw = $true; message = $_.Exception.Message }",
        "}",
        "$out | ConvertTo-Json -Compress",
    ])
    out = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def test_resume_stop_kills_only_its_own_instances_pieces(tmp_path):
    # Arrange / Act — resuming 01 beside a live 02 must not touch 02.
    result = _stop_one_instance(tmp_path)

    # Assert
    assert result["threw"] is False, result.get("message")
    assert sorted(result["killed"]) == [111, 222]
    assert result["kept02"] is True
    assert result["kept01"] is False


def test_instance_enumeration_lists_every_record_file(tmp_path):
    # Arrange — two stale records (dead PIDs read back as not-alive
    # placeholders, which is exactly what the stop path prunes).
    run_dir = str(tmp_path).replace("'", "''")
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        _lift("Get-ShadowDashInstances", "_ReadShadowDashRecord",
              "Get-ShadowDashPidFile", "Get-ShadowDashPort"),
        f"$RunDir = '{run_dir}'",
        f"$ShadowPidFile = '{run_dir}/shadow-dash.pids.json'",
        "@{ dash = @{ pid = 111; started_ticks = 1; port = 8801; db = 'a' } } |",
        "  ConvertTo-Json -Depth 4 |",
        f"  Set-Content -Path '{run_dir}/shadow-dash-shadow-01.pids.json' -Encoding UTF8",
        "@{ dash = @{ pid = 222; started_ticks = 1; port = 8802; db = 'b' } } |",
        "  ConvertTo-Json -Depth 4 |",
        f"  Set-Content -Path '{run_dir}/shadow-dash-shadow-02.pids.json' -Encoding UTF8",
        "try {",
        "  $all = @(Get-ShadowDashInstances)",
        "  $out = @{ threw = $false;",
        "            ids = @($all | ForEach-Object { [string]$_.run_id }) -join ',';",
        "            ports = @($all | ForEach-Object { [string]$_.port }) -join ',' }",
        "} catch {",
        "  $out = @{ threw = $true; message = $_.Exception.Message }",
        "}",
        "$out | ConvertTo-Json -Compress",
    ])
    out = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    result = json.loads(out.stdout)

    # Assert — both files discovered, each carrying its own identity
    # (discovery order is filesystem order; the contract is the set).
    assert result["threw"] is False, result.get("message")
    assert sorted(result["ids"].split(",")) == ["shadow-01", "shadow-02"], result
    assert sorted(result["ports"].split(",")) == ["8801", "8802"], result
