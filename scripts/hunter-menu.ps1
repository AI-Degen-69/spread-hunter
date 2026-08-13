# Spread Hunter Control Center Interactive Menu
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Action = ""
)

$ErrorActionPreference = "Stop"
$ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectPath

$themeLoaded = $false
$pcsPath = "C:\Program Files\PowerShell\7\scripts\Theme\Theme-ColorSystem.ps1"
$tplPath = "C:\Program Files\PowerShell\7\scripts\Theme\Theme-Templates.ps1"

if ((Test-Path $pcsPath) -and (Test-Path $tplPath)) {
    try {
        . $pcsPath
        . $tplPath
        $themeLoaded = $true
    } catch {
        $themeLoaded = $false
    }
}

function Get-Color {
    param([string]$Role, [ConsoleColor]$Fallback)
    if ($themeLoaded -and (Get-Command Get-ProfileColor -ErrorAction SilentlyContinue)) {
        return (Get-ProfileColor -Name $Role)
    }
    return $Fallback
}

$MenuActions = [ordered]@{
    "1"  = @{ Name = "hunter-start";        Label = "Start Spread Hunter";           Desc = "Launch supervisor, maker engine & dashboards (:8800/:8801)" }
    "2"  = @{ Name = "hunter-start-fresh";  Label = "Start Spread Hunter (Fresh)";   Desc = "Archive active DB (fleet.db) and start clean sample" }
    "3"  = @{ Name = "hunter-stop";         Label = "Stop Spread Hunter";            Desc = "Gracefully terminate supervisor and 5 child processes" }
    "4"  = @{ Name = "hunter-procs";        Label = "Spread Hunter Status";          Desc = "Inspect running process tree and active port health" }
    "5"  = @{ Name = "bankroll-start";      Label = "Start 10 Bankroll Bots";        Desc = "Launch 10 simultaneous bots ($100-$1000 in isolated dirs)" }
    "6"  = @{ Name = "bankroll-start-fresh";Label = "Start 10 Bots (Fresh Run)";     Desc = "Archive existing bankroll runs and launch fresh sample" }
    "7"  = @{ Name = "bankroll-stop";       Label = "Stop 10 Bankroll Bots";         Desc = "Terminate all 10 running bankroll experiment workers" }
    "8"  = @{ Name = "bankroll-report";     Label = "Bankroll Stats Report";         Desc = "Compute Student t 95%/98% CIs, Sharpe, and Sortino" }
    "9"  = @{ Name = "pairs-ev-report";     Label = "Pairs EV Analysis";             Desc = "Empirical EV of pairs completion vs naked exit counterfactual" }
    "10" = @{ Name = "test-suite";          Label = "Run Test Suite (pytest)";       Desc = "Execute full test suite (671 unit and integration tests)" }
    "11" = @{ Name = "live-logs";           Label = "Stream Engine Logs";            Desc = "Follow live trading events and sweeps in real-time" }
    "d"  = @{ Name = "open-dash";           Label = "Open Dashboard in Browser";     Desc = "Open http://127.0.0.1:8800 in default web browser" }
    "q"  = @{ Name = "quit";                Label = "Exit Menu";                     Desc = "Return to PowerShell command line" }
}

function Show-Header {
    Clear-Host
    $titleColor   = Get-Color -Role "Info" -Fallback Cyan
    $borderColor  = Get-Color -Role "Border" -Fallback DarkCyan
    $neutralColor = Get-Color -Role "Neutral" -Fallback DarkGray

    Write-Host "========================================================================================" -ForegroundColor $borderColor
    Write-Host "                             SPREAD HUNTER - CONTROL CENTER                             " -ForegroundColor $titleColor
    Write-Host "              Two-Sided Market Maker & 10-Tier Bankroll Sensitivity Matrix              " -ForegroundColor $neutralColor
    Write-Host "========================================================================================" -ForegroundColor $borderColor
    Write-Host ""
}

function Write-MenuItem {
    param([string]$Key)
    $item = $MenuActions[$Key]
    if (-not $item) { return }

    $numColor   = Get-Color -Role "Highlight" -Fallback Yellow
    $labelColor = Get-Color -Role "Strong" -Fallback White
    $descColor  = Get-Color -Role "Neutral" -Fallback DarkGray

    $kStr = ("[{0}]" -f $Key).PadRight(5)
    $lbl  = $item.Label.PadRight(32)
    Write-Host "   $kStr " -NoNewline -ForegroundColor $numColor
    Write-Host "$lbl " -NoNewline -ForegroundColor $labelColor
    Write-Host "$($item.Desc)" -ForegroundColor $descColor
}

function Show-MenuGrid {
    $catColor     = Get-Color -Role "Info" -Fallback Cyan
    $borderCol    = Get-Color -Role "Border" -Fallback DarkCyan

    Write-Host "  [ SPREAD HUNTER STRATEGY ]" -ForegroundColor $catColor
    Write-Host "  --------------------------------------------------------------------------------------" -ForegroundColor $borderCol
    Write-MenuItem "1"
    Write-MenuItem "2"
    Write-MenuItem "3"
    Write-MenuItem "4"
    Write-Host ""

    Write-Host "  [ 10-TIER BANKROLL SENSITIVITY EXPERIMENTS ]" -ForegroundColor $catColor
    Write-Host "  --------------------------------------------------------------------------------------" -ForegroundColor $borderCol
    Write-MenuItem "5"
    Write-MenuItem "6"
    Write-MenuItem "7"
    Write-MenuItem "8"
    Write-Host ""

    Write-Host "  [ RESEARCH, DIAGNOSTICS & TELEMETRY ]" -ForegroundColor $catColor
    Write-Host "  --------------------------------------------------------------------------------------" -ForegroundColor $borderCol
    Write-MenuItem "9"
    Write-MenuItem "10"
    Write-MenuItem "11"
    Write-Host ""

    Write-Host "  [ SHORTCUTS ]" -ForegroundColor $catColor
    Write-Host "  --------------------------------------------------------------------------------------" -ForegroundColor $borderCol
    Write-MenuItem "d"
    Write-MenuItem "q"
    Write-Host ""
}

function Invoke-MenuAction {
    param([string]$Key)
    $item = $MenuActions[$Key]
    if (-not $item) {
        Write-Host "Invalid selection: $Key" -ForegroundColor Red
        Start-Sleep -Seconds 1
        return
    }

    $act = $item.Name
    Write-Host ""
    Write-Host ">>> Executing: $($item.Label)" -ForegroundColor Cyan
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
                Write-Host "Active Spread Hunter Process Tree:" -ForegroundColor Green
                $inst | ForEach-Object {
                    $desc = @(Get-DescendantPids -ParentId $_.pid)
                    Write-Host "  Supervisor PID $($_.pid) ($($_.name)) | Child PIDs: $($desc -join ', ')" -ForegroundColor White
                }
                Write-Host "  Dashboard:    http://127.0.0.1:8800" -ForegroundColor Cyan
                Write-Host "  Market Scan:  http://127.0.0.1:8801/?view=scan" -ForegroundColor Cyan
            } else {
                Write-Host "No active Spread Hunter instance running." -ForegroundColor Yellow
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
            pytest
        }
        "live-logs" {
            Write-Host "Streaming logs/supervisor.err.log (Press Ctrl+C to exit)..." -ForegroundColor Yellow
            Get-Content (Join-Path $ProjectPath "logs/supervisor.err.log") -Wait -Tail 25
        }
        "open-dash" {
            Start-Process "http://127.0.0.1:8800"
            Write-Host "Opened http://127.0.0.1:8800 in browser." -ForegroundColor Green
        }
        "quit" {
            Write-Host "Exiting Spread Hunter Menu." -ForegroundColor Cyan
            exit 0
        }
    }

    if ($act -ne "quit" -and -not $Action) {
        Write-Host ""
        Write-Host "Press [Enter] to return to the menu..." -ForegroundColor DarkGray
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
        "report"       = "8"
        "breport"      = "8"
        "ev"           = "9"
        "test"         = "10"
        "tests"        = "10"
        "logs"         = "11"
        "dash"         = "d"
        "dashboard"    = "d"
    }

    $key = $null
    if ($MenuActions.Contains($trimmed)) {
        $key = $trimmed
    } elseif ($aliasMap.ContainsKey($trimmed)) {
        $key = $aliasMap[$trimmed]
    }

    if ($null -ne $key) {
        Invoke-MenuAction -Key $key
        return
    } else {
        Write-Host "Unknown action: $Action" -ForegroundColor Red
        return
    }
}

while ($true) {
    Show-Header
    Show-MenuGrid

    $promptColor = Get-Color -Role "Command" -Fallback Cyan
    Write-Host "Select option [1-11, d, q]: " -NoNewline -ForegroundColor $promptColor
    $inputChoice = [Console]::ReadLine()

    if ($null -eq $inputChoice) { break }
    $choice = $inputChoice.Trim().ToLower()

    if ($choice -eq "q" -or $choice -eq "exit" -or $choice -eq "quit") {
        Write-Host "Exiting Spread Hunter Control Center." -ForegroundColor Cyan
        break
    }

    if ($MenuActions.Contains($choice)) {
        Invoke-MenuAction -Key $choice
    } else {
        Write-Host "Invalid option: $choice" -ForegroundColor Red
        Start-Sleep -Milliseconds 800
    }
}
