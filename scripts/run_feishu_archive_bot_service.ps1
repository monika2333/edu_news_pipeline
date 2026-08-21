param(
    [string]$RepoRoot = "$PSScriptRoot\..",
    [string]$PythonPath = "",
    [string]$LogPath = ""
)

if (-not (Test-Path -LiteralPath $RepoRoot)) {
    Write-Error "Repo root '$RepoRoot' not found."
    exit 1
}

$resolvedRepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
if (-not $PythonPath) {
    $PythonPath = Join-Path $resolvedRepoRoot "venv\Scripts\python.exe"
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    Write-Error "Python interpreter '$PythonPath' not found. Pass -PythonPath or create the virtualenv."
    exit 1
}

if (-not $LogPath) {
    $LogPath = Join-Path $resolvedRepoRoot "logs\feishu_archive_bot.log"
}

$logDir = Split-Path $LogPath -Parent
if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

$env:PYTHONUNBUFFERED = "1"

Write-Host "Starting Feishu archive bot service loop..."
Write-Host "Repo       : $resolvedRepoRoot"
Write-Host "Python     : $PythonPath"
Write-Host "Log        : $LogPath"

while ($true) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$timestamp] Starting Feishu archive bot..." | Tee-Object -FilePath $LogPath -Append | Out-Null

    Push-Location $resolvedRepoRoot
    try {
        & $PythonPath -m src.cli.main feishu-archive-bot *>> $LogPath
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$timestamp] Bot exited with code $exitCode. Restarting in 5 seconds." | Tee-Object -FilePath $LogPath -Append | Out-Null
    Start-Sleep -Seconds 5
}
