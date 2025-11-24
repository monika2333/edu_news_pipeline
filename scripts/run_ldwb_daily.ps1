param(
    [string]$Python = "python",
    [switch]$ContinueOnError,
    [string]$LogDirectory,
    [string]$LockDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $repoRoot) {
    throw "Unable to resolve repository root from script location."
}

if (-not $LockDirectory) {
    $LockDirectory = Join-Path $repoRoot "locks"
}
if (-not (Test-Path -LiteralPath $LockDirectory)) {
    New-Item -ItemType Directory -Path $LockDirectory -Force | Out-Null
}

$lockPath = Join-Path $LockDirectory "pipeline_ldwb.lock"
$lockFile = $null
try {
    $lockFile = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch {
    Write-Output "Laodong Wubao run already in progress; skipping."
    exit 0
}

if (-not $LogDirectory) {
    $LogDirectory = Join-Path $repoRoot "logs"
}
if (-not (Test-Path -LiteralPath $LogDirectory)) {
    New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $LogDirectory "pipeline_ldwb_$timestamp.log"

$arguments = @(
    "-m",
    "scripts.run_pipeline_once",
    "--steps",
    "crawl",
    "hash-primary",
    "score",
    "summarize",
    "external-filter",
    "--trigger-source",
    "scheduler-ldwb"
)
if ($ContinueOnError) {
    $arguments += "--continue-on-error"
}

$env:PYTHONUNBUFFERED = "1"
$previousSources = $env:CRAWL_SOURCES
$env:CRAWL_SOURCES = "laodongwubao"

Push-Location $repoRoot
try {
    $prevErr = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Python @arguments 2>&1 | Tee-Object -FilePath $logFile
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $prevErr
} finally {
    Pop-Location
    if ($null -ne $previousSources) {
        $env:CRAWL_SOURCES = $previousSources
    } else {
        Remove-Item Env:CRAWL_SOURCES -ErrorAction SilentlyContinue
    }
    if ($lockFile) {
        $lockFile.Dispose()
        Remove-Item -LiteralPath $lockPath -ErrorAction SilentlyContinue
    }
}

if ($exitCode -ne 0) {
    Write-Warning "Laodong Wubao pipeline exited with code $exitCode"
}

exit $exitCode
