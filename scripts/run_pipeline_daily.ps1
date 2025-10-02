param(
    [string]$Python = "python",
    [string[]]$Steps,
    [string[]]$Skip,
    [switch]$ContinueOnError,
    [string]$LogDirectory
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $repoRoot) {
    throw "Unable to resolve repository root from script location."
}

if (-not $LogDirectory) {
    $LogDirectory = Join-Path $repoRoot "logs"
}

if (-not (Test-Path $LogDirectory)) {
    New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $LogDirectory "pipeline_$timestamp.log"

$arguments = @("scripts/run_pipeline_once.py", "--trigger-source", "scheduled-task")

if ($Steps -and $Steps.Length -gt 0) {
    $arguments += "--steps"
    $arguments += $Steps
}

if ($Skip -and $Skip.Length -gt 0) {
    $arguments += "--skip"
    $arguments += $Skip
}

if ($ContinueOnError) {
    $arguments += "--continue-on-error"
}

$env:PYTHONUNBUFFERED = "1"

Push-Location $repoRoot
try {
    & $Python @arguments *>&1 | Tee-Object -FilePath $logFile
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($exitCode -ne 0) {
    Write-Warning "Pipeline exited with code $exitCode"
}

exit $exitCode
