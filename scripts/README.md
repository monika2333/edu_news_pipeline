Scripts layout

- run_optimize_prompts.ps1
  - Runs `python -m src.cli.main optimize-prompts` from the repo root.
  - Writes timestamped logs to `logs/optimize_prompts_*.log`; `-DryRun` only
    previews the collected cases and precheck result, without calling any model
    or writing prompt versions / run records.
  - Register it to run every night at 00:00 (server local time). Run this from
    the repository root in an elevated PowerShell. The production venv is
    `.venv`; if a machine uses a different directory name, point `-Python` at
    that machine's actual `Scripts\python.exe`:

    ```powershell
    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PWD\scripts\run_optimize_prompts.ps1`" -Python `"$PWD\.venv\Scripts\python.exe`"" `
        -WorkingDirectory $PWD
    $trigger = New-ScheduledTaskTrigger -Daily -At 00:00
    Register-ScheduledTask `
        -TaskName "EduNewsPromptOptimizer" `
        -Action $action `
        -Trigger $trigger `
        -Description "Nightly minimal edits to the four scoring prompts"
    ```

    Verify with `Get-ScheduledTask -TaskName EduNewsPromptOptimizer` and
    `Start-ScheduledTask -TaskName EduNewsPromptOptimizer`. The command exits
    non-zero when any prompt failed, so the task history reflects partial
    failures without blocking the other prompts.

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

- db/refresh-schema.ps1
  - Dumps the live database into `database/schema.sql` and normalizes the result into the
    committed shape (strips pg_dump guards and the public schema block, pins
    `schema_migrations.version` to `varchar(128)`).
  - `-NoDump` only normalizes the existing file; `-Verify` runs the migration replay and
    normalizer tests afterwards. The rules live in `db/normalize_schema.py`.

Notes
- If you move the repo folder, re-register all tasks so the absolute paths are updated.
