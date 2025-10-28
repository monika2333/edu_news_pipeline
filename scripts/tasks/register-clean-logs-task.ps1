<#
.SYNOPSIS
  Register a Windows Task Scheduler job to run log cleanup daily.

.DESCRIPTION
  Creates or updates a scheduled task that runs scripts/clean-logs.ps1 daily at -Time.

.PARAMETER Time
  Daily start time in HH:mm (24h). Default: 02:00

.PARAMETER TaskName
  Name of the scheduled task. Default: EduNews_CleanLogs

.PARAMETER LogsPath
  Logs folder path passed to clean-logs.ps1. Default: logs

.PARAMETER CompressOlderThanDays
  Default: 3

.PARAMETER DeleteOlderThanDays
  Default: 14
#>

[CmdletBinding()]
param(
  [string]$Time = '02:00',
  [string]$TaskName = 'EduNews_CleanLogs',
  [string]$LogsPath = 'logs',
  [int]$CompressOlderThanDays = 3,
  [int]$DeleteOlderThanDays = 14
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$tasksDir  = Split-Path -Parent $MyInvocation.MyCommand.Path  # scripts\tasks
$scriptsDir = Split-Path -Parent $tasksDir                     # scripts
$repoRoot  = Split-Path -Parent $scriptsDir
$cleanScript = Join-Path $scriptsDir 'clean-logs.ps1'
if (-not (Test-Path -LiteralPath $cleanScript)) { throw "Script not found: $cleanScript" }

if (-not ([System.IO.Path]::IsPathRooted($LogsPath))) {
    $LogsPath = Join-Path $repoRoot $LogsPath
}

$ps = (Get-Command powershell.exe).Source
# Use a wrapper to simplify /TR and capture logs reliably
$wrapper = Join-Path $tasksDir 'run_clean_logs.ps1'
if (-not (Test-Path -LiteralPath $wrapper)) { throw "Wrapper not found: $wrapper" }
# Keep /TR short to avoid 261-char limit; rely on wrapper defaults
$action = "`"$ps`" -NoProfile -ExecutionPolicy Bypass -File `"$wrapper`""

Write-Host "Registering scheduled task '$TaskName' to run daily at $Time" -ForegroundColor Cyan
Write-Host "Action: $action"

# Use schtasks.exe for broad compatibility
$args = @(
  '/Create','/F',
  '/SC','DAILY',
  '/ST', $Time,
  '/TN', $TaskName,
  '/TR', $action
)

& schtasks.exe @args | Write-Host
Write-Host "Done. Use 'schtasks /Query /TN $TaskName /V /FO LIST' to verify." -ForegroundColor Green
