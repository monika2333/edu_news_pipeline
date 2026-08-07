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

- clean_oversized_llm_sources.py
  - One-time inspection and cleanup for source values longer than the shared
    `MAX_LLM_SOURCE_LENGTH` correctness guard.
  - `python -m scripts.clean_oversized_llm_sources` is a dry-run and reports
    matches in `news_summaries`, `manual_reviews`, and `shift_reviews`.
  - `python -m scripts.clean_oversized_llm_sources --apply` sets only matching
    `news_summaries.llm_source` values to `NULL`; manual review fields are never
    changed by this script.

- tasks/register-clean-logs-task.ps1
  - Registers `EduNews_CleanLogs` scheduled task. Default time `02:00`.
  - Uses `tasks/run_clean_logs.ps1` to keep `/TR` short and capture logs.

- tasks/run_clean_logs.ps1
  - Wrapper invoked by the scheduled task; runs `clean-logs.ps1` and tees output to `logs/clean_logs_task_*.log`.

Notes
- Do not move or delete `run_export.ps1` unless you also update existing Scheduled Tasks, which point to its absolute path.
- If you move the repo folder, re-register all tasks so the absolute paths are updated.
