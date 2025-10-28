param(
  [string]$RepoPath
)

# Resolve repo path: default to the script's parent directory
if (-not $RepoPath -or $RepoPath -eq "") {
  $RepoPath = Split-Path -Parent -Path $PSScriptRoot
}

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location $RepoPath

# Prepare logs
$logDir = Join-Path $RepoPath 'logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$timestamp = (Get-Date).ToString('yyyy-MM-dd_HH-mm-ss')
$logFile = Join-Path $logDir "export_$timestamp.log"

# Make Python output unbuffered for real-time logs
$env:PYTHONUNBUFFERED = '1'

Write-Host "[run_export] Repo: $RepoPath"
Write-Host "[run_export] Log:  $logFile"

try {
  # Run the export and tee output to log
  & python -m src.cli.main export *>&1 | Tee-Object -FilePath $logFile
  $LASTEXITCODE = 0
}
catch {
  "ERROR: $($_.Exception.Message)" | Tee-Object -FilePath $logFile -Append | Out-Null
  exit 1
}

exit 0

