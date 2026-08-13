# 10-TIER BANKROLL SENSITIVITY EXPERIMENT STOP SCRIPT
# Stops all running bankroll experiment worker processes and updates status.json.
#
# Usage:
#   .\scripts\bankroll-stop.ps1

$ProjectPath = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot "bankroll-procs.ps1")

Write-Host "Stopping 10-Tier Bankroll Sensitivity Experiments..." -ForegroundColor Cyan
Write-Host "[1/2] Terminating recorded bankroll worker processes..." -ForegroundColor DarkGray

$stopped = Stop-BankrollInstance

if ($stopped -gt 0) {
    Write-Host "      Terminated $stopped bankroll worker process(es)." -ForegroundColor Yellow
} else {
    Write-Host "      No recorded bankroll experiment processes were running." -ForegroundColor DarkGray
}

$strays = @(Find-BankrollStrays)
if ($strays.Count -gt 0) {
    Write-Host "[2/2] Warning: Found $($strays.Count) unowned bankroll process(es):" -ForegroundColor Yellow
    $strays | ForEach-Object {
        Write-Host "      PID $($_.ProcessId)" -ForegroundColor DarkGray
    }
} else {
    Write-Host "[2/2] All bankroll experiment processes stopped." -ForegroundColor Green
}
