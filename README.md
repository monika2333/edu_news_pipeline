# Edu News Pipeline

面向教育新闻的自动化采集、评分、摘要与导出流水线，并提供 Web 控制台进行人工筛选与复核。

## 功能总览
- **流水线**：抓取 → 去重 → 评分 → 摘要/情感 → 北京/外地分流与重要性评分 → 导出简报。
- **Web 控制台**：`/manual_filter` 进行人工筛选/审阅（簇展示、状态自动保存、排序模式、导出弹窗）；`/dashboard` 查看最近运行与最新导出并可手动触发；`/articles/search` 提供关键词检索。
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

## Web 控制台
- 默认地址：`http://127.0.0.1:8000`
- **/manual_filter**
  - 默认按地域/情感聚类展示，可切换桶（京内正/京内负/京外正/京外负/全部）。
  - 卡片摘要可编辑，状态下拉/批量设置会自动保存并移动到对应列，放弃/待处理会移出视图。
  - 审阅页支持排序模式（紧凑卡片 + 拖拽），导出弹窗支持预览/正式导出。
- **/dashboard**
  - 查看最近流水线运行和最新导出概况，可从页面触发一次运行。
- **/articles/search**
  - 关键词检索；默认返回摘要与链接，正文按需加载。

## 配置要点（.env / .env.local）
- 数据库：`DB_HOST`、`DB_PORT`、`DB_NAME`、`DB_USER`、`DB_PASSWORD`、`DB_SCHEMA`。
- 抓取/评分：如 `TOUTIAO_AUTHORS_PATH`、`TENCENT_AUTHORS_PATH`、`PROCESS_LIMIT` 等。
- 控制台认证：`CONSOLE_BASIC_USERNAME` / `CONSOLE_BASIC_PASSWORD` 或 `CONSOLE_API_TOKEN`。

## 数据库迁移 (Database)

我们使用 **Dbmate** 进行数据库版本管理。请确保设置了 `DATABASE_URL` 环境变量，以便 dbmate 识别。

### 常用操作
```powershell
# 设置环境变量 (PowerShell)
$env:DATABASE_URL="postgres://postgres:Postgres2025@localhost:5432/edu_news_pipeline?sslmode=disable"

# 查看迁移状态
dbmate status

# 执行迁移 (升级)
dbmate up

# 创建新迁移文件
dbmate new <migration_name>
# 示例: dbmate new add_users_table

# 回滚迁移 (撤销)
dbmate down
```

### 注意事项
- 迁移文件保存在 `database/migrations/`。
- 如果 `DATABASE_URL` 格式不正确，dbmate 会提示 "invalid url"。

## 目录速览
- `run_console.py`：控制台入口。
- `src/console/app.py`：FastAPI 应用与路由挂载。
- `src/cli/main.py`：流水线命令行入口。
- `src/workers/`：抓取、去重、评分、摘要、外部过滤、导出等流水线步骤。
- `src/adapters/`：数据库、HTTP 源、LLM、通知等外部系统适配器。
- `src/domain/`：领域模型、评分、地域判断、导出格式等业务规则。
- `src/console/*_routes.py`：API 与页面路由（manual_filter、dashboard、articles/search 等）。
- `src/console/*_service.py`：控制台业务逻辑。
- `src/console/*_schemas.py`：控制台请求/响应结构。
- `src/console/web_templates/`：Jinja2 模板。
- `src/console/web_static/`：前端 JS / CSS 资源。
- `database/migrations/`：Dbmate 数据库迁移。
- `docs/`：提示词、控制台认证和流程文档。
- `AGENTS.md`：面向 AI agent 的全局开发约束与命令说明。

## 说明
- 控制台访问默认仅监听本机，部署到外网时务必开启认证。
- 如果数据库不可用，部分接口会降级为空结果以保证页面可访问。
