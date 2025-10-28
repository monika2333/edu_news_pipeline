param(
  [string]$LogsPath = 'logs',
  [int]$CompressOlderThanDays = 3,
  [int]$DeleteOlderThanDays = 14
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptsDir = Split-Path -Parent $PSScriptRoot
$repoRoot = Split-Path -Parent $scriptsDir
$cleanScript = Join-Path $scriptsDir 'clean-logs.ps1'
if (-not (Test-Path -LiteralPath $cleanScript)) {
  throw "clean-logs.ps1 not found: $cleanScript"
}

# Resolve logs dir absolute
if (-not ([System.IO.Path]::IsPathRooted($LogsPath))) {
  $LogsPath = Join-Path $repoRoot $LogsPath
}

# Prepare a task log under logs/
$logDir = Join-Path $repoRoot 'logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$ts = (Get-Date).ToString('yyyy-MM-dd_HH-mm-ss')
$taskLog = Join-Path $logDir "clean_logs_task_$ts.log"

Write-Host "[run_clean_logs] LogsPath=$LogsPath Compress>$CompressOlderThanDays Delete>$DeleteOlderThanDays"
Write-Host "[run_clean_logs] Task log: $taskLog"

try {
  $ps = (Get-Command powershell.exe).Source
  & $ps -NoProfile -ExecutionPolicy Bypass -File $cleanScript `
    -LogsPath $LogsPath `
    -CompressOlderThanDays $CompressOlderThanDays `
    -DeleteOlderThanDays $DeleteOlderThanDays `
    *>&1 | Tee-Object -FilePath $taskLog
  exit 0
}
catch {
  "ERROR: $($_.Exception.Message)" | Tee-Object -FilePath $taskLog -Append | Out-Null
  exit 1
}
