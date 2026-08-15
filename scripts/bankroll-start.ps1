# 10-TIER BANKROLL SENSITIVITY EXPERIMENT LAUNCHER
# Starts 10 simultaneous paper-trading bots with isolated workdirs ($100 to $1,000 in $100 steps).
# Serves the Spread Hunter dashboard on port 8800 and automatically opens it in the browser.
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
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectPath "logs") | Out-Null

. (Join-Path $PSScriptRoot "theme-loader.ps1")
. (Join-Path $PSScriptRoot "bankroll-procs.ps1")

$modeFlags = @()
if ($FreshRun) { $modeFlags += "-FreshRun (Archive Runs)" }
if ($DryRun) { $modeFlags += "-DryRun" }
$modeSubtitle = if ($modeFlags.Count -gt 0) { ($modeFlags -join " | ") } else { "Standard Run (Preserve Runs)" }

Write-ProfileBanner -Title "10-TIER BANKROLL SENSITIVITY EXPERIMENTS" `
                    -Subtitle "Parallel Paper Bots (`$$Start-`$$End in `$$Step steps) | Mode: $modeSubtitle" `
                    -Style "Info"
Write-Host ""

Write-ProfileInfo -Message "[1/5] Checking for prior bankroll experiment instances..." -Detail "(FreshRun = $FreshRun, DryRun = $DryRun)"

# 1. Stop any currently running bankroll experiment instances
$stopped = Stop-BankrollInstance
if ($stopped -gt 0) {
    Write-ProfileWarning -Message "Stopped prior instances:" -Detail "$stopped bankroll experiment process(es)"
}

# 2. Archive previous experiment directories if -FreshRun requested
if ($FreshRun) {
    Write-ProfileInfo -Message "[2/5] Archiving prior bankroll runs (-FreshRun active)..."
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
    Write-ProfileInfo -Message "[2/5] Preserving active experiment databases..."
}

Write-ProfileInfo -Message "[3/5] Spawning 10 isolated fleet workers (`$$Start -> `$$End in `$$Step steps)..."

# 3. Build tier configurations and launch
$tierRecords = @()

for ($amount = $Start; $amount -le $End; $amount += $Step) {
    $workdir = Join-Path $ProjectPath "run/bankroll_$amount"
    $dbPath = Join-Path $workdir "fleet.db"
    $logPath = Join-Path $workdir "fleet.log"
    $statusPath = Join-Path $workdir "status.json"

    New-Item -ItemType Directory -Force -Path $workdir | Out-Null

    if ($DryRun) {
        Write-ProfileNeutral -Message "[DRY RUN] Tier `$$($amount):" -Detail $workdir
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
        Write-ProfileNeutral -Message "Started Tier `$$($amount):" -Detail "PID $($proc.Id)"
    }
}

# 4. Check / Start Dashboard Server on Port 8800
$dashProc = $null
Write-ProfileInfo -Message "[4/5] Checking dashboard server availability on port 8800..."

if (Test-PortListening 8800) {
    Write-ProfileInfo -Message "Dashboard server is already active on port 8800."
} elseif (-not $DryRun) {
    Write-ProfileInfo -Message "Launching dashboard server on http://127.0.0.1:8800..."
    $dashPsi = New-Object System.Diagnostics.ProcessStartInfo
    $dashPsi.FileName = "python"
    $dashPsi.Arguments = "-m uvicorn server.spread_dash:app --host 127.0.0.1 --port 8800 --reload --reload-dir server"
    $dashPsi.WorkingDirectory = $ProjectPath
    $dashPsi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $dashPsi.CreateNoWindow = $true
    $dashPsi.UseShellExecute = $false
    $dashPsi.RedirectStandardOutput = $true
    $dashPsi.RedirectStandardError = $true

    $dashProc = [System.Diagnostics.Process]::Start($dashPsi)

    # Wait up to 5s for port 8800 to be live
    for ($i = 0; $i -lt 10; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-PortListening 8800) { break }
    }
}

if (-not $DryRun -and $tierRecords.Count -gt 0) {
    Write-ProfileInfo -Message "[5/5] Saving PID registry and verifying processes..."
    Save-BankrollInstance -TierRecords $tierRecords -DashProcess $dashProc
}

Write-Host ""
Write-ProfileSuccess -Message "All 10 bankroll experiment workers are ACTIVE!" -Detail "($($tierRecords.Count) bots launched)"
Write-ProfileKeyValue -Key "Dashboard Matrix" -Value "http://127.0.0.1:8800" -Style "Link" -KeyWidth 20
Write-ProfileKeyValue -Key "Stats Report" -Value "python -m scripts.bankroll_stats_report" -Style "Command" -KeyWidth 20
Write-ProfileKeyValue -Key "Stop Command" -Value "bankroll-stop" -Style "Highlight" -KeyWidth 20
Write-Host ""

# 5. Open dashboard in default browser
if (-not $DryRun) {
    try {
        Start-Process "http://127.0.0.1:8800"
        Write-ProfileSuccess -Message "Opened http://127.0.0.1:8800 in default browser."
    } catch {
        Write-ProfileWarning -Message "Could not open browser automatically:" -Detail "Navigate to http://127.0.0.1:8800"
    }
}
Write-Host ""
