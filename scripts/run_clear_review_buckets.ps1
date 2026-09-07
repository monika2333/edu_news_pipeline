param(
    [string]$Python = "python"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $repoRoot) {
    throw "Unable to resolve repository root from script location."
}

$logDirectory = Join-Path $repoRoot "logs"
if (-not (Test-Path -LiteralPath $logDirectory)) {
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $logDirectory "clear_review_buckets_$timestamp.log"
$arguments = @("-m", "src.cli.main", "clear-review-buckets")
$exitCode = 1
$previousErrorActionPreference = $ErrorActionPreference

$env:PYTHONUNBUFFERED = "1"
Push-Location $repoRoot
try {
    $ErrorActionPreference = "Continue"
    & $Python @arguments 2>&1 | Tee-Object -FilePath $logFile -Append
    if ($null -ne $LASTEXITCODE) {
        $exitCode = $LASTEXITCODE
    }
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
    Pop-Location
}

if ($exitCode -ne 0) {
    Write-Warning "Clear review buckets exited with code $exitCode"
}

exit $exitCode
