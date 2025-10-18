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

# Prevent overlapping runs
$lockPath = Join-Path $LockDirectory "pipeline_hourly.lock"
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
$logFile = Join-Path $LogDirectory "pipeline_hourly_$timestamp.log"

# Restrict to crawl -> hash-primary -> score -> summarize
# Prefer module execution so project root stays on sys.path
$arguments = @(
    "-m",
    "scripts.run_pipeline_once",
    "--steps",
    "crawl",
    "hash-primary",
    "score",
    "summarize",
    "--trigger-source",
    "scheduler-hourly"
)
if ($ContinueOnError) {
    $arguments += "--continue-on-error"
}

$env:PYTHONUNBUFFERED = "1"
Push-Location $repoRoot
try {
    $prevErr = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Python @arguments 2>&1 | Tee-Object -FilePath $logFile
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $prevErr
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
