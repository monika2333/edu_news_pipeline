param(
    [string]$RepoRoot = "$PSScriptRoot\..",
    [string]$PythonPath = "",
    [string]$LogPath = ""
)

if (-not (Test-Path $RepoRoot)) {
    Write-Error "Repo root '$RepoRoot' not found."
    exit 1
}

if (-not $PythonPath) {
    $PythonPath = Join-Path $RepoRoot ".venv\Scripts\python.exe"
}

if (-not (Test-Path $PythonPath)) {
    Write-Error "Python interpreter '$PythonPath' not found. Pass -PythonPath or create the virtualenv."
    exit 1
}

if (-not $LogPath) {
    $LogPath = Join-Path $RepoRoot "logs\console_service.log"
}

$logDir = Split-Path $LogPath -Parent
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

$env:PYTHONUNBUFFERED = "1"

Write-Host "Starting Edu News console service loop..."
Write-Host "Repo       : $RepoRoot"
Write-Host "Python     : $PythonPath"
Write-Host "Log        : $LogPath"

while ($true) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$timestamp] Starting console..." | Tee-Object -FilePath $LogPath -Append | Out-Null

    $process = Start-Process -FilePath $PythonPath `
        -ArgumentList "run_console.py" `
        -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput $LogPath `
        -RedirectStandardError $LogPath `
        -NoNewWindow `
        -PassThru

    Wait-Process -InputObject $process

    $exitCode = $process.ExitCode
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$timestamp] Console exited with code $exitCode. Restarting in 5 seconds." | Tee-Object -FilePath $LogPath -Append | Out-Null
    Start-Sleep -Seconds 5
}
