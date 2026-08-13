# 10-TIER BANKROLL SENSITIVITY EXPERIMENT STOP SCRIPT
# Stops all running bankroll experiment worker processes and updates status.json.
#
# Usage:
#   .\scripts\bankroll-stop.ps1

$ProjectPath = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot "theme-loader.ps1")
. (Join-Path $PSScriptRoot "bankroll-procs.ps1")

Write-ProfileBanner -Title "10-TIER BANKROLL SENSITIVITY - SHUTDOWN" `
                    -Subtitle "Terminating All Active Experiment Bots" `
                    -Style "Warning"
Write-Host ""

Write-ProfileInfo -Message "[1/2] Terminating recorded bankroll worker processes..."

$stopped = Stop-BankrollInstance

if ($stopped -gt 0) {
    Write-ProfileWarning -Message "Terminated worker processes:" -Detail "$stopped process(es)"
} else {
    Write-ProfileError -Message "No recorded bankroll experiment processes were running."
}

$strays = @(Find-BankrollStrays)
if ($strays.Count -gt 0) {
    Write-ProfileWarning -Message "[2/2] Warning: Detected unowned bankroll processes:" -Detail "$($strays.Count) process(es)"
    $strays | ForEach-Object {
        Write-ProfileNeutral -Message "PID $($_.ProcessId):" -Detail "$($_.CommandLine)"
    }
} else {
    Write-ProfileSuccess -Message "[2/2] All bankroll experiment processes stopped."
}
