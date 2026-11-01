"""Option 8 (Check System Status) survives a session file with missing slots.

`Show-Status` walks the four shadow-run slots (screener, loop, observer,
watcher) and asks `Get-ProcessRecord` about each one. A session recorded
before the stop-loss watcher started has no `watcher` key, so the caller
hands `Get-ProcessRecord` a `$null` entry. With `[Parameter(Mandatory)]`
and no `[AllowNull()]`, PowerShell refuses the bind and the whole status
page dies mid-render:

    Cannot bind argument to parameter 'Entry' because it is null.

The function body already returns `$null` for a null entry, so the guard
was the parameter attribute, not the logic.

Journeys under test:
1. As the operator, a slot the session never recorded reads as "not running"
   instead of killing the status page.
2. As the operator, a slot with no pid reads as "not running".
3. As the operator, a slot naming a pid that is not on this host reads as
   "not running" rather than throwing.
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


def _get_process_record_source() -> str:
    """Lift just the Get-ProcessRecord definition out of the menu script.

    The menu script starts a live control centre when it is dot-sourced, so
    the test runs the one function under test on its own.
    """
    text = MENU.read_text(encoding="utf-8")
    match = re.search(r"^function Get-ProcessRecord \{.*?^\}", text,
                      re.MULTILINE | re.DOTALL)
    assert match, "Get-ProcessRecord is no longer defined in the menu script"
    return match.group(0)


def _record(entry_json: str | None) -> dict:
    """Call Get-ProcessRecord with `entry_json` and report what came back."""
    build = "$e = $null" if entry_json is None else (
        f"$e = '{entry_json}' | ConvertFrom-Json")
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        _get_process_record_source(),
        build,
        "try {",
        "  $r = Get-ProcessRecord -Entry $e",
        "  $out = @{ threw = $false; null = ($null -eq $r) }",
        "} catch {",
        "  $out = @{ threw = $true; null = $false; message = $_.Exception.Message }",
        "}",
        "$out | ConvertTo-Json -Compress",
    ])
    out = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def test_missing_session_slot_reads_as_not_running():
    result = _record(None)

    assert result["threw"] is False, result.get("message")
    assert result["null"] is True


def test_slot_without_a_pid_reads_as_not_running():
    result = _record(json.dumps({"started_ticks": 1}))

    assert result["threw"] is False, result.get("message")
    assert result["null"] is True


def test_slot_naming_a_dead_pid_reads_as_not_running():
    # 999999 is above the Windows pid range in practice; if it ever does
    # resolve, the started_ticks comparison rejects it anyway.
    result = _record(json.dumps({"pid": 999999, "started_ticks": 1}))

    assert result["threw"] is False, result.get("message")
    assert result["null"] is True


def test_get_active_shadow_runs_parses_status_payload():
    """Get-ActiveShadowRuns discovers tournament arms from dashboard status payload."""
    status_payload = {
        "shadow_runs": [
            {
                "run_id": "shadow-905-t01-queue-clear-5m-2026-11-01T13-34",
                "db_path": "data/tournaments/i905/queue-clear-5m.db",
                "running": True,
                "pid": 11444,
                "heartbeat_age_sec": 1.2,
                "cycle": 15,
                "tournament": {"issue": 905, "arm": "queue-clear-5m"},
            },
            {
                "run_id": "shadow-905-t02-balanced-mid-2026-11-01T13-34",
                "db_path": "data/tournaments/i905/balanced-mid.db",
                "running": True,
                "pid": 31116,
                "heartbeat_age_sec": 2.0,
                "cycle": 12,
                "tournament": {"issue": 905, "arm": "balanced-mid"},
            },
        ]
    }
    src = MENU.read_text(encoding="utf-8")
    m = re.search(r"function Get-ActiveShadowRuns \{.*?^\}", src, re.MULTILINE | re.DOTALL)
    assert m, "Get-ActiveShadowRuns definition must exist"
    fn_def = m.group(0)

    payload_json = json.dumps(status_payload).replace('"', '`"')
    pwsh_script = f"""
$ErrorActionPreference = 'Stop'
function Test-PidAlive {{ param([int]$ProcessId) return $true }}
{fn_def}
$status = '{json.dumps(status_payload)}' | ConvertFrom-Json
$runs = Get-ActiveShadowRuns -DashStatus $status
$out = @()
foreach ($r in $runs) {{
    $out += @{{
        RunId     = $r.RunId
        TourneyId = $r.TourneyId
        ArmTag    = $r.ArmTag
        State     = $r.State
    }}
}}
$out | ConvertTo-Json -Compress
"""
    res = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", pwsh_script],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    parsed = json.loads(res.stdout)
    assert len(parsed) == 2
    assert parsed[0]["TourneyId"] == "905"
    assert parsed[0]["ArmTag"] == "queue-clear-5m"
    assert parsed[0]["State"] == "Running"
    assert parsed[1]["TourneyId"] == "905"
    assert parsed[1]["ArmTag"] == "balanced-mid"
