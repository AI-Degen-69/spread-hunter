# SPREAD HUNTER & BANKROLL EXPERIMENTS POWERSHELL ALIASES & FUNCTIONS
# Dot-source this file into your PowerShell session or profile:
#   . .\scripts\aliases.ps1

$Global:SpreadHunterScriptDir = $PSScriptRoot

# --- UNIFIED CONTROL CENTER MENU ---
function global:Show-HunterMenu {
    param([string]$Action)
    $script = Join-Path $Global:SpreadHunterScriptDir "hunter-menu.ps1"
    if ($Action) { & $script $Action } else { & $script }
}

function global:hunter {
    param([string]$Action)
    Show-HunterMenu -Action $Action
}

function global:hunter-menu {
    param([string]$Action)
    Show-HunterMenu -Action $Action
}

function global:hmenu {
    param([string]$Action)
    Show-HunterMenu -Action $Action
}

function global:spread-hunter {
    param([string]$Action)
    Show-HunterMenu -Action $Action
}

# --- SPREAD HUNTER (MAIN MAKER STRATEGY) ---
function global:Start-Hunter {
    param([switch]$FreshRun)
    $script = Join-Path $Global:SpreadHunterScriptDir "hunter-start.ps1"
    if ($FreshRun) { & $script -FreshRun } else { & $script }
}

function global:hunter-start {
    param([switch]$FreshRun)
    Start-Hunter -FreshRun:$FreshRun
}

function global:start-hunter {
    param([switch]$FreshRun)
    Start-Hunter -FreshRun:$FreshRun
}

function global:fleet-start {
    param([switch]$FreshRun)
    Start-Hunter -FreshRun:$FreshRun
}

function global:start-fleet {
    param([switch]$FreshRun)
    Start-Hunter -FreshRun:$FreshRun
}

function global:Stop-Hunter {
    param([switch]$Strays)
    $script = Join-Path $Global:SpreadHunterScriptDir "hunter-stop.ps1"
    if ($Strays) { & $script -Strays } else { & $script }
}

function global:hunter-stop {
    param([switch]$Strays)
    Stop-Hunter -Strays:$Strays
}

function global:stop-hunter {
    param([switch]$Strays)
    Stop-Hunter -Strays:$Strays
}

function global:fleet-stop {
    param([switch]$Strays)
    Stop-Hunter -Strays:$Strays
}

function global:stop-fleet {
    param([switch]$Strays)
    Stop-Hunter -Strays:$Strays
}


# --- 10-TIER BANKROLL SENSITIVITY EXPERIMENTS ---
function global:Start-Bankroll {
    param(
        [switch]$FreshRun,
        [switch]$DryRun,
        [int]$Start = 100,
        [int]$End = 1000,
        [int]$Step = 100
    )
    $script = Join-Path $Global:SpreadHunterScriptDir "bankroll-start.ps1"
    $params = @{
        Start = $Start
        End   = $End
        Step  = $Step
    }
    if ($FreshRun) { $params["FreshRun"] = $true }
    if ($DryRun) { $params["DryRun"] = $true }
    & $script @params
}

function global:bankroll-start {
    param(
        [switch]$FreshRun,
        [switch]$DryRun,
        [int]$Start = 100,
        [int]$End = 1000,
        [int]$Step = 100
    )
    Start-Bankroll -FreshRun:$FreshRun -DryRun:$DryRun -Start $Start -End $End -Step $Step
}

function global:start-bankroll {
    param(
        [switch]$FreshRun,
        [switch]$DryRun,
        [int]$Start = 100,
        [int]$End = 1000,
        [int]$Step = 100
    )
    Start-Bankroll -FreshRun:$FreshRun -DryRun:$DryRun -Start $Start -End $End -Step $Step
}

function global:Stop-Bankroll {
    $script = Join-Path $Global:SpreadHunterScriptDir "bankroll-stop.ps1"
    & $script
}

function global:bankroll-stop {
    Stop-Bankroll
}

function global:stop-bankroll {
    Stop-Bankroll
}

function global:bankroll-report {
    python -m scripts.bankroll_stats_report
}

function global:report-bankroll {
    python -m scripts.bankroll_stats_report
}

Write-Host "Spread Hunter & Bankroll aliases loaded:" -ForegroundColor Cyan
Write-Host "  Interactive Menu:    hunter (or hmenu / hunter-menu / spread-hunter)" -ForegroundColor Yellow
Write-Host "  Spread Hunter:       hunter-start / hunter-stop (aliases: start-hunter / stop-hunter)"
Write-Host "  Bankroll 10-Tier:    bankroll-start / bankroll-stop (aliases: start-bankroll / stop-bankroll)"
Write-Host "  Bankroll Stats:      bankroll-report / report-bankroll"
