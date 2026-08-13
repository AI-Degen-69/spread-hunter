# SPREAD HUNTER STOP SCRIPT
# Stops the Spread Hunter process tree started by this repository checkout.
#
# Usage:
#   .\scripts\hunter-stop.ps1            # stop the recorded Spread Hunter instance
#   .\scripts\hunter-stop.ps1 -Strays    # also stop hunter-shaped processes not owned by this checkout
param([switch]$Strays)

$ProjectPath = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot "theme-loader.ps1")
. (Join-Path $PSScriptRoot "hunter-procs.ps1")

Write-ProfileBanner -Title "SPREAD HUNTER - SHUTDOWN" `
                    -Subtitle "Terminating Strategy Supervisor and Child Process Trees" `
                    -Style "Warning"
Write-Host ""

Write-ProfileInfo -Message "[1/2] Terminating recorded supervisor and child processes..."

$stopped = Stop-HunterInstance
if ($stopped -gt 0) {
    Write-ProfileWarning -Message "Stopped process tree(s):" -Detail "$stopped instance(s) terminated"
}

$unowned = @(Find-HunterStrays)

if ($unowned.Count -eq 0) {
    if ($stopped -eq 0) { 
        Write-ProfileError -Message "No active Spread Hunter processes were running."
    } else {
        Write-ProfileSuccess -Message "All Spread Hunter processes stopped successfully."
    }
    return
}

if (-not $Strays) {
    Write-Host ""
    Write-ProfileWarning -Message "Found unowned hunter-shaped processes:" -Detail "$($unowned.Count) process(es) NOT owned by this checkout"
    $unowned | ForEach-Object {
        $cl = ($_.CommandLine -replace '\s+', ' ')
        Write-ProfileNeutral -Message "PID $($_.ProcessId):" -Detail "$($cl.Substring(0, [Math]::Min(75, $cl.Length)))"
    }
    Write-ProfileInfo -Message "Left running." -Detail "To terminate unowned processes, run: hunter-stop -Strays"
    return
}

Write-ProfileWarning -Message "[2/2] -Strays specified: terminating unowned processes..." -Detail "$($unowned.Count) process(es)"
$sups = @($unowned | Where-Object { $_.CommandLine -like "*strategy.supervisor*" })
$rest = @($unowned | Where-Object { $_.CommandLine -notlike "*strategy.supervisor*" })
foreach ($p in ($sups + $rest)) {
    Stop-HunterTree -ProcessId $p.ProcessId -Label "(stray)"
    Start-Sleep -Milliseconds 300
}

Start-Sleep -Seconds 2
$left = @(Find-HunterStrays)
if ($left.Count -gt 0) {
    Write-ProfileError -Message "Some processes still running:" -Detail "$(($left.ProcessId) -join ', ')"
} else {
    Write-ProfileSuccess -Message "All Spread Hunter and stray processes stopped successfully."
}
