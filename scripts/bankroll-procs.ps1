# Ownership of Bankroll Sensitivity Experiment processes ($100 to $1,000 tiers).
# Shared by bankroll-start.ps1 and bankroll-stop.ps1.
#
# Records PIDs in run/bankroll.pids.json and validates start times to prevent
# killing recycled PIDs.

$ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BankrollPidFile = Join-Path $ProjectPath "run/bankroll.pids.json"
$BankrollPattern = "*strategy.fleet*"

function Save-BankrollInstance {
    <# Record all tier processes started for bankroll experiments. #>
    param(
        [Parameter(Mandatory, Position = 0)]
        [Alias("Procs")]
        [array]$TierRecords
    )

    $payload = [pscustomobject]@{
        experiment = "bankroll-sensitivity-10-tier"
        saved      = (Get-Date).ToString("o")
        tiers      = $TierRecords
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $BankrollPidFile) | Out-Null
    $payload | ConvertTo-Json -Depth 4 | Set-Content -Path $BankrollPidFile -Encoding UTF8
}

function Get-BankrollInstance {
    <# Retrieve all active bankroll experiment tier processes. #>
    if (-not (Test-Path $BankrollPidFile)) { return @() }
    try {
        $data = Get-Content $BankrollPidFile -Raw | ConvertFrom-Json
    } catch {
        Write-Host "could not read $BankrollPidFile ($($_.Exception.Message))" -ForegroundColor Yellow
        return @()
    }

    $live = @()
    foreach ($r in @($data.tiers)) {
        try { $p = Get-Process -Id $r.pid -ErrorAction Stop } catch { continue }
        if ($null -ne $r.started_ticks) {
            if ($p.StartTime.ToUniversalTime().Ticks -ne [int64]$r.started_ticks) {
                continue
            }
        }
        $live += [pscustomobject]@{
            bankroll = $r.bankroll
            pid      = $r.pid
            workdir  = $r.workdir
            proc     = $p
        }
    }
    return $live
}

function Stop-BankrollInstance {
    <# Stop all 10 running bankroll experiment processes and update status.json. #>
    $live = @(Get-BankrollInstance)
    $stoppedCount = 0

    foreach ($r in $live) {
        Write-Host "Stopping Tier `$$($r.bankroll) (PID $($r.pid))..." -ForegroundColor Yellow
        Stop-Process -Id $r.pid -Force -ErrorAction SilentlyContinue
        $stoppedCount++

        # Update tier status.json
        if ($r.workdir -and (Test-Path $r.workdir)) {
            $statusFile = Join-Path $r.workdir "status.json"
            if (Test-Path $statusFile) {
                try {
                    $st = Get-Content $statusFile -Raw | ConvertFrom-Json
                    $st.status = "STOPPED"
                    $st.stopped_at = [double](Get-Date -UFormat %s)
                    $st | ConvertTo-Json -Depth 4 | Set-Content -Path $statusFile -Encoding UTF8
                } catch {}
            }
        }
    }

    # Also inspect all run/bankroll_*/status.json in case pid file was missed
    $runDir = Join-Path $ProjectPath "run"
    if (Test-Path $runDir) {
        Get-ChildItem -Path $runDir -Directory -Filter "bankroll_*" | ForEach-Object {
            $statusFile = Join-Path $_.FullName "status.json"
            if (Test-Path $statusFile) {
                try {
                    $st = Get-Content $statusFile -Raw | ConvertFrom-Json
                    if ($st.status -eq "RUNNING" -and $st.pid) {
                        try {
                            $p = Get-Process -Id $st.pid -ErrorAction Stop
                            if ($p.ProcessName -match "python") {
                                Stop-Process -Id $st.pid -Force -ErrorAction SilentlyContinue
                                $stoppedCount++
                            }
                        } catch {}
                        $st.status = "STOPPED"
                        $st.stopped_at = [double](Get-Date -UFormat %s)
                        $st | ConvertTo-Json -Depth 4 | Set-Content -Path $statusFile -Encoding UTF8
                    }
                } catch {}
            }
        }
    }

    Remove-Item $BankrollPidFile -ErrorAction SilentlyContinue
    return $stoppedCount
}

function Find-BankrollStrays {
    <# Find python processes executing strategy.fleet that are not tracked. #>
    $ownedPids = @()
    foreach ($r in @(Get-BankrollInstance)) {
        $ownedPids += $r.pid
    }
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
        Where-Object {
            $cl = $_.CommandLine
            $cl -and ($ownedPids -notcontains $_.ProcessId) -and
            ($cl -like "*strategy.fleet*" -and $cl -like "*bankroll_*")
        }
}
