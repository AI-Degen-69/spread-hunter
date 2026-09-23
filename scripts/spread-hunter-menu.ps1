# SPREAD HUNTER - CONTROL CENTER
# Standalone menu for the spread hunter execution engine
# (C:\Users\Tiger\Agents\Projects\spread-hunter).
#
# Usage:
#   .\scripts\spread-hunter-menu.ps1          # interactive menu (press 1-9/q, no Enter; one choice, then exits)
#   .\scripts\spread-hunter-menu.ps1 8        # run menu option 8 directly (1-9, q all work)
#   .\scripts\spread-hunter-menu.ps1 start -Yes    # 1 · LIVE: preflight-stop + wipe, fresh bot + dashboard (real bids)
#   .\scripts\spread-hunter-menu.ps1 stop          # 2 · LIVE: stop bot + dashboard
#   .\scripts\spread-hunter-menu.ps1 host          # 3 · LIVE: release :8799 from the other menu-owned dashboard (no wipe), host live & open
#   .\scripts\spread-hunter-menu.ps1 shadow-run [-Minutes N] [-Watch] # 4 · SHADOW: preflight + wipe, full rehearsal (loop + stop loss); -Watch streams the loop log live
#   .\scripts\spread-hunter-menu.ps1 statistical-run [-Hours N] # overnight shadow statistics + dashboard
#   .\scripts\spread-hunter-menu.ps1 stop-shadow   # 5 · SHADOW: stop loop, watcher and viewer
#   .\scripts\spread-hunter-menu.ps1 open-shadow   # 6 · SHADOW: release :8799 from the other menu-owned dashboard (no wipe), host shadow & open
#   .\scripts\spread-hunter-menu.ps1 shadow-resume [-Minutes N] # R · SHADOW: resume the pinned 01_shadow_12-09_00-58.db rehearsal (shadow-01) in place (no wipe) & reattach dashboard
#   .\scripts\spread-hunter-menu.ps1 clean         # 7 · GLOBAL: kill all + wipe data + verify (no start)
#   .\scripts\spread-hunter-menu.ps1 status        # 8 · status page
# the same code path as the dashboard's START/STOP buttons (interprocess lock,
# starting-capital snapshot, shared run_id). The dashboard process itself is
# owned by this script via runtime/live-dash.pids.json.
#
# NOTE: "start" launches Decide & Execute, which rests REAL maker bids. That is
# an opening command and requires explicit supervision (AGENTS.md).

#Requires -Version 7.0
# PowerShell 7 (pwsh) is required. This file is UTF-8 without a BOM and
# contains box-drawing characters that Windows PowerShell 5.1 misdecodes.

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Action = "",
    [switch]$Yes,
    [int]$Minutes = 0,
    [double]$Hours = 0,
    [switch]$Watch,
    [string]$ResumeDb = ""
)

$ErrorActionPreference = "Stop"
$ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Port    = 8799
$LivePort     = $Port
$DashUrl     = "http://127.0.0.1:$Port"
$RunDir      = Join-Path $ProjectPath "runtime"
$LegacyRunDir = Join-Path $ProjectPath "run"

# The pinned rehearsal: every shadow-resume reopens this store under run id
# shadow-01 (Owner's standing choice, 2026-09-23). -ResumeDb overrides it for
# a single launch.
$script:DefaultResumeDb = Join-Path $ProjectPath "data/01_shadow_12-09_00-58.db"

# Runtime state moved run/ -> runtime/ and some files were renamed with it. A
# stack started before that move still records itself under the old names, and
# a menu that cannot see it prints STOPPED while real bids are resting. Read
# through this: current path when it exists, pre-rename path while only that
# one does, current path otherwise. Mirrors core_brain/runtime_paths.py.
function Resolve-RuntimeFile {
    param(
        [Parameter(Mandatory)][string]$Name,
        [string]$LegacyName
    )
    $current = Join-Path $RunDir $Name
    if (Test-Path $current) { return $current }
    if (-not $LegacyName) { $LegacyName = $Name }
    $legacy = Join-Path $LegacyRunDir $LegacyName
    if (Test-Path $legacy) { return $legacy }
    return $current
}

# --- rehearsal-only trial knobs ---------------------------------------------
# core_brain/config.py's resolve_pair_cost_trial RAISES unless the process
# declared itself a rehearsal, and only core_brain.shadow_run does.
# Start-Process copies this menu's whole environment into every child, so
# HUNTER_PAIR_COST_CAP -- exported to steer a rehearsal -- also lands in the
# stats observer, whose config.load() then dies at import.
#
# Capture and strip it from THIS process now; Invoke-WithRehearsalTrialEnv
# re-injects it for the shadow_run launch alone. (HUNTER_WIDE_BOOK_TRIAL is
# NOT handled here: it reaches the screener through the ranker's own
# --trial-spread flag, not this menu, and the dashboard tolerates it via
# config.load(for_display=True).)
$script:RehearsalTrialEnv = @{}
foreach ($k in @('HUNTER_PAIR_COST_CAP')) {
    $v = [Environment]::GetEnvironmentVariable($k)
    if ($null -ne $v -and $v -ne '') {
        $script:RehearsalTrialEnv[$k] = $v
        Remove-Item "Env:$k" -ErrorAction SilentlyContinue
    }
}

function Invoke-WithRehearsalTrialEnv {
    <# Run $Action with the rehearsal-only trial knobs restored to the
       environment, then strip them again so no later child inherits them.
       Only core_brain.shadow_run, the one child that declares itself a
       rehearsal, should run inside this. #>
    param([Parameter(Mandatory)][scriptblock]$Action)
    foreach ($k in $script:RehearsalTrialEnv.Keys) {
        Set-Item "Env:$k" $script:RehearsalTrialEnv[$k]
    }
    try { & $Action }
    finally {
        foreach ($k in $script:RehearsalTrialEnv.Keys) {
            Remove-Item "Env:$k" -ErrorAction SilentlyContinue
        }
    }
}

# The dashboard PID file is menu-owned and rewritten on every start, so it is
# not resolved: a stale pre-rename copy only ever meant "not owned by menu".
$DashPidFile = Join-Path $RunDir "live-dash.pids.json"
$ShadowPidFile = Join-Path $RunDir "shadow-dash.pids.json"
$ShadowDbPath  = $null
$StatsDbPath   = $null
$ShadowRunId   = $null
$ShadowPort    = 8799
$ShadowDashUrl = "http://127.0.0.1:$ShadowPort"
$ShadowOutLog  = Join-Path $RunDir "shadow_dash.out.log"
$ShadowErrLog  = Join-Path $RunDir "shadow_dash.err.log"

# Shadow dashboard ports derive from the run id (#288): shadow-01 -> 8801,
# shadow-02 -> 8802. :8799 is live-only and is never derived here. The
# unnumbered "shadow-resume" fallback id gets :8899 -- off the live port and
# outside any numbered instance. Anything else fails loudly rather than
# silently landing back beside the live stack.
function Get-ShadowDashPort {
    param([Parameter(Mandatory)][string]$RunId)
    if ($RunId -match '^shadow-(\d{1,2})$') {
        return 8800 + [int]$Matches[1]
    }
    if ($RunId -eq "shadow-resume") {
        return 8899
    }
    throw "Cannot derive a shadow dashboard port from run id '$RunId' (expected 'shadow-NN'). Refusing rather than reusing the live dashboard port."
}
function Get-ShadowDashUrl {
    <# The single builder of shadow dashboard URLs -- every open-browser and
       status call site uses this so none can disagree about where an
       instance lives. #>
    param([Parameter(Mandatory)][string]$RunId)
    return "http://127.0.0.1:$(Get-ShadowDashPort $RunId)"
}
function Get-ShadowDashPidFile {
    param([Parameter(Mandatory)][string]$RunId)
    return Join-Path $RunDir "shadow-dash-$RunId.pids.json"
}
function Get-ShadowDashLogs {
    param([Parameter(Mandatory)][string]$RunId)
    return @{ out = (Join-Path $RunDir "shadow_dash_$RunId.out.log"); err = (Join-Path $RunDir "shadow_dash_$RunId.err.log") }
}
function Open-ShadowDashboard {
    <# Open the dashboard browser for one shadow instance. Returns the URL. #>
    param([string]$RunId = $script:ShadowRunId)
    $url = Get-ShadowDashUrl $RunId
    Start-Process $url
    return $url
}
function Get-ShadowSessionFile {
    <# Session record per run id; empty id falls back to the pre-#288 single
       file so a stale record is still found and pruned, never orphaned. #>
    param([string]$RunId = $script:ShadowRunId)
    if (-not $RunId) { return $ShadowSessionFile }
    return Join-Path $RunDir "shadow-session-$RunId.json"
}
$ProcsFile   = Resolve-RuntimeFile -Name "processes.json" -LegacyName "live_procs.json"
$OutLog      = Join-Path $RunDir "live_dash.out.log"
$ErrLog      = Join-Path $RunDir "live_dash.err.log"
$HbFile      = Resolve-RuntimeFile -Name "global_stop_loss_heartbeat.json" -LegacyName "guardrail_watch_heartbeat.json"
$ShadowSessionFile = Join-Path $RunDir "shadow-session.json"

# `started_at` in the process file is a NUMERIC Unix timestamp -- start_bot()
# writes `time.time()`. [datetime]::Parse() throws on that, and the stop path
# below treats a throw as "skip the kill" and then deletes the process file, so
# a live stack would be orphaned with no registry left to find it by.
function ConvertTo-RecordedStart {
    <# The recorded process start as a DateTime, or $null when unusable. #>
    param($StartedAt)
    if ($null -eq $StartedAt -or "$StartedAt" -eq "") { return $null }
    $numeric = 0.0
    if ([double]::TryParse("$StartedAt", [ref]$numeric)) {
        return [DateTimeOffset]::FromUnixTimeMilliseconds([int64]($numeric * 1000)).LocalDateTime
    }
    try { return [datetime]::Parse("$StartedAt") } catch { return $null }
}

# Process keys were renamed with the file. Read both names.
$LegacyServiceKeys = @{ filter = "screener"; query = "engine"; decide = "fleet" }

function Get-ServiceEntry {
    <# The recorded process entry for a service, accepting its pre-rename key. #>
    param(
        [Parameter(Mandatory)]$Saved,
        [Parameter(Mandatory)][string]$Key
    )
    if (-not $Saved) { return $null }
    $entry = $Saved.$Key
    if ($entry) { return $entry }
    $legacyKey = $LegacyServiceKeys[$Key]
    if ($legacyKey) { return $Saved.$legacyKey }
    return $null
}

# The dashboard reads runtime/processes.json to decide whether Market Scan is
# running -- it never looks at shadow-session.json. A rehearsal that starts
# filter_loop without registering it here leaves a stale PID in the file, and
# the dashboard then reports "Market Scan stopped" while a healthy screener is
# scanning every ten minutes. Merge rather than overwrite: the same file also
# carries starting_account_value, which the dashboard needs for the PnL header.
function Register-StackService {
    <# Record a running stack process in processes.json under its service key. #>
    param(
        [Parameter(Mandatory)][string]$Key,
        [Parameter(Mandatory)]$Process
    )
    if (-not $Process) { return }
    $saved = [ordered]@{}
    if (Test-Path $ProcsFile) {
        try {
            $existing = Get-Content $ProcsFile -Raw | ConvertFrom-Json
            foreach ($prop in $existing.PSObject.Properties) { $saved[$prop.Name] = $prop.Value }
        } catch {
            # An unreadable registry is replaced, never inherited: a half-written
            # file would otherwise keep the dashboard on a dead PID forever.
            $saved = [ordered]@{}
        }
    }
    $startedAt = [DateTimeOffset]::new($Process.StartTime.ToUniversalTime()).ToUnixTimeMilliseconds() / 1000.0
    $saved[$Key] = [ordered]@{ pid = $Process.Id; started_at = $startedAt }
    # The pre-rename key would otherwise shadow the fresh one in Get-ServiceEntry.
    $legacyKey = $LegacyServiceKeys[$Key]
    if ($legacyKey) { $saved.Remove($legacyKey) | Out-Null }
    # Write-then-rename, never Set-Content over the live path. The dashboard
    # polls this same file, and a direct write is visible to it half-finished:
    # start_bot() treats a truncated registry as permission to launch a second
    # stack. Move-Item -Force on the same directory is a single rename, so a
    # reader sees either the old file or the new one.
    $tmp = "$ProcsFile.$PID.tmp"
    try {
        $saved | ConvertTo-Json -Depth 5 | Set-Content -Path $tmp -Encoding UTF8
        Move-Item -LiteralPath $tmp -Destination $ProcsFile -Force -ErrorAction Stop
    } catch {
        Lsh-Warn "Could not register '$Key' (PID $($Process.Id)) in $ProcsFile; the dashboard may report it stopped."
    } finally {
        if (Test-Path $tmp) { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }
}

# Module map: each stack process -> its source file (relative to the repo root).
$StackPaths = @{
    filter     = "scripts/filter_loop.py"
    query      = "core_brain/order_manager.py"
    decide     = "core_brain/trader_loop.py"
    guardrail  = "scripts/global_stop_loss.py"
    dash       = "dashboard/server.py"
    shadowDash = "dashboard/server.py"
}

$StackCmds = @{
    filter     = "python -m scripts.filter_loop"
    query      = "python -m core_brain.order_manager poll --interval 0.5"
    decide     = "python -m core_brain.trader_loop --live --no-reconcile --no-sweep --interval 5"
    guardrail  = "python -m scripts.global_stop_loss"
    dash       = "python -m dashboard.server --port 8799"
    shadowDash = "python -m dashboard.server --db <per-run-shadow-db> --port 8799"
}

# ── Theme system (shared profile templates, self-contained fallback) ──
# Dot-source the profile theme system (PowerShell 7 Theme folder) when installed
# - the same Write-Profile* templates the sim checkout's scripts use. When it is
# absent, define local ASCII-safe equivalents so this script stays standalone.
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}
$ThemeColorPath = "C:\Program Files\PowerShell\7\scripts\Theme\Theme-ColorSystem.ps1"
$ThemeTplPath   = "C:\Program Files\PowerShell\7\scripts\Theme\Theme-Templates.ps1"
if ((Test-Path $ThemeColorPath) -and (Test-Path $ThemeTplPath)) {
    try { . $ThemeColorPath; . $ThemeTplPath } catch {}
}
if (-not (Get-Command Write-ProfileSuccess -ErrorAction SilentlyContinue)) {
    function Get-ProfileColor {
        param([string]$Name)
        switch ($Name) {
            "Success"  { [ConsoleColor]::Green }
            "Error"    { [ConsoleColor]::Red }
            "Warning"  { [ConsoleColor]::Yellow }
            "Info"     { [ConsoleColor]::Cyan }
            "Neutral"  { [ConsoleColor]::DarkGray }
            "Strong"   { [ConsoleColor]::White }
            "Highlight"{ [ConsoleColor]::Yellow }
            "Border"   { [ConsoleColor]::DarkCyan }
            "Path"     { [ConsoleColor]::Green }
            "Link"     { [ConsoleColor]::Blue }
            "Command"  { [ConsoleColor]::Cyan }
            "Argument" { [ConsoleColor]::DarkYellow }
            "Danger"   { [ConsoleColor]::DarkRed }
            "Value"    { [ConsoleColor]::DarkCyan }
            "Progress" { [ConsoleColor]::DarkGray }
            "Text"     { [ConsoleColor]::Gray }
            default    { [ConsoleColor]::Gray }
        }
    }
    function Write-ProfileBanner {
        param([string]$Title, [string]$Subtitle = "", [string]$Style = "Info")
        Write-Host ("=" * 80) -ForegroundColor (Get-ProfileColor -Name Border)
        Write-Host $Title -ForegroundColor (Get-ProfileColor -Name Info)
        if ($Subtitle) { Write-Host $Subtitle -ForegroundColor (Get-ProfileColor -Name Neutral) }
        Write-Host ("=" * 80) -ForegroundColor (Get-ProfileColor -Name Border)
        Write-Host ""
    }
    function Write-ProfileSuccess { param([string]$Message, [string]$Detail = "") $t = if ($Detail) { "$Message $Detail" } else { $Message }; Write-Host ("  [OK] " + $t) -ForegroundColor (Get-ProfileColor -Name Success) }
    function Write-ProfileError   { param([string]$Message, [string]$Detail = "") $t = if ($Detail) { "$Message $Detail" } else { $Message }; Write-Host ("  [FAIL] " + $t) -ForegroundColor (Get-ProfileColor -Name Error) }
    function Write-ProfileWarning { param([string]$Message, [string]$Detail = "") $t = if ($Detail) { "$Message $Detail" } else { $Message }; Write-Host ("  [WARN] " + $t) -ForegroundColor (Get-ProfileColor -Name Warning) }
    function Write-ProfileInfo    { param([string]$Message, [string]$Detail = "") $t = if ($Detail) { "$Message $Detail" } else { $Message }; Write-Host ("  [INFO] " + $t) -ForegroundColor (Get-ProfileColor -Name Info) }
    function Write-ProfileRuleWithText { param([string]$Text, [string]$Style = "Neutral") Write-Host ("--- $Text " + ("-" * [Math]::Max(5, (75 - $Text.Length)))) -ForegroundColor (Get-ProfileColor -Name Border) }
}

# ── Console helpers (thin wrappers over the theme templates) ──
function Lsh-Banner {
    param([string]$Title, [string]$Subtitle)
    try { Clear-Host } catch {}
    $cBorder = Get-ProfileColor -Name Border
    $cTitle  = [ConsoleColor]::Yellow
    $cSub    = Get-ProfileColor -Name Neutral
    $w = 78
    Write-Host ("╔" + ("═" * $w) + "╗") -ForegroundColor $cBorder
    # Title row — bright yellow, centered-ish left
    Write-Host "║ " -ForegroundColor $cBorder -NoNewline
    Write-Host $Title.PadRight($w - 2) -ForegroundColor $cTitle -NoNewline
    Write-Host " ║" -ForegroundColor $cBorder
    if ($Subtitle) {
        Write-Host "║ " -ForegroundColor $cBorder -NoNewline
        Write-Host $Subtitle.PadRight($w - 2) -ForegroundColor $cSub -NoNewline
        Write-Host " ║" -ForegroundColor $cBorder
    }
    Write-Host ("╚" + ("═" * $w) + "╝") -ForegroundColor $cBorder
    Write-Host ""
}
function Lsh-Step   { param([string]$Msg) Write-ProfileInfo -Message $Msg }
function Lsh-Ok     { param([string]$Msg) Write-ProfileSuccess -Message $Msg }
function Lsh-Warn   { param([string]$Msg) Write-ProfileWarning -Message $Msg }
function Lsh-Fail   { param([string]$Msg) Write-ProfileError -Message $Msg }
function Lsh-Phase  { param([string]$Text) Write-Host ""; Write-ProfileRuleWithText -Text $Text -Style "Info"; Write-Host "" }

# ── Process / port primitives ──
function Test-PidAlive {
    param([int]$ProcessId)
    if (-not $ProcessId) { return $false }
    try { Get-Process -Id $ProcessId -ErrorAction Stop | Out-Null; return $true } catch { return $false }
}

function Test-Port {
    <# True when $PortNumber is in LISTENING state (default :8799 live). #>
    param([int]$PortNumber = 0)
    if ($PortNumber -le 0) { $PortNumber = $Port }
    return [bool](netstat -ano | Select-String ":$PortNumber\s+.*LISTENING")
}

function Get-PortPid {
    <# PID of the process LISTENING on $PortNumber, or $null (default :8799). #>
    param([int]$PortNumber = 0)
    if ($PortNumber -le 0) { $PortNumber = $Port }
    $line = netstat -ano | Select-String ":$PortNumber\s+.*LISTENING" | Select-Object -First 1
    if (-not $line) { return $null }
    return [int](($line.ToString() -split "\s+")[-1])
}

function Format-Uptime {
    param($Start)
    if (-not $Start) { return "" }
    $el = (Get-Date) - $Start
    if ($el.TotalHours -ge 1) { return ("{0}h {1}m" -f [int]$el.TotalHours, $el.Minutes) }
    if ($el.TotalMinutes -ge 1) { return ("{0}m {1}s" -f [int]$el.TotalMinutes, $el.Seconds) }
    return ("{0}s" -f [int]$el.TotalSeconds)
}

function Format-AgeSec {
    <# Convert raw seconds into a compact human-readable string:
       ≥ 1 day  → "2d 3h"    ≥ 1 hour → "4h 21m"
       ≥ 1 min  → "6m 12s"   < 1 min  → "42s"  #>
    param([int]$Seconds)
    if ($Seconds -ge 86400) {
        $d = [math]::Floor($Seconds / 86400)
        $h = [math]::Floor(($Seconds % 86400) / 3600)
        return "{0}d {1}h" -f $d, $h
    }
    if ($Seconds -ge 3600) {
        $h = [math]::Floor($Seconds / 3600)
        $m = [math]::Floor(($Seconds % 3600) / 60)
        return "{0}h {1}m" -f $h, $m
    }
    if ($Seconds -ge 60) {
        $m = [math]::Floor($Seconds / 60)
        $s = $Seconds % 60
        return "{0}m {1}s" -f $m, $s
    }
    return "{0}s" -f $Seconds
}

function ConvertTo-RelativePath {
    <# Shorten an absolute path for display: project-relative (data/orders.db)
    when under the repo, home-relative (~/...) otherwise. Paths that are
    already relative pass through unchanged. #>
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $Path }
    $norm = $Path -replace '\\', '/'
    if ($norm -notmatch '^[A-Za-z]:/') { return $norm }
    $proj = $ProjectPath -replace '\\', '/'
    if ($norm.StartsWith($proj + '/', [System.StringComparison]::OrdinalIgnoreCase)) {
        return $norm.Substring($proj.Length + 1)
    }
    if ($env:USERPROFILE) {
        $home = $env:USERPROFILE -replace '\\', '/'
        if ($norm.StartsWith($home + '/', [System.StringComparison]::OrdinalIgnoreCase)) {
            return '~/' + $norm.Substring($home.Length + 1)
        }
    }
    return $norm
}

function Get-NextShadowSeq {
    <# Next 2-digit run id (01..99) used to name the NN_shadow_<stamp>.db db
    and the shadow-NN run id itself. It is derived from existing data/*.db files
    that already use the NN_shadow_ prefix, so a new run never collides with a
    previous one. Because a clean reset wipes those files, the count restarts at
    01 each fresh session and counts up run by run. #>
    $seqs = @(Get-ChildItem (Join-Path $ProjectPath "data") -File -Filter "*.db" -ErrorAction SilentlyContinue |
        ForEach-Object { if ($_.BaseName -match '^(\d{1,2})_shadow_') { [int]$Matches[1] } })
    $next = 1
    if ($seqs.Count -gt 0) { $next = [int](@($seqs | Measure-Object -Maximum).Maximum) + 1 }
    if ($next -gt 99) { $next = 1 }   # stay within the 00-99 run-id range
    return ("{0:D2}" -f $next)
}

function Write-DynamicCell {
    <# Render a status dynamic cell. Accepts either a plain string or a list of
    segment hashtables @{ t = 'text'; c = 'ColorRole' } where c is optional and
    falls back to the cell's default color. Segments let one cell mix colors,
    e.g. "Run: python -m scripts.filter_loop" with the command in Command
    color and the module/args in Argument color. #>
    param($Segments, [string]$DefaultColor)
    $color = Get-ProfileColor -Name $DefaultColor
    if ($Segments -is [string]) {
        Write-Host $Segments -ForegroundColor $color
        return
    }
    foreach ($seg in @($Segments)) {
        $segColor = if ($seg -and $seg.c) { Get-ProfileColor -Name $seg.c } else { $color }
        Write-Host $seg.t -ForegroundColor $segColor -NoNewline
    }
    Write-Host ''
}

# ── Dashboard process ownership (runtime/live-dash.pids.json) ──
function Get-DashInstance {
    <# The recorded dashboard process that is STILL the one we started (start-ticks checked). #>
    if (-not (Test-Path $DashPidFile)) { return $null }
    try { $data = Get-Content $DashPidFile -Raw | ConvertFrom-Json } catch { return $null }
    $d = $data.dash
    if (-not $d -or -not $d.pid) { return $null }
    try { $p = Get-Process -Id $d.pid -ErrorAction Stop } catch { return $null }
    if ($null -ne $d.started_ticks) {
        $pStart = $null
        try { $pStart = $p.StartTime } catch {}
        if ($null -eq $pStart -or $pStart.ToUniversalTime().Ticks -ne [int64]$d.started_ticks) {
            return $null  # PID recycled; no longer our process
        }
    }
    return [pscustomobject]@{ pid = $p.Id; proc = $p; port = $Port }
}

function Save-DashInstance {
    param([Parameter(Mandatory)]$DashProcess)
    $record = $null
    if ($DashProcess -and -not $DashProcess.HasExited) {
        $record = [pscustomobject]@{
            pid           = $DashProcess.Id
            started_ticks = $DashProcess.StartTime.ToUniversalTime().Ticks
            started       = $DashProcess.StartTime.ToString("o")
            port          = $Port
        }
    }
    New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
    [pscustomobject]@{
        strategy = "spread-hunter"
        saved    = (Get-Date).ToString("o")
        dash     = $record
    } | ConvertTo-Json -Depth 4 | Set-Content -Path $DashPidFile -Encoding UTF8
}

function Test-DashboardServer {
    <# True when whatever answers on :LivePort is a spread-hunter dashboard
    (answers /api/system/status with a services.dash entry AND reports the
    production registry). Guards adoption so a menu-unowned shadow dashboard
    (or unrelated process) squatting on the port is never adopted as live nor
    killed. db_is_production fails closed: an unreadable/unknown identity
    refuses adoption.
    #>
    try {
        $r = Invoke-RestMethod -Uri "$DashUrl/api/system/status" -UseBasicParsing -TimeoutSec 4
        return ($null -ne $r.services -and $null -ne $r.services.dash -and ($r.db_is_production -eq $true))
    } catch {
        return $false
    }
}

function Adopt-DashboardInstance {
    <# Record the running dashboard on :LivePort as owned by this menu.

    The record stores the process's OWN creation time (not "now"), so the
    PID-recycling check in Get-DashInstance still protects stop/status: a
    recycled PID would carry a different StartTime and be ignored.
    #>
    $portPid = Get-PortPid
    if (-not $portPid) { return $false }
    try {
        $proc = Get-Process -Id $portPid -ErrorAction Stop
        Save-DashInstance -DashProcess $proc
    } catch {
        return $false
    }
    return ($null -ne (Get-DashInstance))
}

function Start-Dashboard {
    <# Launch the dashboard detached; true when :8799 is serving afterwards.

    If something is already on :8799 but not recorded as ours (an orphan left
    by a restart, or a dashboard started outside this menu), offer to ADOPT it
    instead of failing or double-starting. Adoption only happens after
    Test-DashboardServer confirms it really is a spread-hunter dashboard.
    #>
    $inst = Get-DashInstance
    if ($null -ne $inst) {
        Lsh-Ok "Dashboard already running (PID $($inst.pid), up $(Format-Uptime $inst.proc.StartTime))."
        return $true
    }
    if (Test-Port) {
        $portPid = Get-PortPid
        if (Test-DashboardServer) {
            $adopt = $false
            if ($Action -ne "") {
                # CLI (lsh start): adopt silently once verified.
                $adopt = $true
            } else {
                $resp = Read-Host "  A dashboard is already serving on :$LivePort (PID $portPid). Adopt it so stop/status own it? [y/N]"
                $adopt = ($resp -match '^[yY]')
            }
            if ($adopt -and (Adopt-DashboardInstance)) {
                $inst = Get-DashInstance
                Lsh-Ok "Adopted the running dashboard (PID $($inst.pid), up $(Format-Uptime $inst.proc.StartTime)) - stop and status now own it."
            } elseif ($adopt) {
                Lsh-Fail "Could not adopt PID $portPid (may have exited mid-check)."
            } else {
                Lsh-Warn "Not adopting PID $portPid - stop will leave it running; stack control still works."
            }
            return $true
        }
        Lsh-Fail "Port $LivePort is occupied by PID $portPid, which does NOT answer as a spread-hunter dashboard - refusing to act on it. Free the port manually first."
        return $false
    }
    Lsh-Step "Launching dashboard (python -m dashboard.server --port $LivePort)..."
    $dash = Start-Process -FilePath "python" `
        -ArgumentList "-m", "dashboard.server", "--port", "$LivePort" `
        -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $OutLog `
        -RedirectStandardError  $ErrLog
    Save-DashInstance -DashProcess $dash

    $deadline = (Get-Date).AddSeconds(25)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        if (Test-Port) { break }
        $dash.Refresh()
        if ($dash.HasExited) { break }
    }
    if (-not (Test-Port)) {
        Lsh-Fail "Dashboard failed to bind port $LivePort. See $ErrLog"
        Remove-Item $DashPidFile -ErrorAction SilentlyContinue
        return $false
    }
    Lsh-Ok "Dashboard serving on $DashUrl (PID $($dash.Id))."
    return $true
}

function Stop-Dashboard {
    <# Stop the dashboard we own; leaves foreign processes on :8799 alone. #>
    $inst = Get-DashInstance
    if ($null -ne $inst) {
        Lsh-Step "Stopping dashboard PID $($inst.pid)..."
        # Tree kill: with --reload the recorded PID is uvicorn's reloader
        # parent; the serving child would survive a plain Stop-Process and
        # keep the port while the pidfile said otherwise.
        taskkill /T /F /PID $($inst.pid) 2>$null | Out-Null
        $deadline = (Get-Date).AddSeconds(10)
        while ((Get-Date) -lt $deadline -and (Test-Port)) { Start-Sleep -Milliseconds 300 }
    }
    Remove-Item $DashPidFile -ErrorAction SilentlyContinue
    if (Test-Port) {
        Lsh-Warn "Port $LivePort still LISTENING (PID $(Get-PortPid)) - not owned by this menu, left running."
        return $false
    }
    return $true
}

# ── Shadow dashboards (per-instance 880x ports, #288) ──
function _ReadShadowDashRecord {
    <# One PID file -> instance object, or $null when the record is stale. #>
    param([Parameter(Mandatory)][string]$PidFile, [string]$RunId)
    if (-not (Test-Path $PidFile)) { return $null }
    try { $data = Get-Content $PidFile -Raw | ConvertFrom-Json } catch { return $null }
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
    $port = if ($d.port) { [int]$d.port } else { 0 }
    return [pscustomobject]@{ pid = $p.Id; proc = $p; port = $port; db = $d.db; run_id = $RunId; pidfile = $PidFile; alive = $true }
}

function Get-ShadowDashInstance {
    <# The recorded SHADOW dashboard for one run id that is still our process
       (start-ticks checked). No run id in scope means no instance. #>
    param([string]$RunId = $script:ShadowRunId)
    if (-not $RunId) { return $null }
    return _ReadShadowDashRecord (Get-ShadowDashPidFile $RunId) $RunId
}

function Test-ShadowDashAlive {
    <# True when any menu-owned shadow dashboard is still our process. #>
    return (@(Get-ShadowDashInstances | Where-Object { $_.alive }).Count -gt 0)
}

function Get-ShadowDashInstances {
    <# Every menu-owned shadow dashboard record: per-instance files plus the
       pre-#288 single file (labelled legacy, stopped and removed on sight).
       Stale files come back with alive=$false so callers can prune them. #>
    $out = @()
    $files = @(Get-ChildItem $RunDir -Filter "shadow-dash-*.pids.json" -ErrorAction SilentlyContinue)
    if (Test-Path $ShadowPidFile) { $files += Get-Item $ShadowPidFile }
    foreach ($f in $files) {
        $rid = $null
        if ($f.BaseName -match '^shadow-dash-(.+)$') { $rid = $Matches[1] }
        $rec = _ReadShadowDashRecord $f.FullName $rid
        if ($null -ne $rec) { $out += $rec }
        else { $out += [pscustomobject]@{ pid = $null; proc = $null; port = 0; db = $null; run_id = $rid; pidfile = $f.FullName; alive = $false } }
    }
    return $out
}

function Save-ShadowDashInstance {
    param([Parameter(Mandatory)]$DashProcess,
          [string]$RunId = $script:ShadowRunId,
          [int]$Port = 0)
    if (-not $RunId) { throw "Save-ShadowDashInstance needs a run id." }
    if ($Port -le 0) { $Port = Get-ShadowDashPort $RunId }
    $pidFile = Get-ShadowDashPidFile $RunId
    $record = $null
    if ($DashProcess -and -not $DashProcess.HasExited) {
        $record = [pscustomobject]@{
            pid           = $DashProcess.Id
            started_ticks = $DashProcess.StartTime.ToUniversalTime().Ticks
            started       = $DashProcess.StartTime.ToString("o")
            port          = $Port
            db            = $ShadowDbPath
        }
    }
    New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
    [pscustomobject]@{
        strategy = "spread-hunter"
        mode     = "shadow"
        saved    = (Get-Date).ToString("o")
        dash     = $record
    } | ConvertTo-Json -Depth 4 | Set-Content -Path $pidFile -Encoding UTF8
}

function Test-ShadowDashboardServer {
    <# True when whatever answers on the instance port is a SHADOW dashboard
    (services.dash present AND db_is_production false — i.e. it serves
    a per-run shadow_*.db). Guards adoption so a foreign server on the port
    is never recorded as ours nor later stopped as one.
    #>
    param([string]$RunId = $script:ShadowRunId)
    if (-not $RunId) { return $false }
    try {
        $url = Get-ShadowDashUrl $RunId
        $r = Invoke-RestMethod -Uri "$url/api/system/status" -UseBasicParsing -TimeoutSec 4
        return ($null -ne $r.services -and $null -ne $r.services.dash -and ($r.db_is_production -eq $false))
    } catch { return $false }
}

function Adopt-ShadowDashboardInstance {
    param([string]$RunId = $script:ShadowRunId)
    if (-not $RunId) { return $false }
    $portPid = Get-PortPid -PortNumber (Get-ShadowDashPort $RunId)
    if (-not $portPid) { return $false }
    try {
        $proc = Get-Process -Id $portPid -ErrorAction Stop
        Save-ShadowDashInstance -DashProcess $proc -RunId $RunId
    } catch { return $false }
    return ($null -ne (Get-ShadowDashInstance -RunId $RunId))
}

function Start-ShadowDashboard {
    <# Launch shadow dashboard detached with the current per-run shadow database. #>
    if (-not $ShadowDbPath) {
        # No per-run path yet (e.g. Open-Dashboard without a prior Reset) — mint one now
        # so --db never receives an empty argument. Canonical run db name:
        # NN_shadow_DD-MM_HH-mm.db where NN is the run id (shared with the
        # statistical/overnight flows; resets to 01 on a clean reset).
        $stamp = Get-Date -Format "dd-MM_HH-mm"
        $dbSeq = Get-NextShadowSeq
        $ShadowRunId = "shadow-$dbSeq"
        $script:ShadowRunId = $ShadowRunId
        $script:ShadowDbPath = Join-Path $ProjectPath "data/${dbSeq}_shadow_${stamp}.db"
        if (-not $StatsDbPath) {
            $script:StatsDbPath = Join-Path $ProjectPath "data/stats_${stamp}_${ShadowRunId}.db"
        }
        Lsh-Step "Minted per-run shadow DB for dashboard: $script:ShadowDbPath"
    }
    # Fresh/statistical paths set a function-local run id, not the script one:
    # recover it from the store name (NN_shadow_... -> shadow-NN) so the
    # dashboard below binds the instance port, not a guess.
    if (-not $script:ShadowRunId -and $ShadowDbPath) {
        $dbBase = Split-Path -Leaf $ShadowDbPath
        if ($dbBase -match '^(\d{1,2})_shadow_') {
            $script:ShadowRunId = "shadow-" + ([int]$Matches[1]).ToString("D2")
        }
    }
    $runId = $script:ShadowRunId
    $port = Get-ShadowDashPort $runId
    $url = Get-ShadowDashUrl $runId
    $inst = Get-ShadowDashInstance -RunId $runId
    if ($null -ne $inst) {
        Lsh-Ok "Shadow dashboard already running (PID $($inst.pid), up $(Format-Uptime $inst.proc.StartTime))."
        return $true
    }
    # Each instance owns its 880x port, so the live dashboard on :8799 is no
    # longer a conflict -- only this instance's own port matters here.
    if (Test-Port -PortNumber $port) {
        $portPid = Get-PortPid -PortNumber $port
        if (Test-ShadowDashboardServer -RunId $runId) {
            # Something dashboard-like is already there serving a shadow
            # store -- adopt it as ours.
            if ($Action -ne "") {
                if (Adopt-ShadowDashboardInstance -RunId $runId) {
                    $inst = Get-ShadowDashInstance -RunId $runId
                    Lsh-Ok "Adopted running shadow dashboard on :$port (PID $($inst.pid), up $(Format-Uptime $inst.proc.StartTime))."
                    return $true
                }
            } else {
                $resp = Read-Host "  A dashboard is already serving on :$port (PID $portPid). Adopt it as shadow? [y/N]"
                if ($resp -match '^[yY]' -and (Adopt-ShadowDashboardInstance -RunId $runId)) {
                    $inst = Get-ShadowDashInstance -RunId $runId
                    Lsh-Ok "Adopted running shadow dashboard on :$port (PID $($inst.pid))."
                    return $true
                } else {
                    Lsh-Warn "Not adopting PID $portPid."
                    return $true
                }
            }
        }
        Lsh-Fail "Port $port is occupied by PID $portPid, which is not a shadow dashboard. Free the port, then retry."
        return $false
    }
    if (Test-ShadowDashboardServer -RunId $runId) {
        # Edge: port test missed but server answers — adopt
        if (Adopt-ShadowDashboardInstance -RunId $runId) {
            $inst = Get-ShadowDashInstance -RunId $runId
            Lsh-Ok "Adopted running shadow dashboard (PID $($inst.pid))."
            return $true
        }
    }
    $logs = Get-ShadowDashLogs $runId
    Lsh-Step "Launching shadow dashboard (python -m dashboard.server --db $ShadowDbPath --port $port)..."
    $dash = Start-Process -FilePath "python" `
        -ArgumentList "-m", "dashboard.server", "--db", $ShadowDbPath, "--port", "$port" `
        -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $logs.out `
        -RedirectStandardError  $logs.err
    Save-ShadowDashInstance -DashProcess $dash -RunId $runId -Port $port
    $deadline = (Get-Date).AddSeconds(25)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        if (Test-Port -PortNumber $port) { break }
        $dash.Refresh()
        if ($dash.HasExited) { break }
    }
    if (-not (Test-Port -PortNumber $port)) {
        Lsh-Fail "Shadow dashboard failed to bind port $port. See $($logs.err)"
        Remove-Item (Get-ShadowDashPidFile $runId) -ErrorAction SilentlyContinue
        return $false
    }
    Lsh-Ok "Shadow dashboard serving on $url (PID $($dash.Id), db=$ShadowDbPath)."
    return $true
}

function Stop-ShadowDashboard {
    <# Stop every menu-owned shadow dashboard; leaves foreign processes on
       any port alone. #>
    $stopped = $false
    foreach ($inst in @(Get-ShadowDashInstances)) {
        if (-not $inst.alive) {
            Remove-Item $inst.pidfile -ErrorAction SilentlyContinue
            continue
        }
        $label = if ($inst.run_id) { $inst.run_id } else { "legacy record" }
        Lsh-Step "Stopping shadow dashboard $label PID $($inst.pid)..."
        # Tree kill, same reason as Stop-Dashboard: reload mode leaves the
        # serving child under the recorded reloader PID.
        taskkill /T /F /PID $($inst.pid) 2>$null | Out-Null
        $deadline = (Get-Date).AddSeconds(10)
        while ((Get-Date) -lt $deadline -and (Test-Port -PortNumber $inst.port)) { Start-Sleep -Milliseconds 300 }
        if (Wait-ProcessGone -ProcessId $inst.pid) {
            Lsh-Ok "Shadow dashboard $label stopped."
            $stopped = $true
            Remove-Item $inst.pidfile -ErrorAction SilentlyContinue
        } else {
            # Keep the record: the dashboard is still ours and still running,
            # and discarding the pidfile would orphan it from every later stop.
            Lsh-Warn "Shadow dashboard $label PID $($inst.pid) did not exit; record kept."
        }
    }
    foreach ($inst in @(Get-ShadowDashInstances)) {
        if ($inst.alive -and $inst.port -gt 0 -and (Test-Port -PortNumber $inst.port)) {
            Lsh-Warn "Port $($inst.port) still LISTENING — not owned by shadow menu, left running."
            return $false
        }
    }
    return $stopped
}

function Stop-ShadowRun {
    <# Scope note: matching stays keyed on $ProjectPath, the same ownership
    predicate Test-OrphanStackProcess uses, so detection and termination cannot
    disagree about what belongs to this checkout. Every rehearsal this menu
    launches passes an absolute --db under the project, so a menu-created
    orphan always carries the project path and is always caught. A rehearsal
    hand-started from a shell with a relative --db carries no project marker at
    all and is deliberately left alone: it is not this checkout's to kill.
    A failed scan returns $null (unknown), never $false. #>
    <# Kill the shadow rehearsal bot (shadow_run) plus its scoped stop-loss
    watcher, matched by command line inside THIS repo. Live processes never pass
    --db <per-run-shadow-db>, so the production stack is never matched. Used by the
    shadow Stop action and by every global stop / reset wipe. #>
    try {
        $procs = @(Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object {
                $_.Name -match '^python' -and $_.CommandLine -and `
                ($_.CommandLine -like "*$ProjectPath*") -and `
                (($_.CommandLine -like "*core_brain.shadow_run*") -or `
                 ($_.CommandLine -like "*scripts.global_stop_loss*" -and ($_.CommandLine -like "*shadow_*.db*" -or $_.CommandLine -like "*shadow.db*")))
            })
    } catch {
        # An unreadable process table is not proof of an empty one. $null means
        # "unknown" here, matching Test-OrphanStackProcess; callers must not
        # report "nothing running" on it.
        Lsh-Warn "Could not read the process table; shadow process scan is inconclusive."
        return $null
    }
    if ($procs.Count -eq 0) { return $false }
    $killed = $false
    foreach ($p in $procs) {
        $what = if ($p.CommandLine -like "*core_brain.shadow_run*") { "rehearsal loop" } else { "shadow stop-loss watcher" }
        Lsh-Step "Stopping unrecorded $what PID $($p.ProcessId)..."
        taskkill /F /T /PID $p.ProcessId 2>$null | Out-Null
        if (Wait-ProcessGone -ProcessId $p.ProcessId) {
            Lsh-Ok "Unrecorded $what stopped."
            $killed = $true
        } else {
            Lsh-Warn "Unrecorded $what PID $($p.ProcessId) did not exit; still running."
        }
    }
    return $killed
}

function Start-TsBridge {
    <# Launch the TypeScript dashboard bridge (server.ts) detached on :8800.
       It reverse-proxies GET /api/* to the Python dashboard at -PyDashUrl
       and injects the live control token into the HTML it serves. The URL is
       a parameter so the bridged view follows the instance (#288) instead of
       a hardcoded port. #>
    param([string]$LogPrefix = "ts-bridge",
          [string]$PyDashUrl = "")
    if (-not $PyDashUrl) {
        if (-not $script:ShadowRunId) { throw "Start-TsBridge needs -PyDashUrl or a shadow run id in scope." }
        $PyDashUrl = Get-ShadowDashUrl $script:ShadowRunId
    }
    $log = Join-Path $RunDir "$LogPrefix.log"
    $err = Join-Path $RunDir "$LogPrefix.err.log"
    # Stop any orphaned bridge (only node running server.ts in this repo).
    Stop-TsBridge
    Start-Sleep -Milliseconds 500
    # cmd.exe redirects node's stdout/stderr to the per-session log files while
    # the bridge stays fully detached (no stream readers to buffer or block).
    $cmd = "set PORT=8800&& set PY_DASH_URL=$PyDashUrl&& node server.ts >""" + $log + """ 2>""" + $err + """"
    $proc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $cmd `
        -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru
    $deadline = (Get-Date).AddSeconds(8)
    $ok = $false
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 400
        try { if ((Invoke-WebRequest -Uri "http://127.0.0.1:8800/" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) { $ok = $true; break } } catch {}
        $proc.Refresh()
        if ($proc.HasExited) { break }
    }
    if (-not $ok) { throw "TS bridge did not come up on :8800 (see $err)" }
    return @{ pid = $proc.Id; port = 8800 }
}

function Stop-TsBridge {
    <# Stop the TS bridge (and any orphaned node/cmd running server.ts from
       this repo). The node process command line is just `node server.ts` (no
       full path), so we match on the `server.ts` token, which is unique to
       this project's bridge — the dev MCP processes never run server.ts. #>
    $killed = $false
    try {
        $procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -match '^(node|cmd)\.exe$' -and `
                $_.CommandLine -and $_.CommandLine -match 'server\.ts'
            })
        foreach ($p in $procs) {
            Lsh-Step "Stopping TS bridge PID $($p.ProcessId)..."
            taskkill /F /T /PID $p.ProcessId 2>$null | Out-Null
            if (Wait-ProcessGone -ProcessId $p.ProcessId) {
                Lsh-Ok "TS bridge stopped."
                $killed = $true
            } else {
                Lsh-Warn "TS bridge PID $($p.ProcessId) did not exit; still running."
            }
        }
    } catch {}
    return $killed
}

function Open-Dashboard {
    <# "Host & Open Dashboard": claim :8799 for the requested store and open
    the dashboard in the browser WITHOUT wiping any trading or runtime state.
    If the other mode's dashboard is holding the port, stop it first; the
    Start-<mode> dashboard handles adopting/killing anything else it owns. #>
    param([Parameter(Mandatory)][string]$Mode)
    if ($Mode -ne "live" -and $Mode -ne "shadow") {
        Lsh-Fail "Open-Dashboard: unknown mode '$Mode'."
        return $false
    }
    if ($Mode -eq "shadow" -and $null -ne (Get-DashInstance))        { $null = Stop-Dashboard }
    if ($Mode -eq "live"   -and (Test-ShadowDashAlive))  { $null = Stop-ShadowDashboard }
    $ok = $false
    if ($Mode -eq "live") {
        $ok = Start-Dashboard
        if ($ok) {
            Lsh-Step "Sweeping Polymarket account balance to sync live starting capital..."
            try {
                # `python -m` resolves modules against the caller's cwd, and
                # every Start-Process launch in this script pins
                # -WorkingDirectory $ProjectPath for exactly that reason. A
                # direct `& python` call inherits the shell's location, so a
                # menu launched outside the repo root died here with
                # ModuleNotFoundError: No module named 'core_brain'.
                Push-Location $ProjectPath
                try {
                    & python -m core_brain.order_manager account-sweep --quiet
                } finally {
                    Pop-Location
                }
                if ($LASTEXITCODE -ne 0) {
                    Lsh-Warn "Initial account sweep exited with code $LASTEXITCODE; using local registry marks."
                }
            } catch {
                Lsh-Warn "Initial account sweep skipped: $_"
            }
            Start-Process $DashUrl
            Lsh-Ok "Opened $DashUrl (live) in default browser."
        }
    } else {
        $ok = Start-ShadowDashboard
        if ($ok) {
            $openedUrl = Open-ShadowDashboard
            Lsh-Ok "Opened $openedUrl (shadow db=$ShadowDbPath) in default browser."
        }
    }
    return $ok
}

function Resume-ShadowRun {
    <# Resume the PINNED shadow rehearsal in place: reopen
    data/01_shadow_12-09_00-58.db under its original shadow-01 run id, restart
    the screener/loop/observer/watcher against it, and host the dashboard on
    it. Nothing is wiped - closes, orders and marks already in the store stay
    there and the loop keeps appending to the same run id. -ResumeDb <path>
    overrides the store for one launch (run id derived from its seq prefix).
    Use this to continue the pinned run toward the 60-close sample target
    (menu option R / `shadow-resume`). -Minutes bounds the resumed session
    (default 1440 / 24h). #>
    # Select and validate the resume target FIRST: a missing store must abort
    # with the current rehearsal and dashboard still running, not after they
    # have been stopped. Only then is anything torn down.
    if ($ResumeDb -ne "") {
        $db = Get-Item -LiteralPath $ResumeDb -ErrorAction SilentlyContinue
        if (-not $db) {
            Lsh-Fail "Resume store not found: $ResumeDb"
            return $false
        }
        $script:ShadowDbPath = $db.FullName
        # Seq prefix is the run id: NN_shadow_... -> shadow-NN.
        if ($db.BaseName -match '^(\d{1,2})_shadow_') {
            $script:ShadowRunId = "shadow-" + ([int]$Matches[1]).ToString("D2")
        } else {
            $script:ShadowRunId = "shadow-resume"
        }
    } else {
        $db = Get-Item -LiteralPath $script:DefaultResumeDb -ErrorAction SilentlyContinue
        if (-not $db) {
            Lsh-Fail "Pinned rehearsal store not found: $script:DefaultResumeDb"
            return $false
        }
        $script:ShadowDbPath = $db.FullName
        $script:ShadowRunId = "shadow-01"
    }
    # The rehearsal must never touch the production registry. Only the Python
    # loop carries that guard (core_brain.shadow_guard.assert_not_production_registry),
    # so the menu refuses data/orders.db here, before ANY child -- dashboard,
    # observer or watcher included -- is pointed at it. Separators normalize on
    # both sides, and a path that cannot be resolved refuses rather than passes
    # (fail-closed, matching the guard: a false refusal costs a re-run with a
    # plainer path; a false pass points rehearsal writes at the real registry).
    $prodFull = [System.IO.Path]::GetFullPath((Join-Path $ProjectPath "data/orders.db")).Replace('/', '\')
    try {
        $storeFull = [System.IO.Path]::GetFullPath($script:ShadowDbPath).Replace('/', '\')
    } catch {
        Lsh-Fail "Could not resolve the resume store path; refusing to resume."
        return $false
    }
    if ($storeFull -ieq $prodFull) {
        Lsh-Fail "Resume refused: $($script:ShadowDbPath) is the production registry (data/orders.db). A rehearsal fabricates fills; those rows must never enter the real order history."
        return $false
    }

    # Stop any rehearsal already running so two loops never write one store,
    # and verify the stop actually worked: an old loop that survives writes
    # into the same store concurrently with the resumed one.
    if ($null -ne (Get-DashInstance)) { $null = Stop-Dashboard }
    $null = Stop-ShadowSession
    $runStopped = Stop-ShadowRun
    if ($runStopped -eq $null) {
        Lsh-Fail "Could not verify the previous rehearsal stopped (process scan inconclusive). Resume aborted - stop it manually (stop-shadow), then retry."
        return $false
    }
    Start-Sleep -Seconds 2
    if (Test-OrphanStackProcess) {
        Lsh-Fail "A rehearsal process is still alive after the stop. Resume aborted - stop it manually (stop-shadow), then retry."
        return $false
    }
    $stamp = Get-Date -Format "dd-MM_HH-mm"
    $script:StatsDbPath = Join-Path $ProjectPath "data/stats_${stamp}_$($script:ShadowRunId).db"
    $mins = if ($Minutes -gt 0) { [double]$Minutes } else { 1440.0 }

    Lsh-Ok "Resuming $($script:ShadowRunId) from $($db.Name) for $mins minute(s) - no data was wiped."
    if (-not (Start-ShadowDashboard)) { return $false }

    # Universe feed first, exactly as a fresh rehearsal does.
    Lsh-Step "Refreshing the market universe feed..."
    Push-Location $ProjectPath
    try { & python -m scripts.rank_markets } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $ProjectPath "runtime/markets.json"))) {
        Lsh-Warn "Market ranking failed - the resumed loop may quote nothing. Continuing with the existing feed."
    } else {
        Lsh-Ok "Market feed ready (runtime/markets.json)."
    }
    $screener = Start-Process -FilePath "python" -ArgumentList "-m", "scripts.filter_loop" `
        -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $RunDir "resume_screener-$($script:ShadowRunId).out.log") `
        -RedirectStandardError (Join-Path $RunDir "resume_screener-$($script:ShadowRunId).err.log")
    Register-StackService -Key "filter" -Process $screener
    # Timebox the screener like the fresh-run path does: filter_loop has no
    # duration limit of its own, so without a timer it outlives the session.
    $killSec = [int]($mins * 60)

    # The loop: same run id, same store - the merge pass reads already-merged
    # shares per pair, so no double merge and no re-close of settled pairs.
    Lsh-Step "Starting the rehearsal loop against the existing store..."
    $shadowRun = Invoke-WithRehearsalTrialEnv {
        Start-Process -FilePath "python" `
            -ArgumentList "-m", "core_brain.shadow_run", "--minutes", "$mins", "--db", $script:ShadowDbPath, "--run-id", $script:ShadowRunId `
            -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $RunDir "shadow_resume-$($script:ShadowRunId).out.log") `
            -RedirectStandardError (Join-Path $RunDir "shadow_resume-$($script:ShadowRunId).err.log")
    }
    Lsh-Ok "Rehearsal loop running (PID $($shadowRun.Id), $mins minute(s))."
    $observer = Start-Process -FilePath "python" `
        -ArgumentList "-m", "core_brain.statistics_observer", "--mode", "shadow", "--watch", $script:ShadowDbPath, "--run-id", $script:ShadowRunId, "--data-dir", (Join-Path $ProjectPath "data"), "--interval", "5", "--max-hours", (($mins / 60) + 0.08) `
        -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $RunDir "resume_observer-$($script:ShadowRunId).out.log") `
        -RedirectStandardError (Join-Path $RunDir "resume_observer-$($script:ShadowRunId).err.log")
    Lsh-Ok "Statistics observer running (PID $($observer.Id), db=$($script:StatsDbPath))."

    # The ring already exists from the first session: shadow-<NN>.jsonl.
    $ring = Join-Path $RunDir ("shadow-{0}.jsonl" -f ($script:ShadowRunId -replace "^shadow-", ""))
    $guardrail = $null
    if (Test-Path $ring) {
        $guardrail = Start-Process -FilePath "python" `
            -ArgumentList "-m", "scripts.global_stop_loss", "--db", $script:ShadowDbPath, "--ring", $ring `
            -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $RunDir "resume_guardrail-$($script:ShadowRunId).out.log") `
            -RedirectStandardError (Join-Path $RunDir "resume_guardrail-$($script:ShadowRunId).err.log")
        Lsh-Ok "Stop-loss watcher engaged (PID $($guardrail.Id)) on $ring."
    } else {
        Lsh-Warn "Ring file $ring not found; stop-loss watcher not engaged."
    }
    # Detached self-stop timer for the processes that have no duration limit
    # of their own (screener + watcher), same pattern as the fresh-run path:
    # kill only when the PID still carries the recorded start time, so a
    # recycled PID within the window is never force-stopped.
    $timerTargets = @(@{ id = [int]$screener.Id; ticks = $screener.StartTime.ToUniversalTime().Ticks })
    if ($guardrail) { $timerTargets += @{ id = [int]$guardrail.Id; ticks = $guardrail.StartTime.ToUniversalTime().Ticks } }
    $killCmd = "Start-Sleep -Seconds $killSec"
    foreach ($t in $timerTargets) {
        $killCmd += "; `$p = Get-Process -Id $($t.id) -ErrorAction SilentlyContinue; if (`$p -and `$p.StartTime.ToUniversalTime().Ticks -eq $($t.ticks)) { Stop-Process -Id $($t.id) -Force -ErrorAction SilentlyContinue }"
    }
    Start-Process -FilePath "powershell" `
        -ArgumentList "-NoProfile", "-Command", $killCmd `
        -WindowStyle Hidden

    $session = [ordered]@{
        started = (Get-Date).ToString("o")
        started_ticks = (Get-Date).ToUniversalTime().Ticks
        run_id = $script:ShadowRunId
        ShadowRunId = $script:ShadowRunId
        shadow_db = $script:ShadowDbPath
        ShadowDbPath = $script:ShadowDbPath
        stats_db = $script:StatsDbPath
        StatsDbPath = $script:StatsDbPath
        report_path = (Join-Path $ProjectPath "reports")
        resumed = $true
        screener = [ordered]@{ pid = $screener.Id; started_ticks = $screener.StartTime.ToUniversalTime().Ticks }
        loop = [ordered]@{ pid = $shadowRun.Id; started_ticks = $shadowRun.StartTime.ToUniversalTime().Ticks }
        observer = [ordered]@{ pid = $observer.Id; started_ticks = $observer.StartTime.ToUniversalTime().Ticks }
        watcher = if ($guardrail) { [ordered]@{ pid = $guardrail.Id; started_ticks = $guardrail.StartTime.ToUniversalTime().Ticks } } else { $null }
        ring = $ring
    }
    $session | ConvertTo-Json -Depth 5 | Set-Content -Path (Get-ShadowSessionFile) -Encoding UTF8

    Start-Sleep -Seconds 3
    $openedUrl = Open-ShadowDashboard
    Lsh-Ok "Opened $openedUrl in default browser (resumed db=$($script:ShadowDbPath))."
    Lsh-Step "Stop it early" 
    Write-Host "  .\scripts\spread-hunter-menu.ps1 stop-shadow" -ForegroundColor (Get-ProfileColor -Name Info)
    if ($Watch) {
        Lsh-Step "Watching the resumed loop live (Ctrl-C ends the watcher; session self-stops after $mins min)."
        Get-Content (Join-Path $RunDir "shadow_resume-$($script:ShadowRunId).err.log") -Wait -ErrorAction SilentlyContinue
    }
    return $true
}

# ── Bot stack control through the dashboard API ──
function Get-ControlToken {
    <# The dashboard bakes a per-process CSRF token into the HTML it serves. #>
    try {
        $html = (Invoke-WebRequest -Uri $DashUrl -UseBasicParsing -TimeoutSec 5).Content
        if ($html -match 'CONTROL_TOKEN\s*=\s*"([^"]+)"') { return $Matches[1] }
    } catch {}
    return $null
}

function Invoke-Control {
    param([string]$Endpoint)
    $token = Get-ControlToken
    if (-not $token) {
        Lsh-Fail "Could not read the control token from $DashUrl. Dashboard may be restarting."
        return $null
    }
    try {
        return Invoke-RestMethod -Uri "$DashUrl$Endpoint" -Method Post `
            -Headers @{ "x-control-token" = $token } -TimeoutSec 30
    } catch {
        Lsh-Fail "Control request $Endpoint failed: $($_.Exception.Message)"
        return $null
    }
}

# ── Aligned status rows ──
# Unified 4-column template across EVERY section:
#   col 1: Name / Label      (27 wide, Strong/white)
#   col 2: Status Badge      (11 wide, color-coded word: ON/OFF for processes, FOUND/MISSING/STALE for files)
#   col 3: File / Path       (35 wide, Path/green)
#   col 4: Dynamic info      (PID if running, Run command if stopped, or file metadata)
function Write-ProcessRow {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][bool]$Running,
        [object]$PidVal = $null,
        [Parameter(Mandatory)][string]$Path,
        [string]$RunCmd = "",
        $ExtraInfo = "",
        [object]$HeartbeatAgeSec = $null,
        [object]$CadenceSec = $null
    )

    # Live-state language (DESIGN.md): the status cell is one of the six
    # states with the heartbeat age riding beside it, not a bare ON/OFF. The
    # dashboards' ramp (amber 3x cadence, red 12x) is applied when a cadence
    # is supplied, so a 5s quoter and a 10-min scanner age on their own scale.
    $th = if ($CadenceSec) { Get-CadenceThresholds -CadenceSec $CadenceSec } else { $null }
    $state = Get-LiveState -Running $Running -AgeSec $HeartbeatAgeSec -Thresholds $th
    # The state cell widens to 22 chars: "◐ Degraded · 42s" does not fit the
    # old 11-char ON/OFF column, and an overflow shifts every path column.
    $ageText = if ($null -ne $HeartbeatAgeSec -and $state -in @('Running','Degraded','Down')) {
        " · {0}" -f (Format-AgeSec ([int]$HeartbeatAgeSec))
    } else { "" }
    $statusWord = "$(Get-StateGlyph -State $state) $state$ageText"
    $statusColor = Get-ProfileColor -Name (Get-StateStyle -State $state)

    if ($Running) {
        if ($ExtraInfo) {
            # ExtraInfo may be a plain string or a list of colored segments.
            $dynamic = if ($ExtraInfo -is [string]) { @(@{ t = "PID $PidVal · $ExtraInfo" }) }
                       else { @(@{ t = "PID $PidVal · " }) + @($ExtraInfo) }
        }
        elseif ($PidVal) { $dynamic = "PID $PidVal" }
        else { $dynamic = "Running" }
        $dynamicColor = "Value"
    } else {
        if ($RunCmd) {
            $parts = $RunCmd -split ' ', 2
            $dynamic = @(
                @{ t = 'Run: '; c = 'Neutral' },
                @{ t = $parts[0]; c = 'Command' },
                @{ t = if ($parts.Count -gt 1) { ' ' + $parts[1] } else { '' }; c = 'Argument' }
            )
        } else { $dynamic = "Stopped" }
        $dynamicColor = "Command"
    }

    Write-Host ("  {0,-27}" -f $Label) -ForegroundColor (Get-ProfileColor -Name Strong) -NoNewline
    Write-Host ("{0,-22}" -f $statusWord) -ForegroundColor $statusColor -NoNewline
    Write-Host ("{0,-35}" -f $Path) -ForegroundColor (Get-ProfileColor -Name Link) -NoNewline
    Write-Host ' ' -NoNewline -ForegroundColor (Get-ProfileColor -Name Neutral)
    Write-DynamicCell -Segments $dynamic -DefaultColor $dynamicColor
}

function Write-FileRow {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string]$Status,
        [AllowEmptyString()][string]$Path = "",
        $Dynamic = "",
        [string]$StatusStyle = "",
        [string]$DynamicStyle = ""
    )

    if (-not $StatusStyle) {
        $StatusStyle = switch -Regex ($Status.ToUpper()) {
            '^(FOUND|OK|FRESH|ON)$'            { 'Success' }
            '^(AGING|STALE|WARN|LISTENING)$'   { 'Warning' }
            default                            { 'Error' }
        }
    }
    if (-not $DynamicStyle) {
        $DynamicStyle = if ($Dynamic -is [string] -and $Dynamic -like "Run: *") { 'Command' }
                        elseif ($Status -in "MISSING", "ERROR") { 'Error' }
                        elseif ($Status -in "STALE", "AGING") { 'Warning' }
                        else { 'Neutral' }
    }

    $cLabel   = Get-ProfileColor -Name Strong
    $cStatus  = Get-ProfileColor -Name $StatusStyle

    Write-Host ("  {0,-27}" -f $Label) -ForegroundColor $cLabel -NoNewline
    Write-Host ("{0,-11}" -f $Status) -ForegroundColor $cStatus -NoNewline
    if ($Path -or $Dynamic) {
        # Relative paths render in Link color; a guaranteed space keeps long
        # paths from jamming into the dynamic cell ("...db2s old" -> "...db 2s Old").
        Write-Host ("{0,-35}" -f $Path) -ForegroundColor (Get-ProfileColor -Name Link) -NoNewline
        Write-Host ' ' -NoNewline -ForegroundColor (Get-ProfileColor -Name Neutral)
        Write-DynamicCell -Segments $Dynamic -DefaultColor $DynamicStyle
    } else {
        Write-Host ""
    }
}

function Write-SectionHeader {
    param(
        [Parameter(Mandatory)][string]$Number,
        [Parameter(Mandatory)][string]$Title,
        [Parameter(Mandatory)][string]$Status,
        [string]$StatusStyle = "Success",
        [int]$Width = 90
    )
    $cBorder = Get-ProfileColor -Name Border
    $cStatus = Get-ProfileColor -Name $StatusStyle

    $left = "─── $Number · $Title | "
    $rightPrefix = " "
    $usedLen = $left.Length + $Status.Length + $rightPrefix.Length
    $ruleLen = [Math]::Max(5, ($Width - $usedLen))
    $right = $rightPrefix + ("─" * $ruleLen)

    Write-Host ""
    Write-Host $left -ForegroundColor $cBorder -NoNewline
    Write-Host $Status -ForegroundColor $cStatus -NoNewline
    Write-Host $right -ForegroundColor $cBorder
}

function Write-StackRows {
    <# Bot state count (X/3) in header + unified 4-column rows. #>
    param(
        [object[]]$Rows
    )
    $runningCount = @($Rows | Where-Object { $_.Running }).Count
    $totalCount = $Rows.Count
    if ($totalCount -eq 0) { $totalCount = 3 }

    if ($runningCount -eq $totalCount) {
        Write-SectionHeader -Number "2" -Title "BOT STACK" -Status ("ON ({0}/{1})" -f $runningCount, $totalCount) -StatusStyle "Success"
    } elseif ($runningCount -gt 0) {
        Write-SectionHeader -Number "2" -Title "BOT STACK" -Status ("PARTIAL ({0}/{1})" -f $runningCount, $totalCount) -StatusStyle "Warning"
    } else {
        Write-SectionHeader -Number "2" -Title "BOT STACK" -Status ("OFF (0/{0})" -f $totalCount) -StatusStyle "Error"
    }
    foreach ($r in $Rows) {
        Write-ProcessRow -Label $r.Name -Running $r.Running -PidVal $r.Pid -Path $r.Path -RunCmd $r.RunCmd -CadenceSec $r.CadenceSec
    }
    if ($Rows.Count -gt 0 -and $runningCount -eq 0 -and (Test-Path $ProcsFile)) {
        Write-FileRow -Label "Process file" -Status "STALE" -Path "runtime/processes.json" -Dynamic "No Active PID"
    }
}

function Show-ServiceTable {
    <# Render the /api/system/status payload: bot state count + 3 stack services. #>
    param($Status)
    $rows = @()
    foreach ($svc in @("filter", "query", "decide")) {
        $s = $Status.services.$svc
        if ($s) {
            $label = @{
                filter     = "Market Filter"
                query      = "Query Polymarket"
                decide     = "Decide & Execute"
            }[$svc]
            if (-not $label) { $label = $s.name }
            $rows += [pscustomobject]@{
                Name = $label
                Running = [bool]$s.running
                Pid = $s.pid
                Path = $StackPaths[$svc]
                RunCmd = $StackCmds[$svc]
                CadenceSec = @{ filter = 600; query = 0.5; decide = 5 }[$svc]
            }
        }
    }
    Write-StackRows -Rows $rows
}

function Start-BotStack {
    <# Launch market filter + order manager poll + trader loop via the dashboard's start endpoint. #>
    $rerank = Join-Path $ProjectPath "scripts\filter_loop.py"
    if (-not (Test-Path $rerank)) {
        Lsh-Fail "Filter module scripts/filter_loop.py is MISSING in this repo - the dashboard would spawn a phantom process. Add it before starting."
        return
    }
    if (-not (Test-Port)) {
        Lsh-Fail "Dashboard is not serving on :$LivePort - nothing to drive. Start the dashboard first."
        return
    }
    $status = $null
    try { $status = Invoke-RestMethod -Uri "$DashUrl/api/system/status" -UseBasicParsing -TimeoutSec 5 } catch {}
    if ($status -and $status.bot_state -eq "RUNNING") {
        Lsh-Warn "Bot stack is already running."
        Show-ServiceTable $status
        return
    }
    Lsh-Step "Requesting bot stack start (Market Filter + Query Polymarket + Decide & Execute)..."
    Lsh-Warn "Decide & Execute rests REAL maker bids. Verify the dashboard shows a clean state before proceeding."
    $res = Invoke-Control -Endpoint "/api/system/start"
    if (-not $res) { return }
    if ($res.ok) { Lsh-Ok "Bot stack started: $($res.message)." }
    else         { Lsh-Fail "Start refused: $($res.message)" }
    if ($res.status) { Show-ServiceTable $res.status }
}

function Stop-BotStack {
    <# Stop the stack via the API; if the dashboard is down, kill recorded PIDs directly. #>
    if (Test-Port) {
        Lsh-Step "Requesting bot stack stop..."
        $res = Invoke-Control -Endpoint "/api/system/stop"
        if (-not $res) { return }
        if ($res.ok) { Lsh-Ok "Bot stack stopped: $($res.message)." }
        else         { Lsh-Fail "Stop refused: $($res.message)" }
        return
    }
    if (Test-Path $ProcsFile) {
        $saved = $null
        try { $saved = Get-Content $ProcsFile -Raw | ConvertFrom-Json } catch { $saved = $null }
        if ($null -eq $saved) {
            # A registry we cannot parse is not an empty one. Falling through
            # would kill nothing, delete the file, and print "stale record
            # cleared" -- destroying the only record of the live PIDs. Matches
            # stop_bot() in dashboard/server.py, which refuses the same way.
            Lsh-Fail "Cannot read $ProcsFile; refusing to report the stack stopped. No process was killed and the file was left in place. Fix or remove it, then stop again."
            return
        }
        $killed = $false
        $skipped = $false
        foreach ($name in @("filter", "query", "decide")) {
            $info = if ($saved) { Get-ServiceEntry -Saved $saved -Key $name } else { $null }
            if ($info -and $info.pid -and (Test-PidAlive -ProcessId $info.pid)) {
                # Validate started_at before killing
                $shouldKill = $false
                if (-not $info.started_at) {
                    # No start time: skip this kill
                    $skipped = $true
                    Lsh-Warn "$name (PID $($info.pid)) has no started_at; skipping kill"
                } else {
                    $recordedStart = ConvertTo-RecordedStart -StartedAt $info.started_at
                    if ($null -eq $recordedStart) {
                        $skipped = $true
                        Lsh-Warn "$name (PID $($info.pid)) has an unreadable started_at; skipping kill"
                    } else {
                        try {
                            $proc = Get-Process -Id $info.pid -ErrorAction Stop
                            $actualStart = $proc.StartTime
                            $tolerance = [timespan]::FromSeconds(60)
                            if ([math]::Abs(($actualStart - $recordedStart).TotalSeconds) -le $tolerance.TotalSeconds) {
                                $shouldKill = $true
                            } else {
                                $skipped = $true
                                Lsh-Warn "$name (PID $($info.pid)) start time mismatch; skipping kill (recorded: $recordedStart, actual: $actualStart)"
                            }
                        } catch {
                            # If we can't get start time, skip kill
                            $skipped = $true
                            Lsh-Warn "$name (PID $($info.pid)) start time unavailable; skipping kill"
                        }
                    }
                }
                if ($shouldKill) {
                    Lsh-Step "Killing $name (PID $($info.pid))..."
                    taskkill /F /T /PID $info.pid 2>$null | Out-Null
                    $killed = $true
                }
            }
        }
        if ($skipped) {
            # Keep the record. Deleting it here is how a live stack becomes
            # unreachable: the PIDs would be gone and a later start would see
            # STOPPED and launch a second Decide & Execute beside this one.
            Lsh-Fail "Left one or more recorded processes running; keeping $ProcsFile. Stop them by PID, then re-run."
        } else {
            Remove-Item $ProcsFile -ErrorAction SilentlyContinue
            if ($killed) { Lsh-Ok "Bot stack processes killed from recorded PIDs." }
            else         { Lsh-Warn "processes.json exists but no process is alive (stale record cleared)." }
        }
    } else {
        Lsh-Warn "No bot stack is running (no processes.json)."
    }
}

# ── Status ──
# Live-state language (DESIGN.md): the terminal reads the same six-state
# vocabulary the dashboard renders, with the same heartbeat-age ramp — amber
# at 3x a service's cadence, red at 12x — so a state means the same thing on
# both surfaces.
function Get-CadenceThresholds {
    <# DESIGN.md ramp: degraded at 3x cadence, down at 12x. Defaults match a
       ~5s cadence loop; garbage cadence falls back to the documented default. #>
    param([object]$CadenceSec)
    $c = 0.0
    if (-not [double]::TryParse("$CadenceSec", [Globalization.NumberStyles]::Float,
                                [Globalization.CultureInfo]::InvariantCulture, [ref]$c) -or $c -le 0) {
        return @{ Degraded = 15; Down = 60 }
    }
    return @{ Degraded = [int][math]::Round($c * 3); Down = [int][math]::Round($c * 12) }
}

function Get-LiveState {
    <# Map (Running, AgeSec, Thresholds) to one of the six DESIGN.md states.
       Running with a fresh heartbeat is Running; aging past the degraded
       threshold is Degraded; past the down threshold (or dead with a known
       age) is Down. A quiet intentional stop is Stopped. No data at all is
       Unknown — which is NOT an alarm. #>
    param([bool]$Running, [object]$AgeSec, [hashtable]$Thresholds = @{ Degraded = 15; Down = 60 })
    $th = if ($Thresholds) { $Thresholds } else { @{ Degraded = 15; Down = 60 } }
    $hasAge = ($null -ne $AgeSec)
    if ($Running) {
        if (-not $hasAge)          { return "Running" }
        if ($AgeSec -ge $th.Down)  { return "Down" }
        if ($AgeSec -ge $th.Degraded) { return "Degraded" }
        return "Running"
    }
    if ($hasAge) { return "Down" }
    return "Stopped"
}

function Get-StateStyle {
    <# Terminal color per state. Down and Degraded are alarms; a deliberate
       stop is quiet (neutral), not red — the same semantic the dashboard's
       state-stopped pill carries. #>
    param([string]$State)
    switch ($State) {
        "Running"  { return "Success" }
        "Degraded" { return "Warning" }
        "Down"     { return "Error" }
        "Stopped"  { return "Neutral" }
        default    { return "Neutral" }   # Unknown
    }
}

function Get-StateGlyph {
    <# Static two-char mark per state (no emoji, per DESIGN.md). The running
       glyph's animation lives in the heartbeat age, not in the mark. #>
    param([string]$State)
    switch ($State) {
        "Running"  { return "●" }
        "Degraded" { return "◐" }
        "Down"     { return "✕" }
        "Stopped"  { return "○" }
        default    { return "--" }        # Unknown
    }
}

function Get-HeartbeatAgeSec {
    param([string]$Ts)
    if (-not $Ts) { return $null }
    try {
        $dt = [datetime]::Parse($Ts, [Globalization.CultureInfo]::InvariantCulture,
                                 [Globalization.DateTimeStyles]::AdjustToUniversal)
        return [int]((Get-Date).ToUniversalTime() - $dt).TotalSeconds
    } catch { return $null }
}

function Show-Status {
    Lsh-Banner -Title "SPREAD HUNTER - STATUS" -Subtitle "Execution engine: $(Split-Path $ProjectPath -Leaf)"

    # ── 1 · DASHBOARD ──
    $inst = Get-DashInstance
    $portUp = Test-Port
    if ($inst) {
        Write-SectionHeader -Number "1" -Title "DASHBOARD" -Status "ON" -StatusStyle "Success"
        Write-ProcessRow -Label "Dashboard" -Running $true -PidVal $inst.pid -Path $StackPaths["dash"] -ExtraInfo $DashUrl
        Write-FileRow -Label "PID file" -Status "FOUND" -Path "runtime/live-dash.pids.json" -Dynamic ("PID {0} recorded" -f $inst.pid)
    } elseif ($portUp) {
        Write-SectionHeader -Number "1" -Title "DASHBOARD" -Status "LISTENING" -StatusStyle "Warning"
        Write-FileRow -Label "Dashboard" -Status "LISTENING" -Path $StackPaths["dash"] -Dynamic ("PID {0} on port {1} (external)" -f (Get-PortPid), $LivePort)
        Write-FileRow -Label "PID file" -Status "MISSING" -Path "runtime/live-dash.pids.json" -Dynamic "Not Owned by Menu"
    } else {
        Write-SectionHeader -Number "1" -Title "DASHBOARD" -Status "OFF" -StatusStyle "Error"
        Write-ProcessRow -Label "Dashboard" -Running $false -Path $StackPaths["dash"] -RunCmd ("python -m dashboard.server --port {0}" -f $LivePort)
        Write-FileRow -Label "PID file" -Status "MISSING" -Path "runtime/live-dash.pids.json" -Dynamic "No PID File"
    }

    # ── 1b · SHADOW DASHBOARD (one row per menu-owned instance) ──
    $sess = $null
    if (Test-Path $ShadowSessionFile) { try { $sess = Get-Content $ShadowSessionFile -Raw | ConvertFrom-Json } catch {} }
    $shadowInsts = @(Get-ShadowDashInstances)
    $shadowLive = @($shadowInsts | Where-Object { $_.alive })
    $shadowStale = @($shadowInsts | Where-Object { -not $_.alive })
    if ($shadowLive.Count -gt 0) {
        Write-SectionHeader -Number "1b" -Title "SHADOW DASHBOARD" -Status "ON" -StatusStyle "Success"
        foreach ($shadowInst in $shadowLive) {
            $dbInfo = if ($shadowInst.db) { $shadowInst.db } elseif ($sess -and $sess.shadow_db) { $sess.shadow_db } else { $ShadowDbPath }
            if (-not $dbInfo) { $dbInfo = "per-run shadow_*.db" }
            $dbRel = if ($dbInfo -like "per-run*") { $dbInfo } else { ConvertTo-RelativePath $dbInfo }
            $instUrl = "http://127.0.0.1:$($shadowInst.port)"
            $instLabel = if ($shadowInst.run_id) { "Shadow Dashboard ($($shadowInst.run_id))" } else { "Shadow Dashboard (legacy)" }
            $extraInfo = @(
                @{ t = $instUrl; c = 'Link' },
                @{ t = " (db="; c = 'Neutral' },
                @{ t = $dbRel; c = 'Link' },
                @{ t = ")"; c = 'Neutral' }
            )
            Write-ProcessRow -Label $instLabel -Running $true -PidVal $shadowInst.pid -Path $StackPaths["shadowDash"] -ExtraInfo $extraInfo
            Write-FileRow -Label "PID file" -Status "FOUND" -Path (ConvertTo-RelativePath $shadowInst.pidfile) -Dynamic ("PID {0} recorded" -f $shadowInst.pid)
        }
        $tsPid = if ($sess -and $sess.ts_bridge -and $sess.ts_bridge.pid) { $sess.ts_bridge.pid } else { $null }
        $tsAlive = $null -ne $tsPid -and (Get-Process -Id $tsPid -ErrorAction SilentlyContinue)
        $tsTarget = " -> :$($shadowLive[0].port)"
        Write-ProcessRow -Label "TS Bridge" -Running $tsAlive -PidVal $(if ($tsAlive) { $tsPid } else { $null }) -Path "server.ts" -ExtraInfo @(@{ t = "http://127.0.0.1:8800"; c = 'Link' }, @{ t = $tsTarget; c = 'Neutral' })
    } elseif ($shadowStale.Count -gt 0) {
        # pidfiles but nothing alive — stale
        Write-SectionHeader -Number "1b" -Title "SHADOW DASHBOARD" -Status "STALE" -StatusStyle "Warning"
        Write-ProcessRow -Label "Shadow Dashboard" -Running $false -Path $StackPaths["shadowDash"] -RunCmd $StackCmds["shadowDash"]
        foreach ($st in $shadowStale) {
            Write-FileRow -Label "PID file" -Status "STALE" -Path (ConvertTo-RelativePath $st.pidfile) -Dynamic "No Active PID"
        }
    } else {
        Write-SectionHeader -Number "1b" -Title "SHADOW DASHBOARD" -Status "OFF" -StatusStyle "Error"
        Write-ProcessRow -Label "Shadow Dashboard" -Running $false -Path $StackPaths["shadowDash"] -RunCmd $StackCmds["shadowDash"]
        Write-FileRow -Label "PID file" -Status "MISSING" -Path "runtime/shadow-dash.pids.json" -Dynamic "no PID file"
    }

    # ── 1c · SHADOW RUNS (recorded sessions, one row-set per run id) ──
    $sessionFiles = @(Get-ChildItem $RunDir -Filter "shadow-session-*.json" -ErrorAction SilentlyContinue)
    if (Test-Path $ShadowSessionFile) { $sessionFiles += Get-Item $ShadowSessionFile }
    foreach ($sf in $sessionFiles) {
        $one = $null
        try { $one = Get-Content $sf.FullName -Raw | ConvertFrom-Json } catch {}
        if (-not ($one -and $one.screener -and $one.screener.pid)) { continue }
        $runTag = if ($one.run_id) { " ($($one.run_id))" } else { "" }
        Write-SectionHeader -Number "1c" -Title "SHADOW RUN$runTag" -Status "RECORDED" -StatusStyle "Info"
        foreach ($key in @(@{k="screener";l="Shadow Screener"}, @{k="loop";l="Rehearsal Loop"}, @{k="observer";l="Statistics Observer"}, @{k="watcher";l="Stop-loss Watcher"})) {
            $ent = $one.($key.k)
            $p = Get-ProcessRecord -Entry $ent
            $alive = $null -ne $p
            $runCmd = if ($key.k -eq "observer") { "python -m core_brain.statistics_observer" }
                      elseif ($key.k -eq "watcher") { "python -m scripts.global_stop_loss" }
                      elseif ($key.k -eq "screener") { "python -m scripts.filter_loop" }
                      else { "python -m core_brain.shadow_run" }
            Write-ProcessRow -Label $key.l -Running $alive -PidVal $(if ($alive) { $ent.pid } else { $null }) -Path $StackPaths["shadowDash"] -RunCmd $runCmd
        }
        $sessSegs = @()
        $sessSegs += @{ t = "Started {0}; " -f (Format-Uptime (Get-Date $one.started)); c = 'Neutral' }
        if ($one.shadow_db) {
            $sessSegs += @{ t = "shadow_db="; c = 'Neutral' }
            $sessSegs += @{ t = (ConvertTo-RelativePath $one.shadow_db); c = 'Link' }
        } else { $sessSegs += @{ t = "No Shadow DB"; c = 'Neutral' } }
        $sessSegs += @{ t = "; "; c = 'Neutral' }
        if ($one.stats_db) {
            $sessSegs += @{ t = "stats_db="; c = 'Neutral' }
            $sessSegs += @{ t = (ConvertTo-RelativePath $one.stats_db); c = 'Link' }
        } else { $sessSegs += @{ t = "No Stats DB"; c = 'Neutral' } }
        Write-FileRow -Label "Session file" -Status "FOUND" -Path (ConvertTo-RelativePath $sf.FullName) -Dynamic $sessSegs
    }

    # ── 2 · BOT STACK (dashboard API when up, processes.json otherwise) ──
    $status = $null
    if ($portUp) {
        try { $status = Invoke-RestMethod -Uri "$DashUrl/api/system/status" -UseBasicParsing -TimeoutSec 5 } catch {}
    }
    if ($status) {
        Show-ServiceTable $status
    } else {
        $saved = $null
        if (Test-Path $ProcsFile) { try { $saved = Get-Content $ProcsFile -Raw | ConvertFrom-Json } catch {} }
        $rows = @()
        if ($saved) {
            foreach ($name in @("filter", "query", "decide")) {
                $info = Get-ServiceEntry -Saved $saved -Key $name
                $running = [bool]($info -and $info.pid -and (Test-PidAlive -ProcessId $info.pid))
                $label = @{
                    filter     = "Market Filter"
                    query      = "Query Polymarket"
                    decide     = "Decide & Execute"
                }[$name]
                $rows += [pscustomobject]@{
                    Name = $label
                    Running = $running
                    Pid = $info.pid
                    Path = $StackPaths[$name]
                    RunCmd = $StackCmds[$name]
                }
            }
            Write-StackRows -Rows $rows
        } else {
            Write-SectionHeader -Number "2" -Title "BOT STACK" -Status "OFF (0/3)" -StatusStyle "Error"
            Write-ProcessRow -Label "Market Filter" -Running $false -Path $StackPaths["filter"] -RunCmd $StackCmds["filter"]
            Write-ProcessRow -Label "Query Polymarket" -Running $false -Path $StackPaths["query"] -RunCmd $StackCmds["query"]
            Write-ProcessRow -Label "Decide & Execute" -Running $false -Path $StackPaths["decide"] -RunCmd $StackCmds["decide"]
        }
    }

    # ── 3 · GLOBAL STOP LOSS (API when up, heartbeat file otherwise) ──
    $gh = $null
    if ($portUp) {
        try { $gh = Invoke-RestMethod -Uri "$DashUrl/api/guardrail-health" -UseBasicParsing -TimeoutSec 5 } catch {}
    }
    if ($gh) {
        # Live-state language (DESIGN.md): the watcher's ~5s heartbeat drives
        # the same ramp the dashboard applies — amber at 15s, red at 60s.
        $th = Get-CadenceThresholds -CadenceSec 5
        $ghAge = if ($null -ne $gh.age_s) { [int]$gh.age_s } else { $null }
        $state = Get-LiveState -Running ([bool]$gh.running) -AgeSec $ghAge -Thresholds $th
        $hdrStatus = if ($state -eq 'Running') { 'ON' } elseif ($state -in @('Degraded','Down')) { 'STALE' } else { 'OFF' }
        $hdrStyle  = Get-StateStyle -State $state
        Write-SectionHeader -Number "3" -Title "GLOBAL STOP LOSS" -Status $hdrStatus -StatusStyle $hdrStyle
        Write-ProcessRow -Label "Global Stop Loss" -Running ([bool]$gh.running) -PidVal $gh.pid -Path $StackPaths["guardrail"] -HeartbeatAgeSec $ghAge -CadenceSec 5
        $hbAgeText = if ($null -ne $ghAge) { Format-AgeSec $ghAge } else { "Unknown" }
        Write-FileRow -Label "Heartbeat file" -Status $(if ($state -in @('Degraded','Down')) { "STALE" } else { "FOUND" }) -Path "runtime/global_stop_loss_heartbeat.json" -Dynamic ("{0} Old" -f $hbAgeText)
        Write-FileRow -Label "Alerts log" -Status "FOUND" -Path "runtime/global_stop_loss_alerts.log" -Dynamic ("{0} Alerts" -f $gh.alerts_total)
    } elseif (Test-Path $HbFile) {
        try {
            $hb = Get-Content $HbFile -Raw | ConvertFrom-Json
            $age = Get-HeartbeatAgeSec $hb.ts
            $th = Get-CadenceThresholds -CadenceSec 5
            $state = Get-LiveState -Running ($null -ne $age -and $age -le 30) -AgeSec $age -Thresholds $th
            $hdrStatus = if ($state -eq 'Running') { 'ON' } elseif ($state -in @('Degraded','Down')) { 'STALE' } else { 'OFF' }
            $hdrStyle  = Get-StateStyle -State $state
            Write-SectionHeader -Number "3" -Title "GLOBAL STOP LOSS" -Status $hdrStatus -StatusStyle $hdrStyle
            $isAlive = ($state -eq 'Running' -or $state -eq 'Degraded')
            Write-ProcessRow -Label "Global Stop Loss" -Running $isAlive -PidVal $(if ($isAlive) { $hb.pid } else { $null }) -Path $StackPaths["guardrail"] -HeartbeatAgeSec $age -CadenceSec 5
            Write-FileRow -Label "Heartbeat file" -Status $(if ($state -in @('Degraded','Down')) { "STALE" } else { "FOUND" }) -Path "runtime/global_stop_loss_heartbeat.json" -Dynamic ("{0} Old" -f (Format-AgeSec $age))
            Write-FileRow -Label "Alerts log" -Status "FOUND" -Path "runtime/global_stop_loss_alerts.log" -Dynamic "0 Alerts"
        } catch {
            Write-SectionHeader -Number "3" -Title "GLOBAL STOP LOSS" -Status "OFF" -StatusStyle "Error"
            Write-ProcessRow -Label "Global Stop Loss" -Running $false -Path $StackPaths["guardrail"] -RunCmd "python -m scripts.global_stop_loss"
            Write-FileRow -Label "Heartbeat file" -Status "ERROR" -Path "runtime/global_stop_loss_heartbeat.json" -Dynamic "Unreadable"
            Write-FileRow -Label "Alerts log" -Status "FOUND" -Path "runtime/global_stop_loss_alerts.log" -Dynamic "0 Alerts"
        }
    } else {
        Write-SectionHeader -Number "3" -Title "GLOBAL STOP LOSS" -Status "OFF" -StatusStyle "Error"
        Write-ProcessRow -Label "Global Stop Loss" -Running $false -Path $StackPaths["guardrail"] -RunCmd "python -m scripts.global_stop_loss"
        Write-FileRow -Label "Heartbeat file" -Status "MISSING" -Path "runtime/global_stop_loss_heartbeat.json" -Dynamic "No File"
        Write-FileRow -Label "Alerts log" -Status "MISSING" -Path "runtime/global_stop_loss_alerts.log" -Dynamic "No File"
    }

    # ── 4 · MARKET FILTER & UNIVERSE FEED (what the stack depends on) ──
    Show-ScreenerAndFeed

    # ── 5 · CHECKOUT IDENTITY (prove every launched module resolves inside THIS repo) ──
    Show-CheckoutIdentity
}

function Show-ScreenerAndFeed {
    $rerank = Join-Path $ProjectPath "scripts\filter_loop.py"
    $ranker = Join-Path $ProjectPath "scripts\filter_markets.py"
    $strategyCfg = Join-Path $ProjectPath "scoring\config.py"
    $modulesOk = ((Test-Path $rerank) -and (Test-Path $ranker) -and (Test-Path $strategyCfg))

    $feed = Resolve-RuntimeFile -Name "markets.json"
    $feedExists = Test-Path $feed
    $ageSec = if ($feedExists) { [int]((Get-Date) - (Get-Item $feed).LastWriteTime).TotalSeconds } else { $null }
    $count = "?"
    if ($feedExists) {
        try { $count = @(Get-Content $feed -Raw | ConvertFrom-Json).Count } catch {}
    }

    $feedStatus = "FOUND"
    if (-not $feedExists)      { $feedStatus = "MISSING" }
    elseif ($ageSec -gt 21600) { $feedStatus = "STALE" }
    elseif ($ageSec -gt 3600)  { $feedStatus = "AGING" }

    $hdrStatus = if (-not $modulesOk) { "MISSING" } else { $feedStatus }
    Write-SectionHeader -Number "4" -Title "MARKET FILTER & UNIVERSE FEED" -Status $hdrStatus

    $stRerank = if (Test-Path $rerank) { "FOUND" } else { "MISSING" }
    Write-FileRow -Label "Filter loop" -Status $stRerank -Path "scripts/filter_loop.py" -Dynamic "Runs Every 10 Min"

    $stRanker = if (Test-Path $ranker) { "FOUND" } else { "MISSING" }
    Write-FileRow -Label "Filter engine" -Status $stRanker -Path "scripts/filter_markets.py" -Dynamic "Fetch & Filter"

    $runSegs = @(
        @{ t = 'Run: '; c = 'Neutral' },
        @{ t = 'python'; c = 'Command' },
        @{ t = ' -m scripts.filter_loop'; c = 'Argument' }
    )
    $feedDetail = if (-not $feedExists) { $runSegs }
                  elseif ($feedStatus -eq "STALE") { @(@{ t = "{0} Markets · {1} Old · " -f $count, (Format-AgeSec $ageSec); c = 'Neutral' }) + $runSegs }
                  else { "{0} Markets · {1} Old" -f $count, (Format-AgeSec $ageSec) }
    # Show the path actually read, so a pre-rename run/markets.json is visible.
    $feedRel = ($feed.Substring($ProjectPath.Length).Trim("\", "/")) -replace "\\", "/"
    Write-FileRow -Label "Universe feed" -Status $feedStatus -Path $feedRel -Dynamic $feedDetail
}

function Show-CheckoutIdentity {
    $code = "import sys; sys.path.insert(0, r'$ProjectPath'); import os, dashboard.server as d, core_brain.order_manager as m, core_brain.trader_loop as f, scripts.filter_loop as r, scripts.filter_markets as k; print('cwd=' + os.getcwd()); print(d.__file__); print(m.__file__); print(f.__file__); print(r.__file__); print(k.__file__)"
    $raw = @()
    try { $raw = @(& python -c $code 2>&1) } catch {}
    if ($raw.Count -lt 5) {
        $raw = @()
        try { $raw = @(& py -3 -c $code 2>&1) } catch {}
    }
    $paths = @($raw | Where-Object { $_ -is [string] -and $_ -match '^[A-Za-z]:[\\/]' })

    $allOk = ($paths.Count -ge 5)
    if ($allOk) {
        for ($i = 0; $i -lt 5; $i++) {
            if ($paths[$i].Trim() -notlike "$ProjectPath*") { $allOk = $false; break }
        }
    }

    if ($allOk) {
        Write-SectionHeader -Number "5" -Title "CHECKOUT IDENTITY" -Status "FOUND" -StatusStyle "Success"
    } else {
        Write-SectionHeader -Number "5" -Title "CHECKOUT IDENTITY" -Status "CHECK" -StatusStyle "Error"
    }

    if ($paths.Count -lt 5) {
        Write-FileRow -Label "Module resolution" -Status "MISSING" -Path "python" -Dynamic "import failed"
        $tail = (($raw | Select-Object -Last 2 | ForEach-Object { "$_" }) -join " ")
        if ($tail) { Write-FileRow -Label "Diagnostic" -Status "WARN" -Path "error" -Dynamic $tail }
        return
    }
    $labels = @("dashboard.server", "core_brain.order_manager", "core_brain.trader_loop", "scripts.filter_loop", "scripts.filter_markets")
    for ($i = 0; $i -lt 5; $i++) {
        $p = $paths[$i].Trim()
        if ($p -like "$ProjectPath*") {
            Write-FileRow -Label $labels[$i] -Status "FOUND" -Path "" -Dynamic ""
        } else {
            Write-FileRow -Label $labels[$i] -Status "MISSING" -Path "" -Dynamic ($p + " <-- outside repo!")
        }
    }
}

# ── Reset & fresh start ──
function Test-RecordLive {
    <# True when PID is alive and its start time matches the recorded
    started_at (within 60s). $null when the PID is alive but the record's
    start time is missing or unreadable - callers treat that as
    conservatively live so a reset never wipes beside a process it cannot
    identify. Mirrors the start-time validation in Stop-BotStack and
    Stop-Guardrail. #>
    param([int]$ProcessId, $StartedAt)
    if (-not (Test-PidAlive -ProcessId $ProcessId)) { return $false }
    $recordedStart = ConvertTo-RecordedStart -StartedAt $StartedAt
    if ($null -eq $recordedStart) { return $null }
    try {
        $proc = Get-Process -Id $ProcessId -ErrorAction Stop
        return ([math]::Abs(($proc.StartTime - $recordedStart).TotalSeconds) -le 60)
    } catch { return $null }
}

function Test-OrphanStackProcess {
    <# True when a running python inside THIS repo launches a stack service
    (Trader, Order Manager poll, Market Filter loop, Global Stop Loss,
    dashboard) that no pidfile records - an orphan left behind by a crashed
    supervisor. Reset refuses to wipe beside it. $null (unknown) when the
    process table cannot be read, which callers treat as live. Scoped to
    $ProjectPath so an unrelated checkout or tool never blocks a reset. #>
    try {
        $procs = @(Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object { $_.Name -match '^python' -and $_.CommandLine -and ($_.CommandLine -like "*$ProjectPath*") })
    } catch { return $null }
    $markers = @(
        "core_brain.trader_loop",
        "core_brain.shadow_run",
        "core_brain.statistics_observer",
        "statistical_validation_run",
        "core_brain.order_manager poll",
        "scripts.filter_loop",
        "scripts.global_stop_loss",
        "dashboard.server"
    )
    foreach ($p in $procs) {
        foreach ($m in $markers) {
            if ($p.CommandLine -like "*$m*") { return $true }
        }
    }
    return $false
}

function Test-StackAlive {
    <# True when ANY spread-hunter process is alive: dashboards, stack PIDs,
    the guardrail heartbeat, or an orphaned stack service. Used by reset to
    decide whether a stop is needed and to prove the environment is clean
    before starting. A recorded PID is live only when its start time matches;
    a PID with a missing or unreadable start time - or a registry that cannot
    be read at all - is treated as live so a reset never wipes beside a
    process it cannot identify. #>
    if (Test-Port) { return $true }
    if ($null -ne (Get-DashInstance)) { return $true }
    if (Test-ShadowDashAlive) { return $true }
    if (Test-Path $ProcsFile) {
        $saved = $null
        try { $saved = Get-Content $ProcsFile -Raw | ConvertFrom-Json } catch {}
        if ($null -eq $saved) { return $true }  # unreadable registry -> cannot rule out a live stack
        foreach ($name in @("filter", "query", "decide")) {
            $info = Get-ServiceEntry -Saved $saved -Key $name
            if (-not $info -or -not $info.pid) { continue }
            if ((Test-RecordLive -ProcessId $info.pid -StartedAt $info.started_at) -ne $false) { return $true }
        }
    }
    if (Test-Path $HbFile) {
        $hb = $null
        try { $hb = @(Get-Content $HbFile -Raw | ConvertFrom-Json)[0] } catch {}
        if ($null -eq $hb) { return $true }  # unreadable heartbeat -> cannot rule out a live watcher
        if ($hb -and $hb.pid) {
            if ((Test-RecordLive -ProcessId $hb.pid -StartedAt $hb.started_at) -ne $false) { return $true }
        }
    }
    if ((Test-OrphanStackProcess) -ne $false) { return $true }
    return $false
}

function Stop-Guardrail {
    <# Stop the global stop loss watcher via its heartbeat record (pid +
    started_at), matching the start-time tolerance used for stack PIDs. A
    stale or unreadable record is never trusted to kill a PID. #>
    if (-not (Test-Path $HbFile)) { return }
    $hb = $null
    try { $hb = @(Get-Content $HbFile -Raw | ConvertFrom-Json)[0] } catch {}
    if (-not $hb -or -not $hb.pid) {
        Remove-Item $HbFile -ErrorAction SilentlyContinue
        return
    }
    if (-not (Test-PidAlive -ProcessId $hb.pid)) {
        Remove-Item $HbFile -ErrorAction SilentlyContinue
        return
    }
    $recordedStart = ConvertTo-RecordedStart -StartedAt $hb.started_at
    if ($null -eq $recordedStart) {
        Lsh-Warn "Guardrail (PID $($hb.pid)) has an unreadable started_at; skipping kill, keeping $HbFile"
        return
    }
    try {
        $proc = Get-Process -Id $hb.pid -ErrorAction Stop
        $tolerance = [timespan]::FromSeconds(60)
        if ([math]::Abs(($proc.StartTime - $recordedStart).TotalSeconds) -gt $tolerance.TotalSeconds) {
            Lsh-Warn "Guardrail (PID $($hb.pid)) start time mismatch; skipping kill, keeping $HbFile"
            return
        }
        Lsh-Step "Stopping guardrail PID $($hb.pid)..."
        taskkill /F /T /PID $hb.pid 2>$null | Out-Null
        Remove-Item $HbFile -ErrorAction SilentlyContinue
        Lsh-Ok "Guardrail stopped."
    } catch {
        Lsh-Warn "Guardrail PID $($hb.pid) unavailable; keeping $HbFile"
    }
}

function Clear-RuntimeState {
    <# Wipe every regenerable runtime artifact for a first-run feel.

    NEVER touches data/orders.db (the production registry) or anything
    git-tracked: only gitignored state that processes recreate (runtime/*,
    run/, shadow stores, run-id markers). Call it only after Test-StackAlive
    reports nothing running. #>
    $removed = 0
    $files = @(Get-ChildItem $RunDir -File -ErrorAction SilentlyContinue)
    if ($files.Count -gt 0) { $files | Remove-Item -Force -ErrorAction SilentlyContinue; $removed += $files.Count }
    if (Test-Path $LegacyRunDir) {
        Remove-Item $LegacyRunDir -Recurse -Force -ErrorAction SilentlyContinue
        $removed++
    }
    # Shadow rehearsal artifacts, resolved through $ProjectPath so reset always
    # targets repository state regardless of the caller's working directory
    # (data/orders.db is never touched)
    foreach ($relative in @("data/shadow.db", "data/shadow.db-wal", "data/shadow.db-shm", # legacy cleanup: old shared per-run predecessor
                            "data/shadow_stat_verify.db", "data/shadow_stat_verify.db-wal", "data/shadow_stat_verify.db-shm",
                            "data/_preview_seed.db", "data/_preview_seed.db-wal", "data/_preview_seed.db-shm",
                            "data/_smoke_fleet.db", "data/_smoke_fleet.db-wal", "data/_smoke_fleet.db-shm",
                            "runtime/.current_run_id", "data/.current_run_id")) {
        $p = Join-Path $ProjectPath $relative
        if (Test-Path $p) { Remove-Item $p -Force -ErrorAction SilentlyContinue; $removed++ }
    }
    Get-ChildItem (Join-Path $ProjectPath "data") -Directory -Filter "shadow_stat_*" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem (Join-Path $ProjectPath "data") -File -Filter "stats_*.db*" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem (Join-Path $ProjectPath "data") -File -Filter "*.db*" -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^(overnight_validation_|\d{2}-\d{2}_\d{2}-\d{2}_shadow-\d+|\d{1,2}_shadow_|shadow_\d{8}_\d{6}_shadow-)' } | Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem $ProjectPath -File -Filter "stats_*.db*" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem (Join-Path $ProjectPath "reports") -File -Filter "*_statistics_report*" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
    Lsh-Ok "Runtime state wiped ($removed artifact(s) removed). data/orders.db untouched."
}

function Wait-ProcessGone {
    <# True when the PID is gone within the timeout. Every stop path in this
    file issues a kill whose failure is suppressed (Stop-Process
    -ErrorAction SilentlyContinue, taskkill ... 2>$null), so without this
    check a failed or raced termination still prints [OK] and returns $true --
    the menu then claims work it did not do, which is the fault this whole
    shadow-stop change exists to remove. #>
    param([int]$ProcessId, [int]$TimeoutSeconds = 10)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-PidAlive -ProcessId $ProcessId)) { return $true }
        Start-Sleep -Milliseconds 200
    }
    return (-not (Test-PidAlive -ProcessId $ProcessId))
}

function Kill-RecordedPid {
    <# Kill a recorded PID only when it is alive AND its start time matches the
    record, so a recycled PID is never touched. #>
    param([string]$Name, $TargetPid, $StartedTicks)
    if (-not $TargetPid -or -not (Test-PidAlive -ProcessId $TargetPid)) { return $false }
    try {
        $proc = Get-Process -Id $TargetPid -ErrorAction Stop
        if ($StartedTicks -and $proc.StartTime.ToUniversalTime().Ticks -ne [int64]$StartedTicks) {
            Lsh-Warn "$Name PID $TargetPid start time mismatch; not killing (recycled PID)."
            return $false
        }
        Lsh-Step "Stopping $Name PID $TargetPid..."
        taskkill /F /T /PID $TargetPid 2>$null | Out-Null
        if (-not (Wait-ProcessGone -ProcessId $TargetPid)) {
            Lsh-Warn "$Name PID $TargetPid did not exit; still running."
            return $false
        }
        Lsh-Ok "$Name stopped."
        return $true
    } catch {
        Lsh-Warn "$Name PID $TargetPid unavailable; not killed."
        return $false
    }
}

function Get-ProcessRecord {
    # A session file written before a process started has no key for it, so the
    # caller hands us $null for that slot. That is "not running", not an error.
    param([Parameter(Mandatory)][AllowNull()]$Entry)
    if (-not $Entry -or -not $Entry.pid) { return $null }
    try { $p = Get-Process -Id $Entry.pid -ErrorAction Stop } catch { return $null }
    if ($null -eq $Entry.started_ticks) { return $null }
    try {
        if ($p.StartTime.ToUniversalTime().Ticks -ne [int64]$Entry.started_ticks) { return $null }
    } catch { return $null }
    return $p
}

function Stop-ShadowSession {
    <# Stop every menu-driven shadow session before its timebox ends: the
    market screener, the rehearsal loop (shadow_run), the stop-loss watcher,
    and the shadow viewer, per run id. Recorded PIDs are killed only when
    their start times match. #>
    $killedAny = $false
    $sessionFiles = @(Get-ChildItem $RunDir -Filter "shadow-session-*.json" -ErrorAction SilentlyContinue)
    if (Test-Path $ShadowSessionFile) { $sessionFiles += Get-Item $ShadowSessionFile }
    foreach ($sf in $sessionFiles) {
        $s = $null
        try { $s = Get-Content $sf.FullName -Raw | ConvertFrom-Json } catch {}
        if ($s) {
            if ($s.screener.pid)   { $res = Kill-RecordedPid -Name "shadow screener" -TargetPid $s.screener.pid   -StartedTicks $s.screener.started_ticks; if ($res) { $killedAny = $true } }
            if ($s.loop.pid)       { $res = Kill-RecordedPid -Name "shadow loop"      -TargetPid $s.loop.pid       -StartedTicks $s.loop.started_ticks; if ($res) { $killedAny = $true } }
            if ($s.watcher.pid)    { $res = Kill-RecordedPid -Name "shadow watcher"   -TargetPid $s.watcher.pid    -StartedTicks $s.watcher.started_ticks; if ($res) { $killedAny = $true } }
            if ($s.observer.pid)   { $res = Kill-RecordedPid -Name "statistics observer" -TargetPid $s.observer.pid -StartedTicks $s.observer.started_ticks; if ($res) { $killedAny = $true } }
        }
        Remove-Item $sf.FullName -ErrorAction SilentlyContinue
    }
    $tsRes = Stop-TsBridge
    if ($tsRes) { $killedAny = $true }
    $dashRes = Stop-ShadowDashboard
    if ($dashRes) { $killedAny = $true }
    return $killedAny
}

function Reset-Environment {
    <# Full fresh-start reset: verify -> stop -> wipe -> verify clean ->
    start the requested mode. Mode "none" stops after the clean proof;
    "shadow" also brings up the shadow dashboard (and a rehearsal run when
    -Minutes > 0); "live" starts the live stack and REQUIRES $Force because
    Decide & Execute rests real maker bids. #>
    param(
        [Parameter(Mandatory)][string]$Mode,
        [switch]$Force
    )
    Lsh-Banner -Title "RESET & FRESH START" -Subtitle "verify -> stop -> wipe -> start ($Mode)"

    # 1 · VERIFY: what is alive right now
    Lsh-Phase -Text "PHASE 1 · VERIFY"
    $found = @()
    if (Test-Port) { $found += "port :$LivePort (PID $(Get-PortPid))" }
    $dash = Get-DashInstance;  if ($dash) { $found += "live dashboard PID $($dash.pid)" }
    foreach ($sd in @(Get-ShadowDashInstances | Where-Object { $_.alive })) { $found += "shadow dashboard $($sd.run_id) PID $($sd.pid) :$($sd.port)" }
    if (Test-Path $ProcsFile) {
        try {
            $saved = Get-Content $ProcsFile -Raw | ConvertFrom-Json
            foreach ($name in @("filter", "query", "decide")) {
                $info = Get-ServiceEntry -Saved $saved -Key $name
                if ($info -and $info.pid -and (Test-PidAlive -ProcessId $info.pid)) { $found += "$name PID $($info.pid)" }
            }
        } catch { Lsh-Warn "Could not read $ProcsFile; stop will keep it in place if unreadable." }
    }
    if (Test-Path $HbFile) {
        try {
            $hb = @(Get-Content $HbFile -Raw | ConvertFrom-Json)[0]
            if ($hb -and $hb.pid -and (Test-PidAlive -ProcessId $hb.pid)) { $found += "guardrail PID $($hb.pid)" }
        } catch {}
    }
    if ($found.Count -gt 0) { Lsh-Step "Alive: $(($found -join ', ')) - stopping, then wiping." }
    else                   { Lsh-Ok "Nothing spread-hunter is running (port free, no stack PIDs, no guardrail)." }

    # 2 · STOP (API stop when a dashboard answers; recorded PIDs otherwise)
    Lsh-Phase -Text "PHASE 2 · STOP"
    if (Test-StackAlive) {
        Stop-BotStack
        $null = Stop-ShadowSession   # recorded rehearsal loop + watcher + viewer
        $null = Stop-ShadowRun       # any unrecorded rehearsal loop still running
        Stop-Guardrail
        $null = Stop-Dashboard
        $null = Stop-ShadowDashboard
    } else {
        Stop-Guardrail  # clears a stale heartbeat record even when nothing is alive
    }

    # 3 · VERIFY STOPPED before wiping (never wipe beside a live stack)
    Lsh-Phase -Text "PHASE 3 · VERIFY STOPPED"
    if (Test-StackAlive) {
        Lsh-Fail "Refusing to wipe: a spread-hunter process is still alive. Re-run reset, or free :$LivePort manually."
        return $false
    }

    # 4 · WIPE
    Lsh-Phase -Text "PHASE 4 · WIPE"
    Clear-RuntimeState

    # 5 · VERIFY CLEAN (pidfiles are gone by construction; still guard against
    # an unregistered orphan service so we never declare clean beside one)
    Lsh-Phase -Text "PHASE 5 · VERIFY CLEAN"
    if (Test-Port) {
        Lsh-Fail "Port $LivePort is still LISTENING (PID $(Get-PortPid)) and not owned by this menu. Free it, then start."
        return $false
    }
    if ((Test-StackAlive) -ne $false) {
        Lsh-Fail "Refusing to declare the environment clean: an unregistered spread-hunter service is still alive. Stop it, then start."
        return $false
    }
    Lsh-Ok "Environment verified clean - no blockers, no contradictions."

    # 6 · START
    Lsh-Phase -Text "PHASE 6 · START ($Mode)"
    switch ($Mode) {
        "shadow" {
            # Short, readable run names: NN_shadow_DD-MM_HH-mm.db where NN is the
            # run id — it ties this db to every order/close/stats record stamped
            # with the same $ShadowRunId. Count restarts at 01 on a clean reset.
            $stamp = Get-Date -Format "dd-MM_HH-mm"
            $dbSeq = Get-NextShadowSeq
            $ShadowRunId = "shadow-$dbSeq"
            $ShadowDbPath = Join-Path $ProjectPath "data/${dbSeq}_shadow_${stamp}.db"
            $StatsDbPath = Join-Path $ProjectPath "data/stats_${stamp}_${ShadowRunId}.db"
            if (Start-ShadowDashboard) {
                if ($Minutes -gt 0) {
                    # Ordered rehearsal boot (no signer is ever loaded, so it
                    # spends nothing):
                    #  1. MARKET SCREENER fills the universe feed first, so the
                    #     loop has real screened markets to quote.
                    #  2. The rehearsal LOOP (shadow_run, the "executor") starts
                    #     after the universe exists.
                    #  3. The GLOBAL STOP-LOSS watcher engages on the loop's
                    #     ring once it appears.
                    Lsh-Step "Running the market screener to fill the universe feed..."
                    $seed = Start-Process -FilePath "python" `
                        -ArgumentList "-m", "scripts.filter_markets" `
                        -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
                        -RedirectStandardOutput (Join-Path $RunDir "screener_seed-$ShadowRunId.out.log") `
                        -RedirectStandardError (Join-Path $RunDir "screener_seed-$ShadowRunId.err.log")
                    $feed = Join-Path $ProjectPath "runtime/markets.json"
                    $deadline = (Get-Date).AddSeconds(120)
                    while (-not (Test-Path $feed) -and (Get-Date) -lt $deadline) {
                        Start-Sleep -Milliseconds 1000
                        if ($seed.HasExited) { break }
                    }
                    if (Test-Path $feed) { Lsh-Ok "Market screener filled the universe feed (markets.json)." }
                    else { Lsh-Warn "Market screener did not produce markets.json in time - the rehearsal may quote nothing." }
                    # Continuous screener process (refreshes the feed on its interval).
                    $screener = Start-Process -FilePath "python" `
                        -ArgumentList "-m", "scripts.filter_loop" `
                        -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
                        -RedirectStandardOutput (Join-Path $RunDir "screener-$ShadowRunId.out.log") `
                        -RedirectStandardError (Join-Path $RunDir "screener-$ShadowRunId.err.log")
                    Lsh-Ok "Market screener loop running (PID $($screener.Id))."
                    # The rehearsal loop (the executor) - started now that the
                    # universe exists.
                    Lsh-Step "Starting the rehearsal loop (python -m core_brain.shadow_run --minutes $Minutes --db $ShadowDbPath)..."
                    # shadow_run is the ONLY child that declares itself a
                    # rehearsal, so it is the only one that may see
                    # HUNTER_PAIR_COST_CAP. Re-inject it here; the wrapper
                    # strips it again before the observer launches.
                    $shadowRun = Invoke-WithRehearsalTrialEnv {
                        Start-Process -FilePath "python" `
                            -ArgumentList "-m", "core_brain.shadow_run", "--minutes", "$Minutes", "--db", $ShadowDbPath, "--run-id", $ShadowRunId `
                            -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
                            -RedirectStandardOutput (Join-Path $RunDir "shadow_run-$ShadowRunId.out.log") `
                            -RedirectStandardError (Join-Path $RunDir "shadow_run-$ShadowRunId.err.log")
                    }
                    Lsh-Ok "Rehearsal loop running (PID $($shadowRun.Id), $Minutes minute(s)) - dashboard updates live from $ShadowDbPath."
                    $observer = Start-Process -FilePath "python" `
                        -ArgumentList "-m", "core_brain.statistics_observer", "--mode", "shadow", "--watch", $ShadowDbPath, "--run-id", $ShadowRunId, "--data-dir", (Join-Path $ProjectPath "data"), "--interval", "5", "--max-hours", (($Minutes / 60) + 0.08) `
                        -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
                        -RedirectStandardOutput (Join-Path $RunDir "statistics_observer-$ShadowRunId.out.log") `
                        -RedirectStandardError (Join-Path $RunDir "statistics_observer-$ShadowRunId.err.log")
                    Lsh-Ok "Statistics observer running (PID $($observer.Id), db=$StatsDbPath)."
                    # Scope the stop-loss watcher to this rehearsal's ring (the
                    # newest shadow-*.jsonl the loop just began writing). No
                    # run-scoped id is forced, so the dashboard resolves the run
                    # id itself and displays the rehearsal.
                    $ring = $null
                    $deadline = (Get-Date).AddSeconds(30)
                    while ($null -eq $ring -and (Get-Date) -lt $deadline) {
                        $ringFile = Get-ChildItem $RunDir -Filter "shadow-*.jsonl" -ErrorAction SilentlyContinue |
                            Sort-Object LastWriteTime -Descending | Select-Object -First 1
                        if ($ringFile) { $ring = $ringFile.FullName; break }
                        Start-Sleep -Milliseconds 500
                        if ($shadowRun.HasExited) { break }
                    }
                    $guardrail = $null
                    if ($ring) {
                        $guardrail = Start-Process -FilePath "python" `
                            -ArgumentList "-m", "scripts.global_stop_loss",                            "--db", $ShadowDbPath, "--ring", $ring `
                            -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
                            -RedirectStandardOutput (Join-Path $RunDir "guardrail-$ShadowRunId.out.log") `
                            -RedirectStandardError (Join-Path $RunDir "guardrail-$ShadowRunId.err.log")
                        Lsh-Ok "Stop-loss watcher engaged (PID $($guardrail.Id)) on $ring."
                    } else {
                        Lsh-Warn "Could not resolve the rehearsal ring; stop-loss watcher not engaged."
                    }
                    # Session record so option 5 stops every process; screener
                    # and watcher self-stop at the wall clock via a detached timer
                    # (the loop self-exits at $Minutes).
                    $session = [ordered]@{
                        started = (Get-Date).ToString("o")
                        started_ticks = (Get-Date).ToUniversalTime().Ticks
                        run_id = $ShadowRunId
                        ShadowRunId = $ShadowRunId
                        shadow_db = $ShadowDbPath
                        ShadowDbPath = $ShadowDbPath
                        stats_db = $StatsDbPath
                        StatsDbPath = $StatsDbPath
                        report_path = (Join-Path $ProjectPath "reports")
                        screener = [ordered]@{ pid = $screener.Id; started_ticks = $screener.StartTime.ToUniversalTime().Ticks }
                        loop = [ordered]@{ pid = $shadowRun.Id; started_ticks = $shadowRun.StartTime.ToUniversalTime().Ticks }
                        observer = [ordered]@{ pid = $observer.Id; started_ticks = $observer.StartTime.ToUniversalTime().Ticks }
                        watcher = if ($guardrail) { [ordered]@{ pid = $guardrail.Id; started_ticks = $guardrail.StartTime.ToUniversalTime().Ticks } } else { $null }
                        ring = $ring
                    }
                    $session | ConvertTo-Json -Depth 5 | Set-Content -Path (Get-ShadowSessionFile -RunId $ShadowRunId) -Encoding UTF8
                    $killSec = [int]($Minutes * 60)
                    $timerTargets = @(@{ id = [int]$screener.Id; ticks = $screener.StartTime.ToUniversalTime().Ticks })
                    if ($guardrail) { $timerTargets += @{ id = [int]$guardrail.Id; ticks = $guardrail.StartTime.ToUniversalTime().Ticks } }
                    $killCmd = "Start-Sleep -Seconds $killSec"
                    foreach ($t in $timerTargets) {
                        # Kill only when the PID still carries the recorded start time, so a
                        # recycled PID within the window is never force-stopped.
                        $killCmd += "; `$p = Get-Process -Id $($t.id) -ErrorAction SilentlyContinue; if (`$p -and `$p.StartTime.ToUniversalTime().Ticks -eq $($t.ticks)) { Stop-Process -Id $($t.id) -Force -ErrorAction SilentlyContinue }"
                    }
                    Start-Process -FilePath "powershell" `
                        -ArgumentList "-NoProfile", "-Command", $killCmd `
                        -WindowStyle Hidden
                    Lsh-Ok "Shadow session running: screener + loop + stop-loss watcher. Stop Bot + Dashboard (5 / stop-shadow) ends it early."
                }
                # Open the browser only after every worker is up and the screener
                # has written its universe + pipeline, so the dashboard's first
                # frame already shows the screener feed instead of blank/STALLED.
                Start-Sleep -Seconds 3
                $openedUrl = Open-ShadowDashboard
                Lsh-Ok "Opened $openedUrl in default browser (shadow db=$ShadowDbPath)."
                if ($Watch -and $Minutes -gt 0) {
                    # -Watch: stream the rehearsal loop's stderr to THIS console
                    # instead of backgrounding it silently. The loop logs its
                    # [QUOTING] lines to its per-instance log. Ctrl-C ends
                    # the watcher; the session still self-stops after $Minutes
                    # min (detached timer) or via option 5 / stop-shadow.
                    Write-Host ""
                    Lsh-Step "Watching the rehearsal loop live (Ctrl-C ends the watcher; session self-stops after $Minutes min or via stop-shadow)."
                    Get-Content (Join-Path $RunDir "shadow_run-$ShadowRunId.err.log") -Wait -ErrorAction SilentlyContinue
                }
            } else {
                return $false
            }
        }
        "statistical" {
            # Short, readable run names: NN_shadow_DD-MM_HH-mm.db where NN is the
            # run id (shared with the shadow-run flow; resets to 01 on a clean
            # reset). It ties this db to the run records, stats store and report.
            $stamp = Get-Date -Format "dd-MM_HH-mm"
            $dbSeq = Get-NextShadowSeq
            $ShadowRunId = "shadow-$dbSeq"
            $ShadowDbPath = Join-Path $ProjectPath "data/${dbSeq}_shadow_${stamp}.db"
            $StatsDbPath = Join-Path $ProjectPath "data/stats_${stamp}_${ShadowRunId}.db"
            $reportPath = Join-Path $ProjectPath "reports/validation_${stamp}_${ShadowRunId}"
            $runHours = if ($Hours -gt 0) { $Hours } else { 12.0 }
            Lsh-Ok "Run id:   $ShadowRunId"
            Lsh-Ok "Database: $ShadowDbPath"
            Lsh-Ok "Report:   $reportPath"
            if (Start-ShadowDashboard) {
                Lsh-Step "Running the market ranker before starting the validation loop..."
                # Same cwd pin as the live account sweep above: `python -m`
                # resolves against the caller's location, not the repo.
                Push-Location $ProjectPath
                try {
                    & python -m scripts.rank_markets
                } finally {
                    Pop-Location
                }
                if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $ProjectPath "runtime/markets.json"))) {
                    Lsh-Fail "Market ranking failed; validation loop was not started. See runtime/rerank.log or ranker output."
                    return $false
                }
                Lsh-Ok "Market feed ready (runtime/markets.json)."
                $screener = Start-Process -FilePath "python" -ArgumentList "-m", "scripts.filter_loop" -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
                    -RedirectStandardOutput (Join-Path $RunDir "validation_screener.out.log") -RedirectStandardError (Join-Path $RunDir "validation_screener.err.log")
                Register-StackService -Key "filter" -Process $screener
                Lsh-Ok "Market screener started (PID $($screener.Id))."
                # The sample-size gate's close target lives in config
                # (stat_gate_target_closes, default 60): the harness reads it
                # itself and stops on the close count, not just the clock.
                $validation = Start-Process -FilePath "python" -ArgumentList "-m", "statistical_validation_run", "--max-hours", "$runHours", "--db", $ShadowDbPath, "--report", $reportPath, "--run-id", $ShadowRunId `
                    -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
                    -RedirectStandardOutput (Join-Path $RunDir "statistical_validation.out.log") -RedirectStandardError (Join-Path $RunDir "statistical_validation.err.log")
                Lsh-Ok "Validation loop started (PID $($validation.Id), $runHours hour(s))."
                $observer = Start-Process -FilePath "python" -ArgumentList "-m", "core_brain.statistics_observer", "--mode", "shadow", "--watch", $ShadowDbPath, "--run-id", $ShadowRunId, "--data-dir", (Join-Path $ProjectPath "data"), "--interval", "5", "--max-hours", ($runHours + 0.08) -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
                    -RedirectStandardOutput (Join-Path $RunDir "validation_observer.out.log") -RedirectStandardError (Join-Path $RunDir "validation_observer.err.log")
                Lsh-Ok "Statistics observer started (PID $($observer.Id))."
                # The loop names its ring shadow-<run_id>.jsonl (the id is
                # already shadow-prefixed; the runner never double-prefixes).
                $ring = Join-Path $RunDir ("shadow-{0}.jsonl" -f ($ShadowRunId -replace "^shadow-", ""))
                $guardrail = Start-Process -FilePath "python" -ArgumentList "-m", "scripts.global_stop_loss", "--db", $ShadowDbPath, "--ring", $ring -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru `
                    -RedirectStandardOutput (Join-Path $RunDir "validation_guardrail.out.log") -RedirectStandardError (Join-Path $RunDir "validation_guardrail.err.log")
                Lsh-Ok "Stop-loss watcher started (PID $($guardrail.Id))."
                $session = [ordered]@{ started=(Get-Date).ToString("o"); run_id=$ShadowRunId; shadow_db=$ShadowDbPath; stats_db=$StatsDbPath; report_path=$reportPath; loop=[ordered]@{pid=$validation.Id; started_ticks=$validation.StartTime.ToUniversalTime().Ticks}; screener=[ordered]@{pid=$screener.Id; started_ticks=$screener.StartTime.ToUniversalTime().Ticks}; observer=[ordered]@{pid=$observer.Id; started_ticks=$observer.StartTime.ToUniversalTime().Ticks}; watcher=[ordered]@{pid=$guardrail.Id; started_ticks=$guardrail.StartTime.ToUniversalTime().Ticks}; ring=$ring }
                try {
                    $bridgeUrl = Get-ShadowDashUrl $ShadowRunId
                    $tsBridge = Start-TsBridge -LogPrefix "ts-bridge-$ShadowRunId" -PyDashUrl $bridgeUrl
                    $session.ts_bridge = [ordered]@{ pid = $tsBridge.pid; port = $tsBridge.port }
                    Lsh-Ok "TS dashboard bridge on http://127.0.0.1:8800 (PID $($tsBridge.pid)) -> proxies $bridgeUrl"
                } catch {
                    Lsh-Warn ".TS bridge did not start: $($_.Exception.Message). Dashboard still available at $(Get-ShadowDashUrl $ShadowRunId)."
                }
                $session | ConvertTo-Json -Depth 5 | Set-Content -Path (Get-ShadowSessionFile -RunId $ShadowRunId) -Encoding UTF8
                Write-Host ""
                Write-ProfileRuleWithText -Text "OVERNIGHT STATISTICS RUNNING" -Style "Success"
                Write-ProfileSuccess -Message "Validation loop" -Detail "(PID $($validation.Id)) - rotates for $runHours hour(s)"
                Write-ProfileSuccess -Message "Market screener" -Detail "(PID $($screener.Id)) - refreshes the market feed"
                Write-ProfileSuccess -Message "Statistics observer" -Detail "(PID $($observer.Id)) - snapshots every 5s"
                Write-ProfileSuccess -Message "Stop-loss watcher" -Detail "(PID $($guardrail.Id)) - flags repeat-exit / over-cap pairs"
                Write-ProfileSuccess -Message "Dashboard" -Detail "$ShadowDashUrl (db=$ShadowDbPath)"
                Write-ProfileInfo -Message "Report lands at" -Detail $reportPath
                Write-ProfileInfo -Message "Check it" -Detail ".\scripts\spread-hunter-menu.ps1 status"
                Write-ProfileInfo -Message "Stop it early" -Detail ".\scripts\spread-hunter-menu.ps1 stop-shadow"
                Write-Host ""
                # Open the browser only after every worker is up and the screener
                # feed exists, so the dashboard's first frame already shows the
                # validation/rehearsal data instead of a blank/STALLED state.
                Start-Sleep -Seconds 3
                $openedUrl = Open-ShadowDashboard
                Lsh-Ok "Opened $openedUrl in default browser (validation db=$ShadowDbPath)."
            } else { return $false }
        }
        "live" {
            if (-not $Force) {
                Lsh-Fail "Live start requires explicit confirmation: use -Yes (CLI) or the typed START confirm (menu)."
                return $false
            }
            if (Start-Dashboard) { Start-BotStack }
        }
        default {
            Lsh-Ok "Ready. Options: 1 live start, 4 shadow run, 7 global stop & clean, 8 status, 9 statistical shadow."
        }
    }
    return $true
}

# ── Menu ──
function Show-MenuGrid {
    $cInfo    = Get-ProfileColor -Name Info
    $cStrong  = Get-ProfileColor -Name Strong
    $cNeutral = Get-ProfileColor -Name Neutral

    Write-Host "  SPREAD HUNTER - CONTROL CENTER" -ForegroundColor $cInfo
    Write-Host ("" + ("─" * 30)) -ForegroundColor (Get-ProfileColor -Name Border)
    Write-Host ""

    # Grouped levers: badge (cyan number) + icon + label + description. One
    # wording style: Start/Open = "Start the <target>", Stop = "Stop the
    # <target>", Reset+ = "Stop all, wipe state, then start the <target>".
    # Live actions that rest real maker bids carry "; type START" as the cue.
    $groups = @(
        @{ Header = "🟢 LIVE - real maker bids"; Items = @(
            @{ K = "1"; Icon = "▶"; IconColor = "Success"; V = "Start Bot + Dashboard";     D = "Stops, wipes data & starts fresh bot + dashboard" }
            @{ K = "2"; Icon = "■"; IconColor = "Error";   V = "Stop Bot + Dashboard";      D = "Stops bot processes and dashboard" }
            @{ K = "3"; Icon = "◉"; IconColor = "Warning"; V = "Host & Open Dashboard";     D = "Releases our other-env :8799 dashboard (no wipe), hosts live DB & opens browser" }
        ) }
        @{ Header = "🥷 SHADOW - rehearsal, spends nothing"; Items = @(
            @{ K = "4"; Icon = "▷"; IconColor = "Info";    V = "Start Bot + Dashboard";     D = "Stops, wipes data & starts fresh rehearsal (loop + stop loss); prompts minutes" }
            @{ K = "5"; Icon = "□"; IconColor = "Neutral"; V = "Stop Bot + Dashboard";      D = "Stops rehearsal loop, watcher and dashboard" }
            @{ K = "6"; Icon = "◎"; IconColor = "Info";    V = "Host & Open Dashboard";     D = "Releases our other-env :8799 dashboard (no wipe), hosts shadow DB & opens browser" }
            @{ K = "r"; Icon = "↻"; IconColor = "Info";    V = "Resume Shadow Run";        D = "Reopens the pinned 01_shadow_12-09_00-58.db DB as shadow-01, restarts loop/observer/watcher, reattaches dashboard (no wipe)" }
        ) }
        @{ Header = "MAINTENANCE & STATUS"; Items = @(
            @{ K = "7"; Icon = "⎚"; IconColor = "Warning"; V = "Global Stop & Clean";       D = "Kills all bot processes/dashboards, wipes data, verifies" }
            @{ K = "8"; Icon = "≡"; IconColor = "Info";    V = "Check System Status";        D = "Static Status page" }
            @{ K = "9"; Icon = "▣"; IconColor = "Info";    V = "Overnight Statistics";         D = "Run shadow + statistics + dashboard for hours" }
        ) }
    )

    foreach ($g in $groups) {
        Write-Host ("  " + $g.Header) -ForegroundColor $cInfo
        foreach ($it in $g.Items) {
            Write-Host "   " -NoNewline
            Write-Host (" {0} " -f $it.K) -BackgroundColor (Get-ProfileColor -Name Border) -ForegroundColor Black -NoNewline
            Write-Host ("  {0} " -f $it.Icon) -ForegroundColor (Get-ProfileColor -Name $it.IconColor) -NoNewline
            Write-Host ("{0,-26}" -f $it.V) -ForegroundColor $cStrong -NoNewline
            Write-Host $it.D -ForegroundColor $cNeutral
        }
        Write-Host ""
    }
    Write-Host "  q  × Exit · Return to PowerShell" -ForegroundColor $cNeutral
}

function Invoke-LiveAction {
    param([string]$Key)
    switch ($Key) {
        "1" {
            # Live start preflights: stop everything, wipe, verify, then start
            # (real maker bids). Interactive requires typed START.
            if ($Action -eq "") {
                Write-Host ""
                $confirm = Read-Host "Type START to confirm starting the LIVE bot stack (real maker bids)"
                if ($confirm -ne "START") { Lsh-Warn "Start cancelled."; return }
            }
            $null = Reset-Environment -Mode "live" -Force
        }
        "2" {
            Stop-BotStack
            $null = Stop-Dashboard
            Lsh-Ok "Live stack is down."
        }
        "3" {
            $null = Open-Dashboard -Mode "live"
        }
        "4" {
            $mins = $Minutes
            if ($Action -eq "") {
                $resp = Read-Host "  Minutes for this shadow run (default 5)?"
                if ($resp -and $resp -match '^\s*[0-9]+(?:\.[0-9]+)?\s*$') { $mins = [double]$resp }
                $confirm = Read-Host "  Preflight: stop everything, wipe data, then run a $mins-minute shadow rehearsal? data/orders.db is kept. [y/N]"
                if ($confirm -notmatch '^[yY]') { Lsh-Warn "Shadow start cancelled."; return }
            }
            if (-not $mins -or $mins -le 0) { $mins = 5.0 }
            $script:Minutes = [int]$mins
            $null = Reset-Environment -Mode "shadow"
        }
        "9" {
            $hrs = if ($Hours -gt 0) { $Hours } else { 12.0 }
            if ($Action -eq "") {
                $confirm = Read-Host "  Start a $hrs-hour statistical shadow run with its own database and dashboard? [y/N]"
                if ($confirm -notmatch '^[yY]') { Lsh-Warn "Statistical run cancelled."; return }
            }
            $script:Hours = $hrs
            $null = Reset-Environment -Mode "statistical"
        }
        "5" {
            $sessStopped = Stop-ShadowSession
            $runStopped = Stop-ShadowRun
            if ($sessStopped -or ($runStopped -eq $true)) {
                Lsh-Ok "Shadow session stopped."
            } elseif ($null -eq $runStopped) {
                Lsh-Warn "Shadow session stop finished, but the process scan was inconclusive; verify with option 1."
            } else {
                Lsh-Warn "No shadow session or process is running."
            }
        }
        "6" {
            $null = Open-Dashboard -Mode "shadow"
        }
        "7" {
            if ($Action -eq "") {
                $confirm = Read-Host "  GLOBAL STOP & CLEAN: kill all bot processes/dashboards and wipe data? data/orders.db is kept. [y/N]"
                if ($confirm -notmatch '^[yY]') { Lsh-Warn "Clean cancelled."; return }
            }
            $null = Reset-Environment -Mode "none"
        }
        "8" { Show-Status }
        "r" {
            if ($Action -eq "") {
                $store = if ($ResumeDb -ne "") { $ResumeDb } else { "data/01_shadow_12-09_00-58.db (shadow-01)" }
                $confirm = Read-Host "  Resume rehearsal ($store, no wipe, dashboard reattached)? [y/N]"
                if ($confirm -notmatch '^[yY]') { Lsh-Warn "Resume cancelled."; return }
            }
            $null = Resume-ShadowRun
        }
        "q" { Write-Host "Exiting Spread Hunter menu." -ForegroundColor (Get-ProfileColor -Name Neutral); exit 0 }
        default {
            Lsh-Warn "Invalid selection: $Key (choose 1-9, or q)."
            Start-Sleep -Seconds 1
        }
    }
}

function Read-MenuChoice {
    <# Single-keypress menu input: 1-9/q run at once, no Enter needed.
       Non-printable keys (arrows, etc.) are ignored; Enter exits.
       Falls back to Read-Host when stdin is redirected or non-interactive. #>
    try {
        while ($true) {
            $key = [Console]::ReadKey($true)
            $ch = "$($key.KeyChar)"
            if ($ch -eq "`r" -or $ch -eq "`n") { Write-Host ""; return "" }
            if ([string]::IsNullOrWhiteSpace($ch) -or [char]::IsControl($ch[0])) { continue }
            Write-Host $ch
            return $ch.Trim().ToLower()
        }
    } catch {
        try { $fallback = Read-Host }
        catch {
            Write-Host "No interactive console; pass a menu option directly (e.g. .\scripts\spread-hunter-menu.ps1 8)."
            return ""
        }
        if ($null -eq $fallback) { return "" }
        return $fallback.Trim().ToLower()
    }
}

# ── Dispatch ──
if ($Action -ne "") {
    $actionMap = @{
        "start"        = "1"
        "live"         = "1"
        "live-start"   = "1"
        "stop"         = "2"
        "live-stop"    = "2"
        "host"         = "3"
        "host-live"    = "3"
        "open"         = "3"
        "dashboard"    = "3"
        "dash"         = "3"
        "shadow"       = "4"
        "shadow-start" = "4"
        "shadow-run"   = "4"
        "stop-shadow"  = "5"
        "shadow-stop"  = "5"
        "shadow-host"  = "6"
        "open-shadow"  = "6"
        "shadow-open"  = "6"
        # Pinned resume: option R always reopens 01_shadow_12-09_00-58.db as shadow-01.
        "resume"       = "r"
        "shadow-resume" = "r"
        "resume-shadow" = "r"
        "resume-01"    = "r"
        "reset"        = "7"
        "clean"        = "7"
        "get"          = "7"
        "status"       = "8"
        "statistical"  = "9"
        "stats"        = "9"
        "overnight"    = "9"
        "statistical-run" = "9"
    }
    $key = $Action.Trim().ToLower()
    if ($actionMap.ContainsKey($key)) { $key = $actionMap[$key] }

    # Menu numbers work directly too: `.\scripts\spread-hunter-menu.ps1 8`
    # runs option 8 at once, no menu shown. Reject anything else here so a
    # typo fails fast instead of falling into the "invalid selection" path.
    if (@("1","2","3","4","5","6","7","8","9","r","q") -notcontains $key) {
        Write-Host "ERROR: Unknown action '$Action' (use 1-9, q, or a name like start/stop/status)" -ForegroundColor Red
        exit 1
    }

    # Require -Yes flag for non-interactive LIVE start (key 1)
    if ($key -eq "1" -and -not $Yes) {
        Write-Host "ERROR: Non-interactive LIVE start requires explicit -Yes flag" -ForegroundColor Red
        Write-Host "Usage: .\scripts\spread-hunter-menu.ps1 start -Yes"
        exit 1
    }

    Invoke-LiveAction $key
    exit 0
}

Lsh-Banner -Title "SPREAD HUNTER - CONTROL CENTER"
Show-MenuGrid
Write-Host "  Select " -ForegroundColor (Get-ProfileColor -Name Text) -NoNewline
Write-Host "[1-9, q]" -ForegroundColor (Get-ProfileColor -Name Command) -NoNewline
Write-Host " › " -ForegroundColor (Get-ProfileColor -Name Highlight) -NoNewline
$choice = Read-MenuChoice
if ($null -eq $choice -or $choice -eq "") { exit 0 }
Invoke-LiveAction $choice
exit 0
