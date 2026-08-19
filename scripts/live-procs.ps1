# Ownership of the Live Execution Dashboard process, shared by live-start.ps1 and live-stop.ps1.
#
# The live dashboard is a SINGLE uvicorn process: `python -m dash.live_dash --port 8799`
# launched from live/. Ownership is RECORDED at launch in run/live-dash.pids.json and
# shutdown is scoped to it. The PID is validated against its UTC start ticks so we never
# kill a recycled PID (mirrors hunter-procs.ps1 / bankroll-procs.ps1).

$ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LivePidFile = Join-Path $ProjectPath "run/live-dash.pids.json"
$LivePort    = 8799

. (Join-Path $PSScriptRoot "theme-loader.ps1")

function Save-LiveDashInstance {
    <# Record the launched dashboard process with its start time. #>
    param([Parameter(Mandatory)]$DashProcess)

    $record = $null
    if ($DashProcess -and -not $DashProcess.HasExited) {
        $record = [pscustomobject]@{
            pid           = $DashProcess.Id
            started_ticks = $DashProcess.StartTime.ToUniversalTime().Ticks
            started       = $DashProcess.StartTime.ToString("o")
            port          = $LivePort
        }
    }
    $payload = [pscustomobject]@{
        strategy = "spread-hunter-live-dash"
        saved    = (Get-Date).ToString("o")
        dash     = $record
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $LivePidFile) | Out-Null
    $payload | ConvertTo-Json -Depth 4 | Set-Content -Path $LivePidFile -Encoding UTF8
}

function Get-LiveDashInstance {
    <# The recorded dashboard process that is STILL the one we started. #>
    if (-not (Test-Path $LivePidFile)) { return $null }
    try {
        $data = Get-Content $LivePidFile -Raw | ConvertFrom-Json
    } catch {
        Write-ProfileWarning -Message "Could not read live dashboard PID file:" -Detail "$LivePidFile ($($_.Exception.Message))"
        return $null
    }

    $d = $data.dash
    if (-not $d -or -not $d.pid) { return $null }
    try { $p = Get-Process -Id $d.pid -ErrorAction Stop } catch { return $null }

    if ($null -ne $d.started_ticks) {
        $pStart = $null
        try { $pStart = $p.StartTime } catch {}
        if ($null -eq $pStart -or $pStart.ToUniversalTime().Ticks -ne [int64]$d.started_ticks) {
            return $null
        }
    }
    return [pscustomobject]@{
        pid  = $p.Id
        proc = $p
        port = if ($d.port) { $d.port } else { $LivePort }
    }
}

function Stop-LiveDashInstance {
    <# Stop the recorded live dashboard process (if still the one we started). #>
    $inst = Get-LiveDashInstance
    if ($null -ne $inst) {
        Write-ProfileNeutral -Message "Stopping Live Dashboard:" -Detail "PID $($inst.pid) (port $($inst.port))"
        Stop-Process -Id $inst.pid -Force -ErrorAction SilentlyContinue

        $deadline = (Get-Date).AddSeconds(10)
        while ((Get-Date) -lt $deadline -and $null -ne (Get-LiveDashInstance)) {
            Start-Sleep -Milliseconds 300
        }
    }
    # Mirrors hunter-procs/bankroll-procs: clear the record even if nothing was running.
    Remove-Item $LivePidFile -ErrorAction SilentlyContinue
    return $(if ($null -ne $inst) { 1 } else { 0 })
}

function Show-LiveDashStatus {
    $inst = Get-LiveDashInstance
    if ($null -ne $inst) {
        Write-ProfileSuccess -Message "Live Execution Dashboard Active" -Detail "PID $($inst.pid)"
        Write-ProfileKeyValue -Key "Live Dashboard" -Value "http://127.0.0.1:$($inst.port)" -Style "Link" -KeyWidth 20
    } else {
        Write-ProfileError -Message "No live execution dashboard running." -Suggestion "Run option 12 or 'live-start' to launch it."
    }
}

if ($MyInvocation.InvocationName -ne '.' -and $MyInvocation.InvocationName -ne '&' -and $MyInvocation.Line -like "*live-procs*") {
    Show-LiveDashStatus
}
