# Ownership of Bankroll Sensitivity Experiment processes ($100 to $1,000 tiers).
# Shared by bankroll-start.ps1 and bankroll-stop.ps1.

$ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BankrollPidFile = Join-Path $ProjectPath "run/bankroll.pids.json"
$BankrollPattern = "*strategy.fleet*"

. (Join-Path $PSScriptRoot "theme-loader.ps1")

function Test-PortListening {
    param([int]$Port = 8800)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $success = $iar.AsyncWaitHandle.WaitOne(800, $false)
        if ($success -and $client.Connected) {
            $client.EndConnect($iar)
            $client.Close()
            return $true
        }
        $client.Close()
        return $false
    } catch {
        return $false
    }
}

function Save-BankrollInstance {
    <# Record all tier processes and optional dashboard process started for bankroll experiments. #>
    param(
        [Parameter(Mandatory, Position = 0)]
        [Alias("Procs")]
        [array]$TierRecords,
        [Parameter(Position = 1)]
        [object]$DashProcess = $null
    )

    $dashRecord = $null
    if ($DashProcess) {
        try {
            if (-not $DashProcess.HasExited) {
                $dashRecord = [pscustomobject]@{
                    pid           = $DashProcess.Id
                    started_ticks = $DashProcess.StartTime.ToUniversalTime().Ticks
                    started       = $DashProcess.StartTime.ToString("o")
                }
            }
        } catch {}
    }

    $payload = [pscustomobject]@{
        experiment = "bankroll-sensitivity-10-tier"
        saved      = (Get-Date).ToString("o")
        tiers      = $TierRecords
        dash       = $dashRecord
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
        Write-ProfileWarning -Message "Could not read bankroll PID file:" -Detail "$BankrollPidFile ($($_.Exception.Message))"
        return @()
    }

    $live = @()
    foreach ($r in @($data.tiers)) {
        try { $p = Get-Process -Id $r.pid -ErrorAction Stop } catch { continue }
        if ($null -ne $r.started_ticks) {
            $pStartTime = $null
            try { $pStartTime = $p.StartTime } catch {}
            if ($null -eq $pStartTime -or $pStartTime.ToUniversalTime().Ticks -ne [int64]$r.started_ticks) {
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

function Get-BankrollDashInstance {
    <# Retrieve active dashboard process if tracked in bankroll PID file. #>
    if (-not (Test-Path $BankrollPidFile)) { return $null }
    try {
        $data = Get-Content $BankrollPidFile -Raw | ConvertFrom-Json
        if ($data.dash -and $data.dash.pid) {
            $p = Get-Process -Id $data.dash.pid -ErrorAction Stop
            if ($null -ne $data.dash.started_ticks) {
                $pStartTime = $null
                try { $pStartTime = $p.StartTime } catch {}
                if ($null -ne $pStartTime -and $pStartTime.ToUniversalTime().Ticks -eq [int64]$data.dash.started_ticks) {
                    return $p
                }
            } else {
                return $p
            }
        }
    } catch {}
    return $null
}

function Stop-BankrollInstance {
    <# Stop all running bankroll experiment processes and dashboard server. #>
    $live = @(Get-BankrollInstance)
    $stoppedCount = 0

    foreach ($r in $live) {
        Write-ProfileNeutral -Message "Stopping Tier `$$($r.bankroll):" -Detail "PID $($r.pid)"
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

    # Stop tracked dashboard server if started by bankroll
    $dashProc = Get-BankrollDashInstance
    if ($dashProc) {
        Write-ProfileNeutral -Message "Stopping Bankroll Dashboard Server:" -Detail "PID $($dashProc.Id)"
        Stop-Process -Id $dashProc.Id -Force -ErrorAction SilentlyContinue
        $stoppedCount++
    }

    # Also inspect all run/bankroll_*/status.json in case pid file was missed
    $runDir = Join-Path $ProjectPath "run"
    if (Test-Path $runDir) {
        Get-ChildItem -Path $runDir -Directory -Filter "bankroll_*" | ForEach-Object {
            $statusFile = Join-Path $_.FullName "status.json"
            if (Test-Path $statusFile) {
                try {
                    $st = Get-Content $statusFile -Raw | ConvertFrom-Json
                    if ($st.pid) {
                        try {
                            $p = Get-Process -Id $st.pid -ErrorAction Stop
                            if ($p.ProcessName -match "python") {
                                Stop-Process -Id $st.pid -Force -ErrorAction SilentlyContinue
                                $stoppedCount++
                            }
                        } catch {}
                    }
                    $st.status = "STOPPED"
                    $st.stopped_at = [double](Get-Date -UFormat %s)
                    $st | ConvertTo-Json -Depth 4 | Set-Content -Path $statusFile -Encoding UTF8
                } catch {}
            }
        }
    }

    # Clean up any unowned strays executing bankroll fleet
    $strays = @(Find-BankrollStrays)
    foreach ($stray in $strays) {
        try {
            Stop-Process -Id $stray.ProcessId -Force -ErrorAction SilentlyContinue
            $stoppedCount++
        } catch {}
    }

    Remove-Item $BankrollPidFile -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
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
            ($cl -match "strategy\.fleet" -or $cl -match "bankroll_")
        }
}

function Show-BankrollStatus {
    $inst = @(Get-BankrollInstance)
    $dash = Get-BankrollDashInstance
    if ($inst.Count -gt 0) {
        Write-ProfileSuccess -Message "Active Bankroll Experiment Bots" -Detail "$($inst.Count) tiers running"
        $inst | ForEach-Object {
            Write-ProfileKeyValue -Key "Tier `$$($_.bankroll)" -Value "PID $($_.pid) ($($_.workdir))" -Style "Info"
        }
        if ($dash) {
            Write-ProfileKeyValue -Key "Dashboard Server" -Value "PID $($dash.Id) (http://127.0.0.1:8800)" -Style "Link"
        }
    } else {
        Write-ProfileError -Message "No active bankroll experiment bots running." -Suggestion "Run option 5 or 'bankroll-start' to launch the 10-tier experiments."
    }
}

if ($MyInvocation.InvocationName -ne '.' -and $MyInvocation.InvocationName -ne '&' -and $MyInvocation.Line -like "*bankroll-procs*") {
    Show-BankrollStatus
}
