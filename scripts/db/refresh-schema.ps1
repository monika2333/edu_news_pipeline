<#
.SYNOPSIS
    Refresh database/schema.sql and normalize it into the committed shape.

.DESCRIPTION
    `dbmate up` rewrites database/schema.sql whenever it applies a migration, but pg_dump 18
    emits three things that must not be committed as-is:

      1. `\restrict` / `\unrestrict` lines carrying a token that is randomized on every dump;
      2. the `public` schema comment block (`-- *not* creating schema, since initdb creates it`);
      3. `schema_migrations.version` in whatever shape the local database happens to have
         (`character varying` without a length). The repository contract is varchar(128) and
         tests/test_migration_replay.py builds its mirror table with varchar(128), so committing
         the raw dump turns that test red.

    This script dumps into a temporary file, normalizes and validates it, and only then replaces
    the target - a failed normalization leaves database/schema.sql untouched. The rules live in
    scripts/db/normalize_schema.py and are covered by tests/test_schema_normalizer.py.

.PARAMETER Verify
    Run tests/test_migration_replay.py and tests/test_schema_normalizer.py after refreshing.

.PARAMETER NoDump
    Normalize the existing file without running `dbmate dump`.

.EXAMPLE
    .\scripts\db\refresh-schema.ps1

.EXAMPLE
    .\scripts\db\refresh-schema.ps1 -Verify
#>
param(
    [string]$RepoRoot = "$PSScriptRoot\..\..",
    [string]$SchemaFile = "database\schema.sql",
    [string]$MigrationsDir = "database\migrations",
    [string]$Python = "",
    [switch]$NoDump,
    [switch]$Verify
)

$ErrorActionPreference = "Stop"

function Resolve-ScriptPath {
    param([string]$Root, [string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $Root $Path
}

$resolvedRepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$schemaPath = Resolve-ScriptPath -Root $resolvedRepoRoot -Path $SchemaFile
$migrationsPath = Resolve-ScriptPath -Root $resolvedRepoRoot -Path $MigrationsDir
$tmpPath = "$schemaPath.refresh.tmp"

if (-not $Python) {
    foreach ($candidate in @("venv\Scripts\python.exe", ".venv\Scripts\python.exe")) {
        $candidatePath = Join-Path $resolvedRepoRoot $candidate
        if (Test-Path -LiteralPath $candidatePath) {
            $Python = $candidatePath
            break
        }
    }
}
if (-not $Python) {
    $Python = "python"
}
$pythonCommand = Get-Command -Name $Python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    throw "Python not found: '$Python' (pass -Python <path>)"
}

# Chinese diagnostics from the normalizer must not trip over the console code page.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if (Test-Path -LiteralPath $tmpPath) {
    Remove-Item -LiteralPath $tmpPath -Force
}

if (-not $NoDump) {
    $dbmateCommand = Get-Command -Name "dbmate" -ErrorAction SilentlyContinue
    if (-not $dbmateCommand) {
        throw "dbmate not found in PATH"
    }

    if (-not $env:DATABASE_URL) {
        $envFile = Join-Path $resolvedRepoRoot ".env.local"
        if (Test-Path -LiteralPath $envFile) {
            $urlLine = Get-Content -LiteralPath $envFile -Encoding UTF8 |
                Where-Object { $_ -match '^\s*DATABASE_URL\s*=' } |
                Select-Object -First 1
            if ($urlLine) {
                $env:DATABASE_URL = ($urlLine -replace '^\s*DATABASE_URL\s*=\s*', '').Trim().Trim('"').Trim("'")
            }
        }
    }
    if (-not $env:DATABASE_URL) {
        throw "DATABASE_URL is not set (environment or .env.local)"
    }

    # Dumping while migrations are still pending would snapshot the old schema.
    $statusText = (& $dbmateCommand.Source --migrations-dir $migrationsPath status) 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw "dbmate status failed:`n$statusText"
    }
    if ($statusText -match 'Pending:\s*(\d+)') {
        if ([int]$Matches[1] -gt 0) {
            throw "There are $($Matches[1]) pending migration(s); run 'dbmate up' first, then rerun this script"
        }
    }
    else {
        Write-Warning "Could not read the pending migration count from 'dbmate status'"
    }

    Write-Host "[refresh] dumping schema to $tmpPath"
    & $dbmateCommand.Source --migrations-dir $migrationsPath --schema-file $tmpPath dump
    if ($LASTEXITCODE -ne 0) {
        if (Test-Path -LiteralPath $tmpPath) {
            Remove-Item -LiteralPath $tmpPath -Force
        }
        throw "dbmate dump failed with exit code $LASTEXITCODE"
    }
}

try {
    $normalizeTarget = if ($NoDump) { $schemaPath } else { $tmpPath }
    Push-Location $resolvedRepoRoot
    try {
        & $pythonCommand.Source -m scripts.db.normalize_schema $normalizeTarget
        if ($LASTEXITCODE -ne 0) {
            throw "schema normalization failed; $SchemaFile was not modified"
        }
    }
    finally {
        Pop-Location
    }

    if (-not $NoDump) {
        Move-Item -LiteralPath $tmpPath -Destination $schemaPath -Force
    }
}
finally {
    if (Test-Path -LiteralPath $tmpPath) {
        Remove-Item -LiteralPath $tmpPath -Force
    }
}

Push-Location $resolvedRepoRoot
try {
    $diffStat = (git --no-pager diff --stat -- $schemaPath) 2>&1 | Out-String
}
finally {
    Pop-Location
}

if ($diffStat.Trim()) {
    Write-Host "[refresh] $($diffStat.Trim())"
    Write-Host "[refresh] review: only INSERT INTO public.schema_migrations version rows should differ"
}
else {
    Write-Host "[refresh] $SchemaFile unchanged"
}

if ($Verify) {
    Push-Location $resolvedRepoRoot
    try {
        & $pythonCommand.Source -m pytest tests/test_migration_replay.py tests/test_schema_normalizer.py -q
        if ($LASTEXITCODE -ne 0) {
            throw "verification tests failed"
        }
    }
    finally {
        Pop-Location
    }
}
