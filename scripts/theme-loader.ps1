# Theme & Color System Bootstrap Loader for Spread Hunter
# Dot-sources the profile theme scripts and provides ASCII-safe fallback contracts.

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

$pcsPath = "C:\Program Files\PowerShell\7\scripts\Theme\Theme-ColorSystem.ps1"
$tplPath = "C:\Program Files\PowerShell\7\scripts\Theme\Theme-Templates.ps1"

if ((Test-Path $pcsPath) -and (Test-Path $tplPath)) {
    try {
        . $pcsPath
        . $tplPath
    } catch {}
}

if (-not (Get-Command Write-ProfileSuccess -ErrorAction SilentlyContinue)) {
    function Write-ProfileSuccess { param([string]$Message, [string]$Detail = "", [int]$Indent = 1) Write-Host (" " * $Indent + "[OK] $Message $Detail") -ForegroundColor Green }
    function Write-ProfileError   { param([string]$Message, [string]$Detail = "", [string]$Suggestion = "", [int]$Indent = 1) Write-Host (" " * $Indent + "[FAIL] $Message $Detail") -ForegroundColor Red; if ($Suggestion) { Write-Host (" " * $Indent + "[INFO] Try: $Suggestion") -ForegroundColor Cyan } }
    function Write-ProfileWarning { param([string]$Message, [string]$Detail = "", [int]$Indent = 1) Write-Host (" " * $Indent + "[WARN] $Message $Detail") -ForegroundColor Yellow }
    function Write-ProfileInfo    { param([string]$Message, [string]$Detail = "", [int]$Indent = 1) Write-Host (" " * $Indent + "[INFO] $Message $Detail") -ForegroundColor Cyan }
    function Write-ProfileNeutral { param([string]$Message, [string]$Detail = "", [int]$Indent = 1) Write-Host (" " * $Indent + "[--] $Message $Detail") -ForegroundColor DarkGray }
    function Write-ProfileKeyValue { param([string]$Key, [string]$Value, [string]$Style = "Info", [int]$KeyWidth = 18, [int]$Indent = 2) Write-Host (" " * $Indent + ("{0,-$KeyWidth}" -f $Key) + " ") -ForegroundColor DarkGray -NoNewline; Write-Host $Value -ForegroundColor Cyan }
    function Write-ProfileBanner  { param([string]$Title, [string]$Subtitle = "", [string]$Caption = "", [string]$Style = "Info") Write-Host ("=" * 80) -ForegroundColor DarkCyan; Write-Host $Title -ForegroundColor Cyan; if ($Subtitle) { Write-Host $Subtitle -ForegroundColor DarkGray }; Write-Host ("=" * 80) -ForegroundColor DarkCyan }
    function Write-ProfileRuleWithText { param([string]$Text, [string]$Style = "Neutral") Write-Host ("--- $Text " + ("-" * [Math]::Max(5, (75 - $Text.Length)))) -ForegroundColor DarkCyan }
    function Write-ProfileRuleHeavy    { param([string]$Style = "Border") Write-Host ("=" * 80) -ForegroundColor DarkCyan }
    function Get-ProfileColor     { param([string]$Name) switch ($Name) { "Success" { [ConsoleColor]::Green } "Error" { [ConsoleColor]::Red } "Warning" { [ConsoleColor]::Yellow } "Info" { [ConsoleColor]::Cyan } "Neutral" { [ConsoleColor]::DarkGray } "Strong" { [ConsoleColor]::White } "Highlight" { [ConsoleColor]::Yellow } default { [ConsoleColor]::Gray } } }
}
