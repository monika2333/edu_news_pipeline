# Edu News Pipeline

面向教育新闻的自动化采集、评分、摘要与导出流水线，并提供 Web 控制台进行人工筛选与复核。

## 功能总览
- **流水线**：抓取 → 去重 → 评分 → 摘要/情感 → 北京/外地分流与重要性评分 → 导出简报。
- **Web 控制台**：`/manual_filter` 进行人工筛选/审阅（簇展示、状态自动保存、排序模式、导出弹窗）；`/dashboard` 查看最近运行与最新导出并可手动触发；`/articles/search` 直接按关键词/来源/情感/状态检索。
- **导出/预览**：支持在审阅页导出文本或预览（可选标记为已导出）。

## 快速开始
1) 安装依赖
```bash
pip install -r requirements.txt
```
2) 启动控制台（默认 8000）
```bash
python run_console.py
```
- 建议设置 `CONSOLE_BASIC_USERNAME` / `CONSOLE_BASIC_PASSWORD` 或 `CONSOLE_API_TOKEN` 保护接口。

3) 运行流水线单步（示例）
```bash
python -m src.cli.main crawl --sources toutiao,tencent --limit 5000
python -m src.cli.main hash-primary
python -m src.cli.main score
python -m src.cli.main summarize
python -m src.cli.main external-filter
python -m src.cli.main export
```
可用 `-h` 查看每个步骤的参数。

-## Web 控制台
- 默认地址：`http://127.0.0.1:8000`
- **/manual_filter**
  - 默认按地域/情感聚类展示，可切换桶（京内正/京内负/京外正/京外负/全部）。
  - 卡片摘要可编辑，状态下拉/批量设置会自动保存并移动到对应列，放弃/待处理会移出视图。
  - 审阅页支持排序模式（紧凑卡片 + 拖拽），导出弹窗支持预览/正式导出。
- **/dashboard**
  - 查看最近流水线运行和最新导出概况，可从页面触发一次运行。
- **/articles/search**
  - 按关键词、来源、情感、状态、日期过滤；查看摘要与原文链接。
- **/xiaohongshu/summary**
  - 输入原文或指定路径（默认 `xiaohongshu-summary - origin/input_task.txt`），提取 `http://xhslink.com/o/...` 链接并调用 Codex 生成总结；支持轮询任务与复制输出。

## 配置要点（.env / .env.local）
- 数据库：`DB_HOST`、`DB_PORT`、`DB_NAME`、`DB_USER`、`DB_PASSWORD`、`DB_SCHEMA`。
- 抓取/评分：如 `TOUTIAO_AUTHORS_PATH`、`TENCENT_AUTHORS_PATH`、`PROCESS_LIMIT` 等。
- 控制台认证：`CONSOLE_BASIC_USERNAME` / `CONSOLE_BASIC_PASSWORD` 或 `CONSOLE_API_TOKEN`。
- 小红书总结：`XHS_SUMMARY_ROOT`（默认 `xiaohongshu-summary - origin`，用于定位输入/输出与提示词指南）；可用 `XHS_SUMMARY_FAKE_OUTPUT=1`（配合 `XHS_SUMMARY_FAKE_TEXT`）在无 Codex CLI 时模拟生成，正式环境需安装并登录 codex CLI。

## 目录速览
- `run_console.py`：控制台入口。
- `src/console/app.py`：FastAPI 应用与路由挂载。
- `src/console/routes/`：API 与页面路由（manual_filter、dashboard、articles/search 等）。
- `src/console/services/`：对应的业务逻辑。
- `src/console/web/templates/`：Jinja2 模板。
- `src/console/web/static/`：前端 JS / CSS 资源。
- `docs/`：提示词与流程文档。

## 说明
- 控制台访问默认仅监听本机，部署到外网时务必开启认证。
- 如果数据库不可用，部分接口会降级为空结果以保证页面可访问。
