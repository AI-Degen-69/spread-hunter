# Spread Hunter - Top Level Menu Shortcut
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Action = ""
)

& (Join-Path $PSScriptRoot "scripts\hunter-menu.ps1") @PSBoundParameters
