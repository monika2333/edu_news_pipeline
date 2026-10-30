# -DryRun: preview cases and the precheck only; no model calls, no database writes.
# Keep this file ASCII-only. Windows PowerShell 5.1 decodes BOM-less scripts with
# the ANSI code page, so non-ASCII bytes here can break parsing before it runs.
param(
    [string]$Python = "python",
    [switch]$DryRun
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
$logFile = Join-Path $logDirectory "optimize_prompts_$timestamp.log"
$arguments = @("-m", "src.cli.main", "optimize-prompts")
if ($DryRun) {
    $arguments += "--dry-run"
}
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
    Write-Warning "Prompt optimizer exited with code $exitCode"
}

exit $exitCode
