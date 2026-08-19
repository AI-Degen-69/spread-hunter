# SPREAD HUNTER LIVE DASHBOARD START SCRIPT
# Launches the Live Execution Monitor (:8799) in the background, detached from the shell.
#
# Usage:
#   .\scripts\live-start.ps1
#
# The dashboard is launched via Start-Process (WindowStyle Hidden, output redirected to
# logs/), so it is NOT attached to the launching PowerShell session: it keeps running
# after the shell exits and owns no console handle.

$ErrorActionPreference = "Stop"
$ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectPath
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectPath "logs") | Out-Null

. (Join-Path $PSScriptRoot "theme-loader.ps1")
. (Join-Path $PSScriptRoot "live-procs.ps1")

$LiveDir  = Join-Path $ProjectPath "live"
$DashPort = 8799
$OutLog   = Join-Path $ProjectPath "logs/live_dash.out.log"
$ErrLog   = Join-Path $ProjectPath "logs/live_dash.err.log"

Write-ProfileBanner -Title "SPREAD HUNTER - LIVE DASHBOARD LAUNCHER" `
                    -Subtitle "Live Execution Monitor | Port :$DashPort | Background (detached)" `
                    -Style "Info"
Write-Host ""

Write-ProfileInfo -Message "[1/4] Checking for a prior live dashboard instance and port $DashPort..."

$stopped = Stop-LiveDashInstance
if ($stopped -gt 0) {
    Write-ProfileWarning -Message "Stopped prior live dashboard instance:" -Detail "1 process tree terminated"
}

if (netstat -ano | Select-String ":$DashPort\s+.*LISTENING") {
    Write-ProfileError -Message "Port $DashPort Conflict:" -Detail "Port $DashPort is occupied by another process." -Suggestion "Run live-stop or free port $DashPort."
    throw "Port $DashPort occupied."
}

Write-ProfileInfo -Message "[2/4] Launching live dashboard (detached, windowless)..."

# -m dash.live_dash requires live/ as the working directory (dash package lives under live/).
$dash = Start-Process -FilePath "python" `
    -ArgumentList "-m", "dash.live_dash", "--port", "$DashPort" `
    -WorkingDirectory $LiveDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError  $ErrLog

Save-LiveDashInstance -DashProcess $dash

Write-ProfileInfo -Message "[3/4] Waiting for uvicorn to bind port $DashPort..."
for ($i = 4; $i -gt 0; $i--) {
    Start-Sleep -Seconds 1
}

$dash.Refresh()
if ($dash.HasExited) {
    Write-ProfileError -Message "Live dashboard startup failed:" -Detail "Process exited immediately (check logs/live_dash.err.log)"
    throw "Live dashboard exited."
}

$inst = Get-LiveDashInstance
if ($null -eq $inst) {
    Write-ProfileError -Message "Live dashboard not recorded:" -Detail "PID file missing or process not found."
    throw "Live dashboard not recorded."
}

Write-Host ""
Write-ProfileSuccess -Message "Live Execution Dashboard operational!" -Detail "PID $($inst.pid) | http://127.0.0.1:$DashPort"
Write-ProfileKeyValue -Key "Dashboard URL" -Value "http://127.0.0.1:$DashPort" -Style "Link" -KeyWidth 20
Write-ProfileKeyValue -Key "Logs (out)"    -Value "logs\live_dash.out.log" -Style "Command" -KeyWidth 20
Write-ProfileKeyValue -Key "Logs (err)"    -Value "logs\live_dash.err.log" -Style "Command" -KeyWidth 20
Write-ProfileKeyValue -Key "Stop Command"  -Value "live-stop" -Style "Highlight" -KeyWidth 20
Write-Host ""

try {
    Start-Process "http://127.0.0.1:$DashPort"
    Write-ProfileSuccess -Message "Opened http://127.0.0.1:$DashPort in default browser."
} catch {
    Write-ProfileWarning -Message "Could not open browser automatically:" -Detail "Navigate to http://127.0.0.1:$DashPort"
}
Write-Host ""
