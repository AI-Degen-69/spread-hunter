# SPREAD HUNTER START SCRIPT
# Starts the Spread Hunter strategy stack in the background with windowless execution.
#
# Usage:
#   .\scripts\hunter-start.ps1              # keep the current sample
#   .\scripts\hunter-start.ps1 -FreshRun    # archive prior DB and start clean sample
param(
    [switch]$FreshRun
)

$ErrorActionPreference = "Stop"
$ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectPath
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectPath "logs") | Out-Null

. (Join-Path $PSScriptRoot "theme-loader.ps1")
. (Join-Path $PSScriptRoot "hunter-procs.ps1")

Write-ProfileBanner -Title "SPREAD HUNTER - STACK LAUNCHER" `
                    -Subtitle "Two-Sided Maker Strategy on Polymarket Orderbooks" `
                    -Style "Info"
Write-Host ""

Write-ProfileInfo -Message "[1/5] Checking prior instances and port availability..."

# 1. Stop any previously recorded Spread Hunter instance (including child processes)
$stopped = Stop-HunterInstance
if ($stopped -gt 0) { 
    Write-ProfileWarning -Message "Stopped prior instance:" -Detail "$stopped Spread Hunter process tree(s)" 
}

# 2. Check for unowned stray processes
$strays = @(Find-HunterStrays)
if ($strays.Count -gt 0) {
    Write-ProfileWarning -Message "Detected unowned hunter-shaped processes:" -Detail "$($strays.Count) process(es) running"
    $strays | ForEach-Object {
        $cl = ($_.CommandLine -replace '\s+', ' ')
        Write-ProfileNeutral -Message "PID $($_.ProcessId):" -Detail "$($cl.Substring(0, [Math]::Min(75, $cl.Length)))"
    }
}

# 3. Check for port 8800 conflict
if (netstat -ano | Select-String ":8800\s+.*LISTENING") {
    Write-ProfileError -Message "Port 8800 Conflict:" -Detail "Port 8800 is occupied by another process." -Suggestion "Run hunter-stop -Strays or free port 8800."
    throw "Port 8800 occupied."
}

# 4. Archive prior database if -FreshRun requested
if ($FreshRun) {
    Write-ProfileInfo -Message "[2/5] Archiving prior run data (-FreshRun active)..."
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $archive = Join-Path $ProjectPath "run/archive/hunter_$stamp"
    New-Item -ItemType Directory -Force -Path $archive | Out-Null
    Get-ChildItem -Path (Join-Path $ProjectPath "run") -File -Filter "fleet.db*" -ErrorAction SilentlyContinue |
        Move-Item -Destination $archive -Force
    $state = Join-Path $ProjectPath "run/fleet_state.json"
    if (Test-Path $state) { Move-Item -Path $state -Destination $archive -Force }
    Write-ProfileSuccess -Message "Archived prior database to:" -Detail $archive
} else {
    Write-ProfileInfo -Message "[2/5] Preserving active database:" -Detail "run/fleet.db"
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

Write-ProfileInfo -Message "[3/5] Supervisor launched (PID $($sup.Id)). Spawning engine & dashboards..."
Write-ProfileInfo -Message "[4/5] Waiting for FastAPI servers (ports 8800/8801) and workers to boot..."

for ($i = 6; $i -gt 0; $i--) {
    Start-Sleep -Seconds 1
}

# 6. Verify supervisor is alive
$sup.Refresh()
if ($sup.HasExited) {
    Write-ProfileError -Message "Spread Hunter startup failed:" -Detail "Supervisor exited immediately (check logs/supervisor.err.log)"
    throw "Supervisor exited."
}

$alive = @(Get-HunterInstance).Count + @(Get-DescendantPids -ParentId $sup.Id).Count

Write-Host ""
Write-ProfileSuccess -Message "All systems operational!" -Detail "Supervisor PID $($sup.Id) | $alive processes active"
Write-ProfileKeyValue -Key "Main Dashboard" -Value "http://127.0.0.1:8800" -Style "Link"
Write-ProfileKeyValue -Key "Market Scan" -Value "http://127.0.0.1:8801/?view=scan" -Style "Link"
Write-ProfileKeyValue -Key "Live Logs" -Value "Get-Content logs\supervisor.err.log -Wait -Tail 20" -Style "Command"
Write-ProfileKeyValue -Key "Stop Command" -Value "hunter-stop" -Style "Highlight"
Write-Host ""
