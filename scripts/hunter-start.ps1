# SPREAD HUNTER START SCRIPT
# Starts the Spread Hunter strategy stack in the background with windowless execution.
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

Write-Host "Spread Hunter Startup" -ForegroundColor Cyan
Write-Host "[1/5] Checking for prior instances and port availability..." -ForegroundColor DarkGray

# 1. Stop any previously recorded Spread Hunter instance (including child processes)
$stopped = Stop-HunterInstance
if ($stopped -gt 0) { Write-Host "      Stopped $stopped prior Spread Hunter process tree(s)" -ForegroundColor Yellow }

# 2. Check for unowned stray processes
$strays = @(Find-HunterStrays)
if ($strays.Count -gt 0) {
    Write-Host "      WARNING: $($strays.Count) hunter-shaped process(es) not started by this script:" -ForegroundColor Red
    $strays | ForEach-Object {
        $cl = ($_.CommandLine -replace '\s+', ' ')
        Write-Host "      PID $($_.ProcessId)  $($cl.Substring(0, [Math]::Min(80, $cl.Length)))" -ForegroundColor DarkGray
    }
}

# 3. Check for port 8800 conflict
if (netstat -ano | Select-String ":8800\s+.*LISTENING") {
    throw "Port 8800 is occupied by an unrelated process; refusing to start."
}

# 4. Archive prior database if -FreshRun requested
if ($FreshRun) {
    Write-Host "[2/5] Archiving prior run data (-FreshRun active)..." -ForegroundColor DarkGray
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $archive = Join-Path $ProjectPath "run/archive/hunter_$stamp"
    New-Item -ItemType Directory -Force -Path $archive | Out-Null
    Get-ChildItem -Path (Join-Path $ProjectPath "run") -File -Filter "fleet.db*" -ErrorAction SilentlyContinue |
        Move-Item -Destination $archive -Force
    $state = Join-Path $ProjectPath "run/fleet_state.json"
    if (Test-Path $state) { Move-Item -Path $state -Destination $archive -Force }
    Write-Host "      Archived prior run to $archive" -ForegroundColor Yellow
} else {
    Write-Host "[2/5] Preserving active database run/fleet.db..." -ForegroundColor DarkGray
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

Write-Host "[3/5] Supervisor launched (PID $($sup.Id)). Spawning fleet, dashboards, and watchers..." -ForegroundColor DarkGray
Write-Host "[4/5] Waiting 6 seconds for FastAPI servers (ports 8800/8801) and workers to boot..." -ForegroundColor DarkGray

for ($i = 6; $i -gt 0; $i--) {
    Write-Host "      Booting stack... ($i s remaining)" -ForegroundColor DarkGray
    Start-Sleep -Seconds 1
}

# 6. Verify supervisor is alive
$sup.Refresh()
if ($sup.HasExited) {
    throw "Spread Hunter startup failed: supervisor exited (see logs\supervisor.err.log)"
}

$alive = @(Get-HunterInstance).Count + @(Get-DescendantPids -ParentId $sup.Id).Count

Write-Host "[5/5] All systems operational!" -ForegroundColor Green
Write-Host ""
Write-Host "Spread Hunter supervisor PID $($sup.Id) | $alive processes active" -ForegroundColor Green
Write-Host "  Main Dashboard: http://127.0.0.1:8800" -ForegroundColor Cyan
Write-Host "  Market Scan:    http://127.0.0.1:8801/?view=scan" -ForegroundColor Cyan
Write-Host "  Live Logs:      Get-Content logs\supervisor.err.log -Wait -Tail 20" -ForegroundColor DarkGray
Write-Host "  Stop Command:   hunter-stop" -ForegroundColor DarkGray
Write-Host ""
