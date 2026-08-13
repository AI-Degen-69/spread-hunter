# SPREAD HUNTER STOP SCRIPT
# Stops the Spread Hunter process tree started by this repository checkout.
#
# Usage:
#   .\scripts\hunter-stop.ps1            # stop the recorded Spread Hunter instance
#   .\scripts\hunter-stop.ps1 -Strays    # also stop hunter-shaped processes not owned by this checkout
param([switch]$Strays)

$ProjectPath = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot "hunter-procs.ps1")

$stopped = Stop-HunterInstance
if ($stopped -gt 0) {
    Write-Host "Spread Hunter stopped ($stopped process tree(s))." -ForegroundColor Green
}

$unowned = @(Find-HunterStrays)

if ($unowned.Count -eq 0) {
    if ($stopped -eq 0) { Write-Host "Spread Hunter was not running." -ForegroundColor DarkGray }
    return
}

if (-not $Strays) {
    Write-Host ""
    Write-Host "$($unowned.Count) hunter-shaped process(es) NOT started by this checkout:" -ForegroundColor Yellow
    $unowned | ForEach-Object {
        $cl = ($_.CommandLine -replace '\s+', ' ')
        Write-Host "  PID $($_.ProcessId)  $($cl.Substring(0, [Math]::Min(90, $cl.Length)))" -ForegroundColor DarkGray
    }
    Write-Host "  Left running -- they may belong to another checkout or user." -ForegroundColor DarkGray
    Write-Host "  If they are yours: .\scripts\hunter-stop.ps1 -Strays" -ForegroundColor DarkGray
    return
}

Write-Host ""
Write-Host "-Strays given: stopping $($unowned.Count) unowned process(es)" -ForegroundColor Yellow
$sups = @($unowned | Where-Object { $_.CommandLine -like "*strategy.supervisor*" })
$rest = @($unowned | Where-Object { $_.CommandLine -notlike "*strategy.supervisor*" })
foreach ($p in ($sups + $rest)) {
    Stop-HunterTree -ProcessId $p.ProcessId -Label "(stray)"
    Start-Sleep -Milliseconds 300
}

Start-Sleep -Seconds 2
$left = @(Find-HunterStrays)
if ($left.Count -gt 0) {
    Write-Host "still running: $(($left.ProcessId) -join ', ')" -ForegroundColor Red
} else {
    Write-Host "Spread Hunter stopped." -ForegroundColor Green
}
