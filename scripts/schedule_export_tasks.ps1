param(
  [switch]$Remove
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$taskNames = @('EduNews_Export_15', 'EduNews_Export_20')
$scriptPath = Join-Path $PSScriptRoot 'run_export.ps1'

if ($Remove) {
  foreach ($name in $taskNames) {
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($existing) {
      Write-Host "Removing scheduled task: $name"
      Unregister-ScheduledTask -TaskName $name -Confirm:$false
    } else {
      Write-Host "Task not found (skip): $name"
    }
  }
  Write-Host "Done."
  exit 0
}

if (-not (Test-Path $scriptPath)) {
  throw "Runner script not found: $scriptPath"
}

$action = New-ScheduledTaskAction `
  -Execute 'powershell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"" `
  -WorkingDirectory (Split-Path -Parent $PSScriptRoot)

$trigger15 = New-ScheduledTaskTrigger -Daily -At 15:00
$trigger20 = New-ScheduledTaskTrigger -Daily -At 20:00

# Register or update tasks
Register-ScheduledTask -TaskName $taskNames[0] -Action $action -Trigger $trigger15 -Description 'Daily export at 15:00' -Force
Register-ScheduledTask -TaskName $taskNames[1] -Action $action -Trigger $trigger20 -Description 'Daily export at 20:00' -Force

Write-Host "Scheduled tasks created/updated: $($taskNames -join ', ')"
Write-Host "They will run: daily at 15:00 and 20:00"
Write-Host "Action: powershell -File `"$scriptPath`""

exit 0

