<#
.SYNOPSIS
  Register a Windows Task Scheduler job to run the Beijing Daily crawl.

.DESCRIPTION
  Creates or updates a scheduled task that executes scripts/run_bjrb_daily.ps1
  daily at the requested times. The task explicitly runs PowerShell so Windows
  file associations cannot open the .ps1 script in an editor.

.PARAMETER Times
  Daily start times in HH:mm (24h). Default: 06:00 and 08:00.

.PARAMETER TaskName
  Name of the scheduled task. Default: EduNews_bjrb_daily.

.PARAMETER Python
  Python executable passed to run_bjrb_daily.ps1. Default: python.

.PARAMETER ContinueOnError
  Propagate --continue-on-error to the runner script.
#>

[CmdletBinding()]
param(
    [string[]]$Times = @('06:00', '08:00'),
    [string]$TaskName = 'EduNews_bjrb_daily',
    [string]$Python = 'python',
    [switch]$ContinueOnError
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$tasksDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptsDir = Split-Path -Parent $tasksDir
$runner = Join-Path $scriptsDir 'run_bjrb_daily.ps1'
if (-not (Test-Path -LiteralPath $runner)) {
    throw "Runner script not found: $runner"
}

$ps = (Get-Command powershell.exe).Source
$actionArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$runner`""
if ($Python -and $Python -ne 'python') {
    $actionArgs += " -Python `"$Python`""
}
if ($ContinueOnError) {
    $actionArgs += " -ContinueOnError"
}

$triggers = foreach ($time in $Times) {
    if ($time -notmatch '^\d{2}:\d{2}$') {
        throw "Invalid time '$time'. Use HH:mm."
    }
    $hour, $minute = $time.Split(':')
    New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours([int]$hour).AddMinutes([int]$minute))
}
if (-not $triggers) {
    throw 'At least one time is required.'
}

$action = New-ScheduledTaskAction -Execute $ps -Argument $actionArgs
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Write-Host "Registering scheduled task '$TaskName' to run daily at $($Times -join ', ')" -ForegroundColor Cyan
Write-Host "Action: `"$ps`" $actionArgs"

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Force | Out-Null

Write-Host "Done. Use 'schtasks /Query /TN $TaskName /V /FO LIST' to verify." -ForegroundColor Green
