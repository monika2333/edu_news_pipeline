Windows 定时任务（任务计划程序）

目标：每天下午 15:00 和晚上 20:00 自动执行：

`python -m src.cli.main export`

仓库已提供：
- `scripts\run_export.ps1`：在仓库根目录执行导出并记录日志到 `logs/`。
- `scripts\schedule_export_tasks.ps1`：注册/移除两个计划任务（15:00 和 20:00）。

用法：
- 注册计划任务（创建或更新）：
  - PowerShell：`powershell -ExecutionPolicy Bypass -File scripts\schedule_export_tasks.ps1`
- 移除计划任务：
  - PowerShell：`powershell -ExecutionPolicy Bypass -File scripts\schedule_export_tasks.ps1 -Remove`
- 手动测试一次运行（便于验证环境）：
  - PowerShell：`powershell -ExecutionPolicy Bypass -File scripts\run_export.ps1`

注意事项：
- 确保 `python` 在 PATH 中（用 `python --version` 验证）。
- 日志保存在仓库 `logs/` 目录，文件名包含时间戳。
- 如果需要通过系统 UI 手动创建任务，可按以下配置：
  - 程序/脚本：`powershell.exe`
  - 添加参数：`-NoProfile -ExecutionPolicy Bypass -File "C:\Monica_program\edu_news_pipeline\scripts\run_export.ps1"`
  - 起始于（可选）：`C:\Monica_program\edu_news_pipeline`
  - 触发器：每日 15:00 与 20:00

