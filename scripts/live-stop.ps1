# SPREAD HUNTER LIVE DASHBOARD STOP SCRIPT
# Stops the background live execution monitor started by this checkout.
#
# Usage:
#   .\scripts\live-stop.ps1

$ProjectPath = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot "theme-loader.ps1")
. (Join-Path $PSScriptRoot "live-procs.ps1")

Write-ProfileBanner -Title "SPREAD HUNTER - LIVE DASHBOARD SHUTDOWN" `
                    -Subtitle "Terminating background Live Execution Monitor" `
                    -Style "Warning"
Write-Host ""

Write-ProfileInfo -Message "[1/1] Terminating recorded live dashboard process..."

$stopped = Stop-LiveDashInstance
if ($stopped -gt 0) {
    Write-ProfileSuccess -Message "Live dashboard stopped successfully."
} else {
    Write-ProfileError -Message "No active live dashboard process was running."
}
