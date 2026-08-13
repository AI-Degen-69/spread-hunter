# BACKWARD-COMPATIBILITY WRAPPER
# Forwards to hunter-procs.ps1
$ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "hunter-procs.ps1")
