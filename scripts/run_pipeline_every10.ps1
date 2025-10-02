param(
    [string]$Python = "python",
    [switch]$ContinueOnError,
    [string]$LogDirectory,
    [string]$LockDirectory
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $repoRoot) {
    throw "Unable to resolve repository root from script location."
}

if (-not $LockDirectory) {
    $LockDirectory = Join-Path $repoRoot "locks"
}
if (-not (Test-Path $LockDirectory)) {
    New-Item -ItemType Directory -Path $LockDirectory -Force | Out-Null
}

$lockPath = Join-Path $LockDirectory "pipeline_every10.lock"
$lockFile = $null
try {
    $lockFile = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch {
    Write-Output "Run already in progress; skipping."
    exit 0
}

if (-not $LogDirectory) {
    $LogDirectory = Join-Path $repoRoot "logs"
}
if (-not (Test-Path $LogDirectory)) {
    New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $LogDirectory "pipeline_every10_$timestamp.log"

$arguments = @("scripts/run_pipeline_once.py", "--steps", "crawl", "summarize", "score", "--trigger-source", "scheduler-10min")
if ($ContinueOnError) {
    $arguments += "--continue-on-error"
}

$env:PYTHONUNBUFFERED = "1"
Push-Location $repoRoot
try {
    & $Python @arguments *>&1 | Tee-Object -FilePath $logFile
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
    if ($lockFile) {
        $lockFile.Dispose()
        Remove-Item -LiteralPath $lockPath -ErrorAction SilentlyContinue
    }
}

if ($exitCode -ne 0) {
    Write-Warning "Pipeline exited with code $exitCode"
}

exit $exitCode
