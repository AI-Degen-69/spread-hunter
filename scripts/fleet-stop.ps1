# BACKWARD-COMPATIBILITY WRAPPER
# Forwards to hunter-stop.ps1
param(
    [switch]$Strays
)
$params = @{}
if ($Strays) { $params["Strays"] = $true }
& (Join-Path $PSScriptRoot "hunter-stop.ps1") @params
