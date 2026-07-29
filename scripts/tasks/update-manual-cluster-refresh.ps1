param(
    [string]$TaskName = "EduNewsManualClusterRefresh",
    [string]$TaskPath = "\",
    [string]$RepoRoot = "$PSScriptRoot\..\..",
    [string]$PythonPath = "",
    [ValidateSet("zongbao", "wanbao")]
    [string]$ReportType = "zongbao",
    [ValidateRange(1, 1440)]
    [int]$IntervalMinutes = 10,
    [ValidateRange(1, 1440)]
    [int]$ExecutionTimeLimitMinutes = 15
)

$ErrorActionPreference = "Stop"

try {
    $resolvedRepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
    if (-not $PythonPath) {
        $PythonPath = Join-Path $resolvedRepoRoot ".venv\Scripts\python.exe"
    }
    $resolvedPythonPath = (Resolve-Path -LiteralPath $PythonPath).Path

    $task = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath

    $action = New-ScheduledTaskAction `
        -Execute $resolvedPythonPath `
        -Argument "-m src.cli.main refresh-manual-clusters --report-type $ReportType" `
        -WorkingDirectory $resolvedRepoRoot

    $task.Triggers[0].Repetition.Interval = "PT$($IntervalMinutes)M"
    $task.Settings.ExecutionTimeLimit = "PT$($ExecutionTimeLimitMinutes)M"
    $task.Settings.MultipleInstances = "IgnoreNew"

    Set-ScheduledTask `
        -TaskName $TaskName `
        -TaskPath $TaskPath `
        -Action $action `
        -Trigger $task.Triggers `
        -Settings $task.Settings | Out-Null

    Write-Host "Updated scheduled task '$TaskPath$TaskName'."
    Write-Host "Execute          : $resolvedPythonPath"
    Write-Host "Arguments        : $($action.Arguments)"
    Write-Host "WorkingDirectory : $resolvedRepoRoot"
    Write-Host "Interval         : $IntervalMinutes minutes"
    Write-Host "Execution limit  : $ExecutionTimeLimitMinutes minutes"
    Write-Host "Multiple instances: IgnoreNew"
}
catch {
    Write-Error "Failed to update scheduled task '$TaskPath$TaskName': $_"
    exit 1
}
