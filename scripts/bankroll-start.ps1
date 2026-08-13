# 10-TIER BANKROLL SENSITIVITY EXPERIMENT LAUNCHER
# Starts 10 simultaneous paper-trading bots with isolated workdirs ($100 to $1,000 in $100 steps).
#
# Usage:
#   .\scripts\bankroll-start.ps1              # launch the 10 bankroll tiers
#   .\scripts\bankroll-start.ps1 -FreshRun    # archive existing tier data before starting
#   .\scripts\bankroll-start.ps1 -DryRun      # validate paths and configs without launching
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

. (Join-Path $PSScriptRoot "theme-loader.ps1")
. (Join-Path $PSScriptRoot "bankroll-procs.ps1")

Write-ProfileBanner -Title "10-TIER BANKROLL SENSITIVITY EXPERIMENTS" `
                    -Subtitle "Parallel Paper Bots from `$100 to `$1,000 in `$100 increments" `
                    -Style "Info"
Write-Host ""

Write-ProfileInfo -Message "[1/4] Checking for prior bankroll experiment instances..."

# 1. Stop any currently running bankroll experiment instances
$stopped = Stop-BankrollInstance
if ($stopped -gt 0) {
    Write-ProfileWarning -Message "Stopped prior instances:" -Detail "$stopped bankroll experiment process(es)"
}

# 2. Archive previous experiment directories if -FreshRun requested
if ($FreshRun) {
    Write-ProfileInfo -Message "[2/4] Archiving prior bankroll runs (-FreshRun active)..."
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $archive = Join-Path $ProjectPath "run/archive/bankroll_$stamp"
    New-Item -ItemType Directory -Force -Path $archive | Out-Null
    $items = Get-ChildItem -Path (Join-Path $ProjectPath "run") -Directory -Filter "bankroll_*" -ErrorAction SilentlyContinue
    foreach ($item in $items) {
        $dest = Join-Path $archive $item.Name
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            try {
                Move-Item -Path $item.FullName -Destination $dest -Force -ErrorAction Stop
                break
            } catch {
                if ($attempt -eq 3) {
                    Copy-Item -Path $item.FullName -Destination $dest -Recurse -Force -ErrorAction SilentlyContinue
                    Remove-Item -Path $item.FullName -Recurse -Force -ErrorAction SilentlyContinue
                } else {
                    Start-Sleep -Milliseconds 400
                }
            }
        }
    }
    Write-ProfileSuccess -Message "Archived prior runs to:" -Detail $archive
} else {
    Write-ProfileInfo -Message "[2/4] Preserving active experiment databases..."
}

Write-ProfileInfo -Message "[3/4] Spawning 10 isolated fleet workers (`$$Start -> `$$End in `$$Step steps)..."

# 3. Build tier configurations and launch
$tierRecords = @()

for ($amount = $Start; $amount -le $End; $amount += $Step) {
    $workdir = Join-Path $ProjectPath "run/bankroll_$amount"
    $dbPath = Join-Path $workdir "fleet.db"
    $logPath = Join-Path $workdir "fleet.log"
    $statusPath = Join-Path $workdir "status.json"

    New-Item -ItemType Directory -Force -Path $workdir | Out-Null

    if ($DryRun) {
        Write-ProfileNeutral -Message "[DRY RUN] Tier `$$amount:" -Detail $workdir
        continue
    }

    $statusData = @{
        bankroll       = $amount
        status         = "INITIALIZING"
        created_at     = [double](Get-Date -UFormat %s)
        target_samples = 100
        min_samples    = 30
        pid            = $null
        started_at     = $null
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
        $statusData["status"] = "RUNNING"
        $statusData["pid"] = $proc.Id
        $statusData["started_at"] = [double](Get-Date -UFormat %s)
        $statusData | ConvertTo-Json -Depth 4 | Set-Content -Path $statusPath -Encoding UTF8

        $tierRecords += [pscustomobject]@{
            bankroll      = $amount
            pid           = $proc.Id
            started_ticks = $proc.StartTime.ToUniversalTime().Ticks
            started       = $proc.StartTime.ToString("o")
            workdir       = $workdir
        }
        Write-ProfileNeutral -Message "Started Tier `$$amount:" -Detail "PID $($proc.Id)"
    }
}

if (-not $DryRun -and $tierRecords.Count -gt 0) {
    Write-ProfileInfo -Message "[4/4] Saving PID registry and verifying processes..."
    Save-BankrollInstance -TierRecords $tierRecords
}

Write-Host ""
Write-ProfileSuccess -Message "All 10 bankroll experiment workers are ACTIVE!" -Detail "($($tierRecords.Count) bots launched)"
Write-ProfileKeyValue -Key "Dashboard Matrix" -Value "http://127.0.0.1:8800" -Style "Link"
Write-ProfileKeyValue -Key "Stats Report" -Value "python -m scripts.bankroll_stats_report" -Style "Command"
Write-ProfileKeyValue -Key "Stop Command" -Value "bankroll-stop" -Style "Highlight"
Write-Host ""
