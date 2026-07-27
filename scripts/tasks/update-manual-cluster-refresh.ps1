param(
    [string]$TaskName = "EduNewsManualClusterRefresh",
    [string]$TaskPath = "\",
    [string]$RepoRoot = "$PSScriptRoot\..\..",
    [string]$PythonPath = "",
    [ValidateSet("zongbao", "wanbao")]
    [string]$ReportType = "zongbao"
)

$ErrorActionPreference = "Stop"

try {
    $resolvedRepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
    if (-not $PythonPath) {
        $PythonPath = Join-Path $resolvedRepoRoot ".venv\Scripts\python.exe"
    }
    $resolvedPythonPath = (Resolve-Path -LiteralPath $PythonPath).Path

    Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath | Out-Null

    $action = New-ScheduledTaskAction `
        -Execute $resolvedPythonPath `
        -Argument "-m src.cli.main refresh-manual-clusters --report-type $ReportType" `
        -WorkingDirectory $resolvedRepoRoot

    Set-ScheduledTask `
        -TaskName $TaskName `
        -TaskPath $TaskPath `
        -Action $action | Out-Null

    Write-Host "Updated scheduled task '$TaskPath$TaskName'."
    Write-Host "Execute          : $resolvedPythonPath"
    Write-Host "Arguments        : $($action.Arguments)"
    Write-Host "WorkingDirectory : $resolvedRepoRoot"
}
catch {
    Write-Error "Failed to update scheduled task '$TaskPath$TaskName': $_"
    exit 1
}
