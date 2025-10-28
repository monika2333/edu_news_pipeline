Scripts layout

- run_export.ps1
  - Runs `python -m src.cli.main export` from the repo root.
  - Writes logs to `logs/` with a timestamped filename.

- tasks/schedule_export_tasks.ps1
  - Registers two Windows Scheduled Tasks at 15:00 and 20:00 daily.
  - Params:
    - `-StartDate <yyyy-MM-dd>`: first-run date (defaults to today).
    - `-Remove`: unregister the tasks.

- clean-logs.ps1
  - Compresses logs older than 3 days and deletes items older than 14 days.
  - Supports `-DryRun` to preview actions.

- tasks/register-clean-logs-task.ps1
  - Registers `EduNews_CleanLogs` scheduled task. Default time `02:00`.
  - Uses `tasks/run_clean_logs.ps1` to keep `/TR` short and capture logs.

- tasks/run_clean_logs.ps1
  - Wrapper invoked by the scheduled task; runs `clean-logs.ps1` and tees output to `logs/clean_logs_task_*.log`.

Notes
- Do not move or delete `run_export.ps1` unless you also update existing Scheduled Tasks, which point to its absolute path.
- If you move the repo folder, re-register all tasks so the absolute paths are updated.
