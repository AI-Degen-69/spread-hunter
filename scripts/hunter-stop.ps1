# SPREAD HUNTER STOP SCRIPT
# Stops the Spread Hunter process tree started by this repository checkout.
#
# Usage:
#   .\scripts\hunter-stop.ps1            # stop the recorded Spread Hunter instance
#   .\scripts\hunter-stop.ps1 -Strays    # also stop hunter-shaped processes not owned by this checkout
param([switch]$Strays)

$ProjectPath = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot "hunter-procs.ps1")

Write-Host "Stopping Spread Hunter..." -ForegroundColor Cyan
Write-Host "[1/2] Terminating recorded supervisor and child processes..." -ForegroundColor DarkGray

$stopped = Stop-HunterInstance
if ($stopped -gt 0) {
    Write-Host "      Stopped $stopped process tree(s)." -ForegroundColor Yellow
}

$unowned = @(Find-HunterStrays)

if ($unowned.Count -eq 0) {
    if ($stopped -eq 0) { 
        Write-Host "[2/2] No Spread Hunter processes were running." -ForegroundColor DarkGray 
    } else {
        Write-Host "[2/2] Spread Hunter stopped successfully." -ForegroundColor Green
    }
    return
}

if (-not $Strays) {
    Write-Host ""
    Write-Host "[2/2] Found $($unowned.Count) hunter-shaped process(es) NOT owned by this checkout:" -ForegroundColor Yellow
    $unowned | ForEach-Object {
        $cl = ($_.CommandLine -replace '\s+', ' ')
        Write-Host "      PID $($_.ProcessId)  $($cl.Substring(0, [Math]::Min(80, $cl.Length)))" -ForegroundColor DarkGray
    }
    Write-Host "      Left running. To stop them, run: hunter-stop -Strays" -ForegroundColor DarkGray
    return
}

Write-Host "[2/2] -Strays specified: terminating $($unowned.Count) unowned process(es)..." -ForegroundColor Yellow
$sups = @($unowned | Where-Object { $_.CommandLine -like "*strategy.supervisor*" })
$rest = @($unowned | Where-Object { $_.CommandLine -notlike "*strategy.supervisor*" })
foreach ($p in ($sups + $rest)) {
    Stop-HunterTree -ProcessId $p.ProcessId -Label "(stray)"
    Start-Sleep -Milliseconds 300
}

Start-Sleep -Seconds 2
$left = @(Find-HunterStrays)
if ($left.Count -gt 0) {
    Write-Host "WARNING: Still running: $(($left.ProcessId) -join ', ')" -ForegroundColor Red
} else {
    Write-Host "All Spread Hunter processes stopped successfully." -ForegroundColor Green
}
