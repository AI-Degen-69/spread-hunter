# Spread Hunter Control Center Interactive Menu
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Action = ""
)

$ErrorActionPreference = "Stop"
$ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectPath

. (Join-Path $PSScriptRoot "theme-loader.ps1")
. (Join-Path $PSScriptRoot "hunter-procs.ps1")
. (Join-Path $PSScriptRoot "bankroll-procs.ps1")

$MenuActions = [ordered]@{
    "1"  = @{ Name = "hunter-start";        Label = "Start Spread Hunter";           Desc = "Launch supervisor, maker engine & dashboards (:8800/:8801)" }
    "2"  = @{ Name = "hunter-start-fresh";  Label = "Start Spread Hunter (Fresh)";   Desc = "Archive active DB (fleet.db) and start clean sample" }
    "3"  = @{ Name = "hunter-stop";         Label = "Stop Spread Hunter";            Desc = "Gracefully terminate supervisor and 5 child processes" }
    "4"  = @{ Name = "hunter-procs";        Label = "Spread Hunter Status";          Desc = "Inspect running process tree and active port health" }
    "5"  = @{ Name = "bankroll-start";      Label = "Start 10 Bankroll Bots";        Desc = 'Launch 10 simultaneous bots ($100-$1000 in isolated dirs)' }
    "6"  = @{ Name = "bankroll-start-fresh";Label = "Start 10 Bots (Fresh Run)";     Desc = 'Archive existing bankroll runs and launch fresh sample' }
    "7"  = @{ Name = "bankroll-stop";       Label = "Stop 10 Bankroll Bots";         Desc = "Terminate all 10 running bankroll experiment workers" }
    "8"  = @{ Name = "bankroll-report";     Label = "Bankroll Stats Report";         Desc = "Compute Student t 95%/98% CIs, Sharpe, and Sortino" }
    "9"  = @{ Name = "pairs-ev-report";     Label = "Pairs EV Analysis";             Desc = "Empirical EV of pairs completion vs naked exit counterfactual" }
    "10" = @{ Name = "test-suite";          Label = "Run Test Suite (pytest)";       Desc = "Execute full test suite (671 unit and integration tests)" }
    "11" = @{ Name = "live-logs";           Label = "Stream Engine Logs";            Desc = "Follow live trading events and sweeps in real-time" }
    "d"  = @{ Name = "open-dash";           Label = "Open Dashboard in Browser";     Desc = "Open http://127.0.0.1:8800 in default web browser" }
    "q"  = @{ Name = "quit";                Label = "Exit Menu";                     Desc = "Return to PowerShell command line" }
}

function Get-StrategyOnlineState {
    $hunterLive = @(Get-HunterInstance)
    $bankrollLive = @(Get-BankrollInstance)
    $portActive = if (Get-Command Test-PortListening -ErrorAction SilentlyContinue) { Test-PortListening 8800 } else { $false }
    return ($hunterLive.Count -gt 0 -or $bankrollLive.Count -gt 0 -or $portActive)
}

function Show-Header {
    Clear-Host
    $width = if (Get-Command Get-ProfileContentWidth -ErrorAction SilentlyContinue) { Get-ProfileContentWidth } else { 80 }
    $title = "SPREAD HUNTER - CONTROL CENTER"
    $subtitle = "Two-Sided Market Maker & 10-Tier Bankroll Sensitivity Matrix"
    $isOnline = Get-StrategyOnlineState

    $statusLabel = if ($isOnline) { "● ONLINE" } else { "○ OFFLINE" }
    $spaces = [Math]::Max(2, $width - $title.Length - $statusLabel.Length)

    $heavy = if (Get-Command Get-ProfileSectionSeparator -ErrorAction SilentlyContinue) { Get-ProfileSectionSeparator } else { "=" }
    $borderColor = if (Get-Command Get-ProfileColor -ErrorAction SilentlyContinue) { Get-ProfileColor -Name "Border" } else { [ConsoleColor]::DarkCyan }
    $titleColor  = if (Get-Command Get-ProfileColor -ErrorAction SilentlyContinue) { Get-ProfileColor -Name "Info" } else { [ConsoleColor]::Cyan }
    $subColor    = if (Get-Command Get-ProfileColor -ErrorAction SilentlyContinue) { Get-ProfileColor -Name "Neutral" } else { [ConsoleColor]::DarkGray }

    Write-Host ($heavy * $width) -ForegroundColor $borderColor
    Write-Host $title -ForegroundColor $titleColor -NoNewline
    Write-Host (" " * $spaces) -NoNewline
    if ($isOnline) {
        Write-Host "$([char]27)[1m" -NoNewline
        Write-Host $statusLabel -ForegroundColor Green -NoNewline
        Write-Host "$([char]27)[0m"
    } else {
        Write-Host $statusLabel -ForegroundColor DarkGray
    }
    Write-Host $subtitle -ForegroundColor $subColor
    Write-Host ($heavy * $width) -ForegroundColor $borderColor
    Write-Host ""
}

function Write-MenuItem {
    param([string]$Key)
    $item = $MenuActions[$Key]
    if (-not $item) { return }

    $numColor   = Get-ProfileColor -Name "Highlight"
    $descColor  = Get-ProfileColor -Name "Neutral"
    $kStr       = ("[{0}]" -f $Key).PadRight(6)

    Write-Host "   $kStr" -ForegroundColor $numColor -NoNewline

    $fullLabel = $item.Label
    $labelPadWidth = 34

    if ($fullLabel -match '^(Start)\s+(.*)$') {
        $verb = $Matches[1]
        $rest = $Matches[2]
        Write-Host "$verb " -ForegroundColor (Get-ProfileColor -Name 'Success') -NoNewline
        $pad = $rest.PadRight($labelPadWidth - $verb.Length - 1)
        Write-Host "$pad " -ForegroundColor (Get-ProfileColor -Name 'Strong') -NoNewline
    }
    elseif ($fullLabel -match '^(Stop)\s+(.*)$') {
        $verb = $Matches[1]
        $rest = $Matches[2]
        Write-Host "$verb " -ForegroundColor (Get-ProfileColor -Name 'Error') -NoNewline
        $pad = $rest.PadRight($labelPadWidth - $verb.Length - 1)
        Write-Host "$pad " -ForegroundColor (Get-ProfileColor -Name 'Strong') -NoNewline
    }
    elseif ($fullLabel -match '^(Exit)\s+(.*)$') {
        $verb = $Matches[1]
        $rest = $Matches[2]
        Write-Host "$verb " -ForegroundColor (Get-ProfileColor -Name 'Error') -NoNewline
        $pad = $rest.PadRight($labelPadWidth - $verb.Length - 1)
        Write-Host "$pad " -ForegroundColor (Get-ProfileColor -Name 'Strong') -NoNewline
    }
    elseif ($fullLabel -match '^(Run|Stream|Open)\s+(.*)$') {
        $verb = $Matches[1]
        $rest = $Matches[2]
        Write-Host "$verb " -ForegroundColor (Get-ProfileColor -Name 'Info') -NoNewline
        $pad = $rest.PadRight($labelPadWidth - $verb.Length - 1)
        Write-Host "$pad " -ForegroundColor (Get-ProfileColor -Name 'Strong') -NoNewline
    }
    elseif ($fullLabel -match '^(.*?)\s+(Status)$') {
        $prefix = $Matches[1]
        $verb   = $Matches[2]
        Write-Host "$prefix " -ForegroundColor (Get-ProfileColor -Name 'Strong') -NoNewline
        $pad = $verb.PadRight($labelPadWidth - $prefix.Length - 1)
        Write-Host "$pad " -ForegroundColor (Get-ProfileColor -Name 'Info') -NoNewline
    }
    else {
        Write-Host ("{0,-$labelPadWidth} " -f $fullLabel) -ForegroundColor (Get-ProfileColor -Name 'Strong') -NoNewline
    }

    Write-Host "$($item.Desc)" -ForegroundColor $descColor
}

function Show-MenuGrid {
    Write-ProfileRuleWithText -Text "SPREAD HUNTER STRATEGY" -Style "Border"
    Write-MenuItem "1"
    Write-MenuItem "2"
    Write-MenuItem "3"
    Write-MenuItem "4"
    Write-Host ""

    Write-ProfileRuleWithText -Text "10-TIER BANKROLL SENSITIVITY EXPERIMENTS" -Style "Border"
    Write-MenuItem "5"
    Write-MenuItem "6"
    Write-MenuItem "7"
    Write-MenuItem "8"
    Write-Host ""

    Write-ProfileRuleWithText -Text "RESEARCH, DIAGNOSTICS & TELEMETRY" -Style "Border"
    Write-MenuItem "9"
    Write-MenuItem "10"
    Write-MenuItem "11"
    Write-Host ""

    Write-ProfileRuleWithText -Text "SHORTCUTS" -Style "Border"
    Write-MenuItem "d"
    Write-MenuItem "q"
    Write-Host ""
}

function Invoke-MenuAction {
    param([string]$Key)
    $item = $MenuActions[$Key]
    if (-not $item) {
        Write-ProfileError -Message "Invalid selection:" -Detail $Key -Suggestion "Choose 1-11, 'd' for dashboard, or 'q' to quit."
        Start-Sleep -Seconds 1
        return
    }

    $act = $item.Name
    $paramNote = ""
    if ($act -eq "hunter-start") { $paramNote = " [Param: Default Run (-FreshRun=$false)]" }
    elseif ($act -eq "hunter-start-fresh") { $paramNote = " [Param: -FreshRun=$true (Archive prior DB)]" }
    elseif ($act -eq "bankroll-start") { $paramNote = " [Param: Default Run (-FreshRun=$false)]" }
    elseif ($act -eq "bankroll-start-fresh") { $paramNote = " [Param: -FreshRun=$true (Archive experiment DBs)]" }

    if ($Action -ne "") {
        $paramNote += " (Invoked via CLI parameter: '$Action')"
    }

    Write-Host ""
    Write-ProfileInfo -Message "Executing: " -Detail "$($item.Label)$paramNote"
    Write-Host ""

    switch ($act) {
        "hunter-start" {
            & (Join-Path $PSScriptRoot "hunter-start.ps1")
        }
        "hunter-start-fresh" {
            & (Join-Path $PSScriptRoot "hunter-start.ps1") -FreshRun
        }
        "hunter-stop" {
            & (Join-Path $PSScriptRoot "hunter-stop.ps1")
        }
        "hunter-procs" {
            . (Join-Path $PSScriptRoot "hunter-procs.ps1")
            $inst = @(Get-HunterInstance)
            if ($inst.Count -gt 0) {
                Write-ProfileSuccess -Message "Spread Hunter Active Process Tree"
                $inst | ForEach-Object {
                    $desc = @(Get-DescendantPids -ParentId $_.pid)
                    Write-ProfileKeyValue -Key "Supervisor PID $($_.pid)" -Value "Child PIDs: $($desc -join ', ')" -Style "Info" -KeyWidth 22
                }
                Write-ProfileKeyValue -Key "Main Dashboard" -Value "http://127.0.0.1:8800" -Style "Link" -KeyWidth 22
                Write-ProfileKeyValue -Key "Market Scan" -Value "http://127.0.0.1:8801/?view=scan" -Style "Link" -KeyWidth 22
            } else {
                Write-ProfileError -Message "No active Spread Hunter instance running." -Suggestion "Run option 1 or 'hunter-start' to launch the strategy stack."
            }
        }
        "bankroll-start" {
            & (Join-Path $PSScriptRoot "bankroll-start.ps1")
        }
        "bankroll-start-fresh" {
            & (Join-Path $PSScriptRoot "bankroll-start.ps1") -FreshRun
        }
        "bankroll-stop" {
            & (Join-Path $PSScriptRoot "bankroll-stop.ps1")
        }
        "bankroll-report" {
            python -m scripts.bankroll_stats_report
        }
        "pairs-ev-report" {
            python -m scripts.pairs_ev_report
        }
        "test-suite" {
            python -m pytest
        }
        "live-logs" {
            Write-ProfileInfo -Message "Streaming logs/supervisor.err.log" -Detail "(Press Ctrl+C to exit)..."
            Get-Content (Join-Path $ProjectPath "logs/supervisor.err.log") -Wait -Tail 25
        }
        "open-dash" {
            Start-Process "http://127.0.0.1:8800"
            Write-ProfileSuccess -Message "Opened http://127.0.0.1:8800 in default browser."
        }
        "quit" {
            Write-ProfileInfo -Message "Exiting Spread Hunter Menu."
            exit 0
        }
    }

    if ($act -ne "quit" -and -not $Action) {
        Write-Host ""
        Write-ProfileNeutral -Message "Press [Enter] to return to the menu..."
        [void][Console]::ReadLine()
    }
}

if ($Action -ne "") {
    $trimmed = $Action.Trim().ToLower()
    $aliasMap = @{
        "start"        = "1"
        "start-fresh"  = "2"
        "stop"         = "3"
        "status"       = "4"
        "bstart"       = "5"
        "bstart-fresh" = "6"
        "bstop"        = "7"
        "breport"      = "8"
        "report"       = "8"
        "ev"           = "9"
        "test"         = "10"
        "logs"         = "11"
        "dash"         = "d"
        "exit"         = "q"
    }
    if ($aliasMap.ContainsKey($trimmed)) {
        Invoke-MenuAction $aliasMap[$trimmed]
    } elseif ($MenuActions.Contains($trimmed)) {
        Invoke-MenuAction $trimmed
    } else {
        Write-ProfileError -Message "Unknown action '$Action'" -Suggestion "Valid: 1-11, start, stop, status, bstart, bstop, report, test, logs, dash, exit"
        exit 1
    }
    exit 0
}

while ($true) {
    Show-Header
    Show-MenuGrid

    $promptColor = Get-ProfileColor -Name "Highlight"
    Write-Host "   Select an option [1-11, d, q]: " -NoNewline -ForegroundColor $promptColor
    $choice = [Console]::ReadLine()
    if ($null -eq $choice) { break }
    $choice = $choice.Trim()
    if ($choice -eq "") { continue }

    Invoke-MenuAction $choice
}
