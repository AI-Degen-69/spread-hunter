# BACKWARD-COMPATIBILITY WRAPPER
# Forwards to hunter-start.ps1
param(
    [switch]$FreshRun
)
$params = @{}
if ($FreshRun) { $params["FreshRun"] = $true }
& (Join-Path $PSScriptRoot "hunter-start.ps1") @params
