<#
.SYNOPSIS
  Compress and purge old log files in a directory.

.DESCRIPTION
  - Compresses files older than -CompressOlderThanDays (default 3 days) using ZIP
  - Deletes files (including .zip/.gz) older than -DeleteOlderThanDays (default 14 days)
  - Skips files already compressed (.zip/.gz)
  - Provides a -DryRun switch to preview actions

  Intended to be run by Windows Task Scheduler daily.

.PARAMETER LogsPath
  The logs directory. Default: "logs" relative to repo root.

.PARAMETER CompressOlderThanDays
  Compress files older than this many days. Default: 3.

.PARAMETER DeleteOlderThanDays
  Delete files older than this many days. Default: 14.

.PARAMETER Patterns
  File patterns to consider as log files for compression/deletion. Default: '*.log','*.txt','*.jsonl'.

.PARAMETER DryRun
  If set, only prints what would be done.
#>

[CmdletBinding()]
param(
    [string]$LogsPath = "logs",
    [int]$CompressOlderThanDays = 3,
    [int]$DeleteOlderThanDays = 14,
    [string[]]$Patterns = @('*.log','*.txt','*.jsonl'),
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Info($msg) { Write-Host "[INFO ] $msg" -ForegroundColor Cyan }
function Write-Done($msg) { Write-Host "[DONE ] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Warning $msg }

try {
    $root = (Resolve-Path -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
    # If LogsPath is relative, resolve from repo root (script's parent directory is scripts/)
    if (-not ([System.IO.Path]::IsPathRooted($LogsPath))) {
        $repoRoot = Split-Path -Parent $root
        $LogsPath = Join-Path $repoRoot $LogsPath
    }

    if (-not (Test-Path -LiteralPath $LogsPath)) {
        Write-Info "Logs directory not found: $LogsPath (nothing to do)."
        return
    }

    Write-Info "Logs path: $LogsPath"
    Write-Info "Compress > $CompressOlderThanDays days; Delete > $DeleteOlderThanDays days; DryRun=$($DryRun.IsPresent)"

    $now = Get-Date
    $compressBefore = $now.AddDays(-1 * $CompressOlderThanDays)
    $deleteBefore   = $now.AddDays(-1 * $DeleteOlderThanDays)

    # Build include filter scriptblock for Get-ChildItem
    $includeFilter = { param($f, $patterns) foreach($p in $patterns){ if ($f.Name -like $p) { return $true } } return $false }

    # 1) Delete very old files (any extension commonly used for logs)
    $toDelete = Get-ChildItem -LiteralPath $LogsPath -Recurse -File |
        Where-Object { $_.LastWriteTime -lt $deleteBefore -and (& $includeFilter $_ $Patterns + @('*.zip','*.gz')) }

    $deleted = 0
    foreach ($f in $toDelete) {
        if ($DryRun) {
            Write-Host "[DEL  ] $($f.FullName)" -ForegroundColor Yellow
        } else {
            Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop
        }
        $deleted++
    }
    Write-Info "Delete candidates: $deleted"

    # 2) Compress older files that are not already compressed
    $compressCandidates = Get-ChildItem -LiteralPath $LogsPath -Recurse -File |
        Where-Object {
            $_.LastWriteTime -lt $compressBefore -and
            -not ($_.Extension -in @('.zip', '.gz')) -and
            (& $includeFilter $_ $Patterns)
        }

    $compressed = 0
    foreach ($f in $compressCandidates) {
        $zipPath = "$($f.FullName).zip"
        if (Test-Path -LiteralPath $zipPath) {
            Write-Warn "Zip already exists, skipping: $zipPath"
            continue
        }

        if ($DryRun) {
            Write-Host "[ZIP  ] $($f.FullName) -> $zipPath" -ForegroundColor Magenta
        } else {
            Compress-Archive -Path $f.FullName -DestinationPath $zipPath -CompressionLevel Optimal
            # Only remove original if compression succeeded
            if (Test-Path -LiteralPath $zipPath) {
                Remove-Item -LiteralPath $f.FullName -Force
            }
        }
        $compressed++
    }
    Write-Info "Compressed: $compressed"

    # 3) Optional: prune empty directories
    $empties = Get-ChildItem -LiteralPath $LogsPath -Recurse -Directory |
        Where-Object { @(Get-ChildItem -LiteralPath $_.FullName -Force).Count -eq 0 }
    foreach ($d in $empties) {
        if ($DryRun) {
            Write-Host "[RMDIR] $($d.FullName)" -ForegroundColor DarkYellow
        } else {
            Remove-Item -LiteralPath $d.FullName -Force -Recurse
        }
    }

    Write-Done "Log maintenance complete."
}
catch {
    Write-Error "Failed: $($_.Exception.Message)"
    exit 1
}

