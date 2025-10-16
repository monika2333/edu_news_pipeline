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

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot  = Split-Path -Parent $scriptDir
$cleanScript = Join-Path $scriptDir 'clean-logs.ps1'
if (-not (Test-Path -LiteralPath $cleanScript)) { throw "Script not found: $cleanScript" }

if (-not ([System.IO.Path]::IsPathRooted($LogsPath))) {
    $LogsPath = Join-Path $repoRoot $LogsPath
}

$ps = (Get-Command powershell.exe).Source
$action = "`"$ps`" -NoProfile -ExecutionPolicy Bypass -File `"$cleanScript`" -LogsPath `"$LogsPath`" -CompressOlderThanDays $CompressOlderThanDays -DeleteOlderThanDays $DeleteOlderThanDays"

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

