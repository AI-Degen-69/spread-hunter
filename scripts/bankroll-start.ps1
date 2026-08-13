# 10-TIER BANKROLL SENSITIVITY EXPERIMENT LAUNCHER
# Starts 10 simultaneous paper-trading bots with isolated workdirs ($100 to $1,000 in $100 steps).
#
# Each tier runs an independent instance of strategy.fleet with its own SQLite database
# (run/bankroll_<tier>/fleet.db) and logs (run/bankroll_<tier>/fleet.log).
#
# Usage:
#   .\scripts\bankroll-start.ps1              # launch the 10 bankroll tiers
#   .\scripts\bankroll-start.ps1 -FreshRun    # archive existing tier data before starting
#   .\scripts\bankroll-start.ps1 -DryRun      # validate paths and configs without launching
#
# Monitor with:
#   Dashboard Matrix: http://127.0.0.1:8800
#   Stats Report:     python -m scripts.bankroll_stats_report
#   Stop Experiments: .\scripts\bankroll-stop.ps1
param(
    [switch]$FreshRun,
    [switch]$DryRun,
    [int]$Start = 100,
    [int]$End = 1000,
    [int]$Step = 100
)

$ErrorActionPreference = "Stop"
$ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectPath

. (Join-Path $PSScriptRoot "bankroll-procs.ps1")

# 1. Stop any currently running bankroll experiment instances
$stopped = Stop-BankrollInstance
if ($stopped -gt 0) {
    Write-Host "Stopped $stopped prior bankroll experiment process(es)." -ForegroundColor Yellow
}

# 2. Archive previous experiment directories if -FreshRun requested
if ($FreshRun) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $archive = Join-Path $ProjectPath "run/archive/bankroll_$stamp"
    New-Item -ItemType Directory -Force -Path $archive | Out-Null
    Get-ChildItem -Path (Join-Path $ProjectPath "run") -Directory -Filter "bankroll_*" -ErrorAction SilentlyContinue |
        Move-Item -Destination $archive -Force
    Write-Host "Archived prior bankroll runs to $archive" -ForegroundColor Yellow
}

Write-Host "=== Launching 10-Tier Bankroll Sensitivity Experiments ($Start - $End) ===" -ForegroundColor Cyan

# 3. Build tier configurations and launch
$tierRecords = @()

for ($amount = $Start; $amount -le $End; $amount += $Step) {
    $workdir = Join-Path $ProjectPath "run/bankroll_$amount"
    $dbPath = Join-Path $workdir "fleet.db"
    $logPath = Join-Path $workdir "fleet.log"
    $statusPath = Join-Path $workdir "status.json"

    New-Item -ItemType Directory -Force -Path $workdir | Out-Null

    if ($DryRun) {
        Write-Host " [DRY RUN] Tier `$$amount -> $workdir" -ForegroundColor DarkGray
        continue
    }

    $statusData = [pscustomobject]@{
        bankroll       = $amount
        status         = "INITIALIZING"
        created_at     = [double](Get-Date -UFormat %s)
        target_samples = 100
        min_samples    = 30
    }
    $statusData | ConvertTo-Json -Depth 4 | Set-Content -Path $statusPath -Encoding UTF8

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "python"
    $psi.Arguments = "-m strategy.fleet"
    $psi.WorkingDirectory = $ProjectPath
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $psi.CreateNoWindow = $true
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true

    # Set isolated environment variables
    $psi.EnvironmentVariables["HUNTER_DB"] = $dbPath
    $psi.EnvironmentVariables["SPREAD_HUNTER_DB"] = $dbPath
    $psi.EnvironmentVariables["HUNTER_BANKROLL"] = [string]$amount
    $psi.EnvironmentVariables["SPREAD_HUNTER_BANKROLL"] = [string]$amount
    $psi.EnvironmentVariables["HUNTER_MARGINAL_FLOOR"] = "0.0001"

    $proc = [System.Diagnostics.Process]::Start($psi)

    if ($proc) {
        $statusData.status = "RUNNING"
        $statusData.pid = $proc.Id
        $statusData.started_at = [double](Get-Date -UFormat %s)
        $statusData | ConvertTo-Json -Depth 4 | Set-Content -Path $statusPath -Encoding UTF8

        $tierRecords += [pscustomobject]@{
            bankroll      = $amount
            pid           = $proc.Id
            workdir       = $workdir
            started_ticks = $proc.StartTime.ToUniversalTime().Ticks
            started       = $proc.StartTime.ToString("o")
        }

        Write-Host "  [OK] Tier `$$amount -> PID $($proc.Id) (DB: run\bankroll_$amount\fleet.db)" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] Tier `$$amount failed to launch." -ForegroundColor Red
    }
}

if (-not $DryRun) {
    Save-BankrollInstance -TierRecords $tierRecords
    Write-Host ""
    Write-Host "Launched $($tierRecords.Count) Bankroll Experiment Instances successfully." -ForegroundColor Green
    Write-Host "Dashboard Matrix:  http://127.0.0.1:8800"
    Write-Host "Stats CLI Report:  python -m scripts.bankroll_stats_report"
    Write-Host "Stop All Tiers:    .\scripts\bankroll-stop.ps1"
} else {
    Write-Host "Dry run completed successfully." -ForegroundColor DarkGray
}
