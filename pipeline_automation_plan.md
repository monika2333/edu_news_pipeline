# Pipeline Automation Plan

## Goals
- 实现教育新闻流水线的每日自动运行。
- 保留远程控制台，以便需要时从手机或电脑手动触发。
- 把最新的导出结果推送到手机，便于随时阅读和处理。

## 计划步骤

### 1. 自动触发每日运行
- 在服务器上为 `python scripts/run_pipeline_once.py` 配置计划任务（Windows 任务计划或 Linux cron）。
- 使用默认步骤 `crawl -> summarize -> score -> export`，确保 Supabase 等依赖配置正确。
- 运行后检查 `output/` 目录和控制台 API，确认日志、Supabase 元数据正常写入。
- Windows 示例脚本：`scripts/run_pipeline_daily.ps1` 会自动写日志，可在计划任务里调用：
  ```powershell
  powershell.exe -File "D:\600program\edu_news_pipeline\scripts\run_pipeline_daily.ps1" -Python "C:\Users\me\AppData\Local\Programs\Python\Python311\python.exe"
  ```
  支持参数 `-Steps crawl summarize`、`-Skip score`、`-ContinueOnError`、`-LogDirectory "D:\logs\edu-news"`。
- Windows 任务计划配置步骤：
  1. 打开“任务计划程序”→“创建任务”。
  2. 在“常规”里勾选“使用最高权限运行”，指定运行账号。
  3. 在“触发器”添加“每天”触发时间，设定时区与开始时间。
  4. 在“操作”选择“启动程序”，程序填 `powershell.exe`，参数填上面命令（记得用双引号包路径）。
  5. 在“条件/设置”里关闭“使用电池时停止”并允许错过后尽快运行，保存即可。

### 2. 保持手动触发入口
- 让 `run_console.py`（或 `uvicorn src.console.app:app`）常驻运行，可用 `systemd`、`supervisor`、`pm2` 等守护工具。
- 配置控制台的账号密码（`.env` 中的 `CONSOLE_USERNAME`/`CONSOLE_PASSWORD`）。
- 开放服务器防火墙或安全组端口（默认 8000），并在手机/电脑上测试访问 `http://服务器IP:8000/dashboard`。
- 视情况在前面加 Nginx 等反向代理，启用 HTTPS 和基本认证，确保远程访问安全。

### 3. 结果推送到手机
- 在 `export` 后添加通知脚本：读取最新导出批次（可直接访问 Supabase 或生成的文件）。
- 选择合适的推送渠道（邮件、企业微信/飞书机器人、PushDeer、Telegram 等），实现 API 调用发送摘要或文件链接。
- 先手动调用通知脚本测试，再集成到流水线的 `export` 末尾或新增 `notify` 步骤。
- 确保自动任务和手动触发时都能触发相同的推送逻辑。

## 后续优化（可选）
- 在控制台展示更多运行细节（耗时、错误摘要、导出列表）。
- 为推送内容添加筛选或格式化（例如按关键词分组、生成简报）。
- 加入健康检查和告警，当流水线失败或推送异常时及时提醒。
