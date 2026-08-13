# SPREAD HUNTER START SCRIPT
# Starts the Spread Hunter strategy stack in the background with windowless execution.
#
# Spread Hunter: Two-sided Polymarket maker strategy resting bids on BOTH outcomes
# to capture the bid-ask spread and liquidity rewards while maintaining inventory balance.
#
# Process Hierarchy:
#   strategy.supervisor (Parent)
#     ├── strategy.fleet (Maker engine)
#     ├── server.spread_dash (Main dashboard on port 8800)
#     ├── server.fleet_dash (Scan view on port 8801)
#     ├── scripts.rerank_loop (Dynamic market universe ranker)
#     └── scripts.watch_universe (Read-only market telemetry)
#
# Usage:
#   .\scripts\hunter-start.ps1              # keep the current sample
#   .\scripts\hunter-start.ps1 -FreshRun    # archive prior DB and start clean sample
#
# Stop with .\scripts\hunter-stop.ps1. Watch logs with:
#   Get-Content logs\supervisor.log -Wait -Tail 20
param(
    [switch]$FreshRun
)

$ErrorActionPreference = "Stop"
$ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectPath
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectPath "logs") | Out-Null

. (Join-Path $PSScriptRoot "hunter-procs.ps1")

# 1. Stop any previously recorded Spread Hunter instance (including child processes)
$stopped = Stop-HunterInstance
if ($stopped -gt 0) { Write-Host "stopped $stopped prior Spread Hunter process tree(s)" -ForegroundColor Yellow }

# 2. Check for unowned stray processes
$strays = @(Find-HunterStrays)
if ($strays.Count -gt 0) {
    Write-Host ""
    Write-Host "WARNING: $($strays.Count) hunter-shaped process(es) not started by this script:" -ForegroundColor Red
    $strays | ForEach-Object {
        $cl = ($_.CommandLine -replace '\s+', ' ')
        Write-Host "  PID $($_.ProcessId)  $($cl.Substring(0, [Math]::Min(90, $cl.Length)))" -ForegroundColor DarkGray
    }
    Write-Host "  Not stopping them -- they may belong to another checkout or user." -ForegroundColor DarkGray
    Write-Host "  If they are yours, stop them first: .\scripts\hunter-stop.ps1 -Strays" -ForegroundColor DarkGray
    Write-Host ""
}

# 3. Check for port 8800 conflict
if (netstat -ano | Select-String ":8800\s+.*LISTENING") {
    throw "Port 8800 is occupied by an unrelated process; refusing to start."
}

# 4. Archive prior database if -FreshRun requested
if ($FreshRun) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $archive = Join-Path $ProjectPath "run/archive/hunter_$stamp"
    New-Item -ItemType Directory -Force -Path $archive | Out-Null
    Get-ChildItem -Path (Join-Path $ProjectPath "run") -File -Filter "fleet.db*" -ErrorAction SilentlyContinue |
        Move-Item -Destination $archive -Force
    $state = Join-Path $ProjectPath "run/fleet_state.json"
    if (Test-Path $state) { Move-Item -Path $state -Destination $archive -Force }
    Write-Host "archived prior run to $archive" -ForegroundColor Yellow
}

# Environment setup
$env:HUNTER_DB = "run/fleet.db"
$env:SPREAD_HUNTER_DB = "run/fleet.db"
$env:HUNTER_DEPTH_TRIAL_USD = "500"
$env:HUNTER_VOLUME_TRIAL_USD = "125000"
$env:HUNTER_MARGINAL_FLOOR = "0.0001"

# 5. Start supervisor in hidden background process
$sup = Start-Process -FilePath "python" `
    -ArgumentList "-m", "strategy.supervisor" `
    -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $ProjectPath "logs/supervisor.out.log") `
    -RedirectStandardError  (Join-Path $ProjectPath "logs/supervisor.err.log")

Save-HunterInstance -Procs @{ supervisor = $sup }

Start-Sleep -Seconds 6

# 6. Verify supervisor is alive
$sup.Refresh()
if ($sup.HasExited) {
    throw "Spread Hunter startup failed: supervisor exited (see logs\supervisor.err.log)"
}

$alive = @(Get-HunterInstance).Count + @(Get-DescendantPids -ParentId $sup.Id).Count
Write-Host ""
Write-Host "Spread Hunter supervisor PID $($sup.Id) | $alive processes up (fleet, dash, scan, rerank, watch)" -ForegroundColor Green
Write-Host "Dashboard:    http://127.0.0.1:8800"
Write-Host "Market Scan:  http://127.0.0.1:8801/?view=scan"
Write-Host "Logs:         Get-Content logs\supervisor.log -Wait -Tail 20"
Write-Host "Stop:         .\scripts\hunter-stop.ps1"
