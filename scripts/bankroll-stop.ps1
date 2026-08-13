# 10-TIER BANKROLL SENSITIVITY EXPERIMENT STOP SCRIPT
# Stops all running bankroll experiment worker processes and updates status.json.
#
# Usage:
#   .\scripts\bankroll-stop.ps1

$ProjectPath = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot "bankroll-procs.ps1")

Write-Host "Stopping 10-Tier Bankroll Sensitivity Experiment instances..." -ForegroundColor Cyan
$stopped = Stop-BankrollInstance

if ($stopped -gt 0) {
    Write-Host "Bankroll experiments stopped ($stopped process(es) terminated)." -ForegroundColor Green
} else {
    Write-Host "No active bankroll experiment processes were running." -ForegroundColor DarkGray
}

$strays = @(Find-BankrollStrays)
if ($strays.Count -gt 0) {
    Write-Host "Warning: Found $($strays.Count) unowned bankroll process(es):" -ForegroundColor Yellow
    $strays | ForEach-Object {
        Write-Host "  PID $($_.ProcessId)" -ForegroundColor DarkGray
    }
}
