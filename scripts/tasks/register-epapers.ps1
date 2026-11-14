<#
.SYNOPSIS
  Register the single Windows Task Scheduler job that crawls every enabled epaper.

.DESCRIPTION
  Creates or updates a scheduled task that executes scripts/run_epapers.ps1 at
  the requested times (default 08:00, 12:00, 16:00, 20:00 and 00:00). The task
  explicitly runs PowerShell so Windows file associations cannot open the .ps1
  script in an editor.

  The runner only crawls the epapers enabled in the console settings page
  (app_settings.epaper_sources, default Beijing Daily / Laodong Wubao / China
  Teacher). It never runs scoring, summarization or any other pipeline step:
  those go through the hourly main pipeline, whose lock file guarantees a
  single processing process. Epapers are crawled five times a day, so the
  00:00 round usually runs before the same-day issue is online; the crawler
  reports that as "not published yet" instead of a failure.

  Adding another epaper only needs an adapter, one SOURCE_CATALOG entry and the
  switch in the settings page - no new script and no new scheduled task.

.PARAMETER Times
  Daily start times in HH:mm (24h). Default: 08:00, 12:00, 16:00, 20:00, 00:00.

.PARAMETER TaskName
  Name of the scheduled task. Default: EduNews_Epapers.

.PARAMETER Python
  Python executable passed to run_epapers.ps1. Default: python.

.PARAMETER ContinueOnError
  Propagate --continue-on-error to the runner script.
#>

[CmdletBinding()]
param(
    [string[]]$Times = @('08:00', '12:00', '16:00', '20:00', '00:00'),
    [string]$TaskName = 'EduNews_Epapers',
    [string]$Python = 'python',
    [switch]$ContinueOnError
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$tasksDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptsDir = Split-Path -Parent $tasksDir
$runner = Join-Path $scriptsDir 'run_epapers.ps1'
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
