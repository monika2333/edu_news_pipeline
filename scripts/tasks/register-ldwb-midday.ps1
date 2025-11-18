<#
.SYNOPSIS
  Register a Windows Task Scheduler job to run the Laodong Wubao crawl every evening.

.DESCRIPTION
  Creates or updates a scheduled task that executes scripts/run_ldwb_daily.ps1 daily at the requested time.

.PARAMETER Time
  Daily start time in HH:mm (24h). Default: 18:50.

.PARAMETER TaskName
  Name of the scheduled task. Default: EduNews_LDWB_Daily.

.PARAMETER Python
  Python executable passed to run_ldwb_daily.ps1. Default: python.

.PARAMETER ContinueOnError
  Propagate --continue-on-error to the runner script.
#>

[CmdletBinding()]
param(
  [string]$Time = '18:50',
  [string]$TaskName = 'EduNews_LDWB_Daily',
  [string]$Python = 'python',
  [switch]$ContinueOnError
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$tasksDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptsDir = Split-Path -Parent $tasksDir
$repoRoot  = Split-Path -Parent $scriptsDir
$runner = Join-Path $scriptsDir 'run_ldwb_daily.ps1'
if (-not (Test-Path -LiteralPath $runner)) {
    throw "Runner script not found: $runner"
}

$ps = (Get-Command powershell.exe).Source
$action = "`"$ps`" -NoProfile -ExecutionPolicy Bypass -File `"$runner`""
if ($Python -and $Python -ne 'python') {
    $action += " -Python `"$Python`""
}
if ($ContinueOnError) {
    $action += " -ContinueOnError"
}

Write-Host "Registering scheduled task '$TaskName' to run daily at $Time" -ForegroundColor Cyan
Write-Host "Action: $action"

$args = @(
  '/Create','/F',
  '/SC','DAILY',
  '/ST', $Time,
  '/TN', $TaskName,
  '/TR', $action
)

& schtasks.exe @args | Write-Host
Write-Host "Done. Use 'schtasks /Query /TN $TaskName /V /FO LIST' to verify." -ForegroundColor Green
