"""The PowerShell menu reads the same live-state language as the dashboard.

DESIGN.md defines one vocabulary for both surfaces: RUNNING / DEGRADED / DOWN /
STOPPED / UNKNOWN, with a heartbeat-age ramp (amber at 3x a service's cadence,
red at 12x). `scripts/spread-hunter-menu.ps1` implements its half of that
contract in four PowerShell functions; this test drives them through pwsh and
asserts the mapping agrees with app.js's `stateKey` / `cadenceThresholds`.

Skipped where pwsh is not installed (same accommodation the Node harnesses
make for node).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
MENU = _ROOT / "scripts" / "spread-hunter-menu.ps1"

PWSH = shutil.which("pwsh")  # PowerShell 7+; 5.1 cannot parse this UTF-8 file
requires_pwsh = pytest.mark.skipif(PWSH is None,
                                   reason="pwsh (PowerShell 7) is not installed on this host")

# The harness: extracts the four live-state functions from the menu script and
# runs 25 assertions against them — cadence ramp arithmetic, the (running, age)
# → state mapping mirroring app.js, the color styles, and the glyphs.
HARNESS = r'''
$ErrorActionPreference = 'Stop'
$text = Get-Content '%MENU%' -Raw

$names = @('Get-CadenceThresholds','Get-LiveState','Get-StateStyle','Get-StateGlyph')
$snippets = foreach ($n in $names) {
    $start = $text.IndexOf("function $n ")
    if ($start -lt 0) { throw "function $n not found" }
    $i = $text.IndexOf('{', $start)
    $depth = 0
    for ($j = $i; $j -lt $text.Length; $j++) {
        if ($text[$j] -eq '{') { $depth++ }
        elseif ($text[$j] -eq '}') { $depth--; if ($depth -eq 0) { break } }
    }
    $text.Substring($start, $j - $start + 1)
}
Invoke-Expression ($snippets -join "`n")

$failures = New-Object System.Collections.Generic.List[string]
function Check($name, $actual, $expected) {
    if ("$actual" -ne "$expected") {
        $script:failures.Add(("{0}: got '{1}' want '{2}'" -f $name, $actual, $expected)) | Out-Null
    }
}

# Cadence ramp: 5s -> 15/60; 0.5s -> 2/6; 600s -> 1800/7200; garbage -> 15/60.
$t5  = Get-CadenceThresholds -CadenceSec 5
$t05 = Get-CadenceThresholds -CadenceSec 0.5
$t10 = Get-CadenceThresholds -CadenceSec 600
$tg  = Get-CadenceThresholds -CadenceSec "nonsense"
Check "th5.degraded"   $t5.Degraded 15
Check "th5.down"       $t5.Down 60
Check "th05.degraded"  $t05.Degraded 2
Check "th05.down"      $t05.Down 6
Check "th10.degraded"  $t10.Degraded 1800
Check "th10.down"      $t10.Down 7200
Check "thgar.degraded" $tg.Degraded 15
Check "thgar.down"     $tg.Down 60

# State mapping mirrors app.js stateKey.
Check "fresh"          (Get-LiveState -Running $true  -AgeSec 3     ) 'Running'
Check "degraded"       (Get-LiveState -Running $true  -AgeSec 20    ) 'Degraded'
Check "down"           (Get-LiveState -Running $true  -AgeSec 90    ) 'Down'
Check "no-age-running" (Get-LiveState -Running $true  -AgeSec $null ) 'Running'
Check "cold-stop"      (Get-LiveState -Running $false -AgeSec $null ) 'Stopped'
Check "dead-known-age" (Get-LiveState -Running $false -AgeSec 30    ) 'Down'
Check "custom-th"      (Get-LiveState -Running $true -AgeSec 4 -Thresholds @{Degraded=3;Down=9}) 'Degraded'

# The universe feed's 10-minute cadence ages on its own scale: 4000s is
# DEGRADED (past 1800, under 7200) and 8000s is DOWN — not the 5s defaults.
Check "feed-aging" (Get-LiveState -Running $true -AgeSec 4000 -Thresholds (Get-CadenceThresholds -CadenceSec 600)) 'Degraded'
Check "feed-stale" (Get-LiveState -Running $true -AgeSec 8000 -Thresholds (Get-CadenceThresholds -CadenceSec 600)) 'Down'

# Styles: alarms colored, a quiet stop neutral (not red) — same semantics as
# the dashboard's state-stopped pill.
Check "style-running"  (Get-StateStyle Running)  'Success'
Check "style-degraded" (Get-StateStyle Degraded) 'Warning'
Check "style-down"     (Get-StateStyle Down)     'Error'
Check "style-stopped"  (Get-StateStyle Stopped)  'Neutral'
Check "style-unknown"  (Get-StateStyle Unknown)  'Neutral'

# Glyphs: no emoji (DESIGN.md), stable static marks.
Check "glyph-running"  (Get-StateGlyph Running)  ([string][char]0x25CF)
Check "glyph-degraded" (Get-StateGlyph Degraded) ([string][char]0x25D0)
Check "glyph-down"     (Get-StateGlyph Down)     ([string][char]0x2715)
Check "glyph-stopped"  (Get-StateGlyph Stopped)  ([string][char]0x25CB)
Check "glyph-unknown"  (Get-StateGlyph Unknown)  '--'

if ($failures.Count) { $failures | ForEach-Object { Write-Host "FAIL $_" }; exit 1 }
Write-Host "ALL STATE CHECKS PASS"
'''


@requires_pwsh
def test_the_menu_state_mapping_matches_the_dashboard_vocabulary():
    # Arrange — pwsh reads the file as UTF-8 (5.1 would not).
    script = HARNESS.replace("%MENU%", str(MENU))

    # Act
    res = subprocess.run([PWSH, "-NoProfile", "-NonInteractive",
                          "-ExecutionPolicy", "Bypass", "-Command", script],
                         capture_output=True, text=True, encoding="utf-8", timeout=120)

    # Assert
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ALL STATE CHECKS PASS" in res.stdout


def test_the_status_view_wires_the_state_helpers_into_its_rows():
    # The helpers are useless if the status view never calls them: the stack
    # rows must receive each service's cadence and the guardrail row its
    # heartbeat age, or the terminal would still print bare ON/OFF.
    js = MENU.read_text(encoding="utf-8")
    assert "Write-ProcessRow -Label $r.Name -Running $r.Running -PidVal $r.Pid -Path $r.Path -RunCmd $r.RunCmd -CadenceSec $r.CadenceSec" in js
    assert "CadenceSec = @{ filter = 600; query = 0.5; decide = 5 }[$svc]" in js
    assert "-HeartbeatAgeSec $ghAge -CadenceSec 5" in js
    assert "Get-LiveState -Running" in js
    # An unknown age must stay null, not become 0s: [int]$null is 0, which
    # would render a fictional "· 0s" on a service that never reported.
    assert "if ($null -ne $gh.age_s) { [int]$gh.age_s } else { $null }" in js
    # The state cell is wider than the old 11-char ON/OFF column, or
    # "◐ Degraded · 42s" overflows and shifts every path column.
    assert '"{0,-22}" -f $statusWord' in js


def test_the_state_marks_carry_no_emoji():
    # DESIGN.md bans emoji on status surfaces; the terminal marks are static
    # geometric glyphs that render identically on every machine.
    js = MENU.read_text(encoding="utf-8")
    banned = ["⚡", "🎯", "🔒", "💎", "⚠", "🚀"]
    for glyph in banned:
        assert glyph not in js, f"emoji {glyph!r} found in menu status surfaces"
