# Ownership of Spread Hunter processes, shared by hunter-start.ps1 and hunter-stop.ps1.
#
# Spread Hunter: Two-sided Polymarket maker strategy resting bids on BOTH outcomes
# to capture the spread and liquidity rewards while staying inventory-balanced.
#
# Ownership is RECORDED at launch in run/hunter.pids.json, and shutdown is scoped to it.
# PIDs are validated against UTC start ticks to prevent killing recycled PIDs.

$ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$HunterPidFile = Join-Path $ProjectPath "run/hunter.pids.json"
$LegacyFleetPidFile = Join-Path $ProjectPath "run/fleet.pids.json"

# Reporting only -- never a kill list. Kept so an operator can be TOLD about a
# hunter instance this script does not own, rather than silently killing it.
$HunterPatterns = "*strategy.supervisor*", "*strategy.fleet*",
                  "*scripts.rerank_loop*", "*uvicorn*server.spread_dash*",
                  "*uvicorn*server.fleet_dash*", "*scripts.watch_universe*"

# Backwards compatibility alias
$FleetPatterns = $HunterPatterns
$FleetPidFile = $HunterPidFile


function Save-HunterInstance {
    <# Record the processes we just started, with their start times. #>
    param([Parameter(Mandatory)][hashtable]$Procs)   # name -> System.Diagnostics.Process

    $records = @()
    foreach ($name in $Procs.Keys) {
        $p = $Procs[$name]
        if ($null -eq $p) { continue }
        $records += [pscustomobject]@{
            name          = $name
            pid           = $p.Id
            started_ticks = $p.StartTime.ToUniversalTime().Ticks
            started       = $p.StartTime.ToString("o")
        }
    }
    $payload = [pscustomobject]@{
        strategy = "spread-hunter"
        saved    = (Get-Date).ToString("o")
        procs    = $records
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $HunterPidFile) | Out-Null
    $json = $payload | ConvertTo-Json -Depth 4
    Set-Content -Path $HunterPidFile -Value $json -Encoding UTF8
    Set-Content -Path $LegacyFleetPidFile -Value $json -Encoding UTF8
}

function Save-FleetInstance {
    param([Parameter(Mandatory)][hashtable]$Procs)
    Save-HunterInstance -Procs $Procs
}


function Get-HunterStartTicks {
    <# A record's start time as UTC ticks, or $null if it cannot be read. #>
    param([Parameter(Mandatory)][AllowNull()][object]$Record)

    if ($null -eq $Record) { return $null }
    if ($null -ne $Record.started_ticks) {
        $ticks = [int64]0
        if ([int64]::TryParse([string]$Record.started_ticks, [ref]$ticks)) {
            return $ticks
        }
    }
    $legacy = $Record.started
    if ($null -eq $legacy) { return $null }
    if ($legacy -is [datetime]) { return $legacy.ToUniversalTime().Ticks }
    $dt = [datetime]::MinValue
    if ([datetime]::TryParse([string]$legacy, [cultureinfo]::InvariantCulture,
                             [System.Globalization.DateTimeStyles]::RoundtripKind,
                             [ref]$dt)) {
        return $dt.ToUniversalTime().Ticks
    }
    return $null
}

function Get-FleetStartTicks {
    param([Parameter(Mandatory)][AllowNull()][object]$Record)
    Get-HunterStartTicks -Record $Record
}


function Get-HunterInstance {
    <# The recorded processes that are STILL the ones we started. #>
    $pidFile = if (Test-Path $HunterPidFile) { $HunterPidFile } elseif (Test-Path $LegacyFleetPidFile) { $LegacyFleetPidFile } else { $null }
    if (-not $pidFile) { return @() }

    try {
        $data = Get-Content $pidFile -Raw | ConvertFrom-Json
    } catch {
        Write-Host "could not read $pidFile ($($_.Exception.Message))" -ForegroundColor Yellow
        return @()
    }

    $live = @()
    foreach ($r in @($data.procs)) {
        try { $p = Get-Process -Id $r.pid -ErrorAction Stop } catch { continue }
        $recorded = Get-HunterStartTicks -Record $r
        if ($null -eq $recorded -or $p.StartTime.ToUniversalTime().Ticks -ne $recorded) {
            Write-Host "pid $($r.pid) was recycled; not touching it" -ForegroundColor DarkGray
            continue
        }
        $live += [pscustomobject]@{ name = $r.name; pid = $r.pid; proc = $p }
    }
    return $live
}

function Get-FleetInstance {
    Get-HunterInstance
}


$HunterMinKillablePid = 100
$FleetMinKillablePid = $HunterMinKillablePid

function Assert-KillablePid {
    <# Return the id as an int, or throw. Never returns something unkillable. #>
    param([Parameter(Mandatory)][AllowNull()][object]$ProcessId,
          [string]$Context = "")

    $id = 0
    if ($null -eq $ProcessId -or -not [int]::TryParse([string]$ProcessId, [ref]$id)) {
        throw "Refusing to stop a non-numeric process id '$ProcessId' $Context"
    }
    if ($id -lt $HunterMinKillablePid) {
        throw ("Refusing to stop PID $id $Context -- ids below " +
               "$HunterMinKillablePid are Windows system processes, so this is a " +
               "bug in the caller rather than a Spread Hunter process.")
    }
    return $id
}


function Get-DescendantPids {
    <# Every process descending from $ParentId, deepest first. #>
    param([Parameter(Mandatory)][int]$ParentId)

    $ParentId = Assert-KillablePid -ProcessId $ParentId -Context "(descendant walk root)"

    $all = Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
        Select-Object ProcessId, ParentProcessId
    $out = @()
    $frontier = @($ParentId)
    while ($frontier.Count -gt 0) {
        $kids = $all | Where-Object { $frontier -contains $_.ParentProcessId } |
            Select-Object -ExpandProperty ProcessId
        $kids = @($kids | Where-Object { $out -notcontains $_ -and $_ -ne $ParentId })
        if ($kids.Count -eq 0) { break }
        $out += $kids
        $frontier = $kids
    }
    [array]::Reverse($out)
    return $out
}


function Stop-HunterTree {
    <# Stop one recorded process and everything below it. #>
    param([Parameter(Mandatory)][AllowNull()][object]$ProcessId, [string]$Label = "")

    $target = Assert-KillablePid -ProcessId $ProcessId -Context $Label
    foreach ($child in Get-DescendantPids -ParentId $target) {
        $safe = Assert-KillablePid -ProcessId $child -Context "(child of $target)"
        Write-Host "  stopping child PID $safe" -ForegroundColor DarkYellow
        Stop-Process -Id $safe -Force -ErrorAction SilentlyContinue
    }
    Write-Host "stopping PID $target $Label" -ForegroundColor Yellow
    Stop-Process -Id $target -Force -ErrorAction SilentlyContinue
}

function Stop-FleetTree {
    param([Parameter(Mandatory)][AllowNull()][object]$ProcessId, [string]$Label = "")
    Stop-HunterTree -ProcessId $ProcessId -Label $Label
}


function Stop-HunterInstance {
    <# Stop the recorded Spread Hunter instance. Returns the number of trees stopped. #>
    $live = @(Get-HunterInstance)
    if ($live.Count -eq 0) { return 0 }

    $ordered = @($live | Where-Object { $_.name -eq "supervisor" }) +
               @($live | Where-Object { $_.name -ne "supervisor" })
    foreach ($r in $ordered) {
        Stop-HunterTree -ProcessId $r.pid -Label "($($r.name))"
        Start-Sleep -Milliseconds 300
    }

    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline -and @(Get-HunterInstance).Count -gt 0) {
        Start-Sleep -Milliseconds 300
    }
    if (@(Get-HunterInstance).Count -gt 0) {
        throw "Spread Hunter processes did not stop before the deadline."
    }
    Remove-Item $HunterPidFile -ErrorAction SilentlyContinue
    Remove-Item $LegacyFleetPidFile -ErrorAction SilentlyContinue
    return $ordered.Count
}

function Stop-FleetInstance {
    Stop-HunterInstance
}


function Find-HunterStrays {
    <# Spread Hunter shaped processes this script does not own. REPORTING ONLY. #>
    $ownedPids = @()
    foreach ($r in @(Get-HunterInstance)) {
        $ownedPids += $r.pid
        $ownedPids += @(Get-DescendantPids -ParentId $r.pid)
    }
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
        Where-Object {
            $cl = $_.CommandLine
            $cl -and ($ownedPids -notcontains $_.ProcessId) -and
            ($HunterPatterns | Where-Object { $cl -like $_ })
        }
}

function Find-FleetStrays {
    Find-HunterStrays
}
