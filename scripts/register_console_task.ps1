param(
    [string]$TaskName = "EduNews_Console",
    [string]$RepoRoot = "$PSScriptRoot\..",
    [string]$PythonPath = "",
    [string]$LogPath = ""
)

$scriptPath = Join-Path $PSScriptRoot "run_console_service.ps1"
if (-not (Test-Path $scriptPath)) {
    Write-Error "Helper script not found at $scriptPath"
    exit 1
}

$actionArgs = "-File `"$scriptPath`" -RepoRoot `"$RepoRoot`""
if ($PythonPath) {
    $actionArgs += " -PythonPath `"$PythonPath`""
}
if ($LogPath) {
    $actionArgs += " -LogPath `"$LogPath`""
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $actionArgs
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force | Out-Null
    Write-Host "Scheduled task '$TaskName' registered."
    Write-Host "It will start at boot and restart automatically on failure."
catch {
    Write-Error "Failed to register task: $_"
    exit 1
}
