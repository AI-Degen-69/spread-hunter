[CmdletBinding()]
param(
    [ValidateRange(1, 336)]
    [int]$DurationHours = 101,
    [switch]$StartNow,
    [switch]$ReplaceExisting
)

$ErrorActionPreference = "Stop"
$ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = (Get-Command python -ErrorAction Stop).Source
$TaskName = "SpreadHunter-FlightSupervisor"
$UserId = "$env:USERDOMAIN\$env:USERNAME"
$Script = Join-Path $ProjectPath "scripts\flight_supervisor.py"

if (-not (Test-Path $Script)) {
    throw "Flight supervisor script was not found: $Script"
}

$Arguments = "`"$Script`" --duration-hours $DurationHours"
$Action = New-ScheduledTaskAction -Execute $Python -Argument $Arguments -WorkingDirectory $ProjectPath
$Principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Hours ($DurationHours + 1)) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $UserId
$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($ExistingTask -and -not $ReplaceExisting) {
    throw "Task '$TaskName' already exists. Inspect it first; pass -ReplaceExisting only if you intend to replace it."
}

$RegisterArgs = @{
    TaskName = $TaskName
    Action = $Action
    Trigger = $Trigger
    Principal = $Principal
    Settings = $Settings
    Description = "Read-only shadow flight monitor; never starts the live Trader."
}
if ($ReplaceExisting) { $RegisterArgs.Force = $true }
Register-ScheduledTask @RegisterArgs | Out-Null

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started $TaskName for up to $DurationHours hours."
} else {
    Write-Host "Registered $TaskName; it will start at the next sign-in for $UserId."
}
Write-Host "Task Scheduler is per-user and requires this Windows account to remain signed in."
Write-Host "The supervisor will prevent system sleep while it is running."
Write-Host "Check status with: Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host "Stop with: Stop-ScheduledTask -TaskName '$TaskName'"
