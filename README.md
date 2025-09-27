# 教育新闻自动化流水线

## 项目概览
- 通过 `tools/toutiao_scrapy/toutiao_scraper.py` 抓取今日头条作者主页，生成结构化数据并写入 Supabase `toutiao_articles` 表。
- `tools/summarize_supabase.py` 读取抓取结果，结合关键词筛选和 LLM 完成摘要，归档到 `news_summaries` 表。
- 后续可在 `news_summaries` 基础上继续实现相关度评分、导出等能力；旧的 SQLite 流水线已废弃。

## 目录速览
- `tools/toutiao_scrapy/`：抓取脚本与 `author.txt` 示例。
- `tools/summarize_supabase.py`：Supabase + SiliconFlow 摘要任务，输出 `news_summaries`。
- `tools/score_correlation_supabase.py`、`tools/export_high_correlation_supabase.py`：旧版 Supabase 摘要/导出脚本，待适配新表。
- `tools/supabase_adapter.py`：Supabase 通用封装，供脚本共享。
- `supabase/schema.sql`：数据库建表脚本，可作为 Supabase 项目初始化模板。
- `education_keywords.txt`：教育领域关键词（UTF-8 编码）。

## 环境准备
- Python 3.10+；建议使用虚拟环境：`python -m venv .venv && .venv/Scripts/activate`。
- 安装依赖：`pip install -r requirements.txt`；若需要直接连 Postgres，额外 `pip install "psycopg[binary]"`。
- Playwright 抓取需安装内核：`playwright install chromium`。
- 默认加载 `.env.local` -> `.env` -> `config/abstract.env`（后两者可选）。建议只保留 `.env.local` 并加入 `.gitignore`。

### Supabase 凭据
`toutiao_scraper.py` 直接使用 Postgres 连接，需要：
- `SUPABASE_URL`
- `SUPABASE_DB_PASSWORD`
- 可选：`SUPABASE_DB_USER`（默认 `postgres`）、`SUPABASE_DB_PORT`（默认 `5432`）、`SUPABASE_DB_NAME`（默认 `postgres`）。

摘要脚本通过 Supabase REST，需要：
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` 或 `SUPABASE_KEY`（建议服务密钥）

SiliconFlow 配置：
- `SILICONFLOW_API_KEY`
- 可选：`MODEL_NAME`、`CONCURRENCY`、`ENABLE_THINKING`、`SILICONFLOW_BASE_URL`

## 数据表说明
- `toutiao_articles`：抓取产物，字段含作者 token、文章元数据、正文 `content_markdown` 等。
- `news_summaries`：摘要表，字段 `article_id`、`title`、`llm_summary`、`content_markdown`、`source`、`publish_time_iso`、`publish_time`、`url`、`llm_keywords`、`summary_generated_at`（自动 upsert）。

## 典型流程
1. **抓取今日头条作者**
   ```bash
   python tools/toutiao_scrapy/toutiao_scraper.py \
     --input tools/toutiao_scrapy/author.txt \
     --limit 150 \
     --output tools/toutiao_scrapy/data/toutiao_articles.json
   ```
   - `author.txt` 支持 token 或主页 URL，一行一个，`#` 为注释。
   - 若提供 Supabase Postgres 凭据且安装 `psycopg`，脚本会同步写入 `toutiao_articles`（可用 `--supabase-table` 调整，`--reset-supabase-table` 清空重建）。
   - `--show-browser` 可打开有头浏览器便于排查。

2. **关键词过滤 + 摘要写入 `news_summaries`**
   ```bash
   python tools/summarize_supabase.py \
     --keywords education_keywords.txt \
     --limit 200 \
     --concurrency 5
   ```
   - 仅处理 `toutiao_articles` 中正文非空的记录。
   - 命中关键词后调用 LLM 生成摘要；已有摘要会复用并保持 `summary_generated_at`。
   - 结果 upsert 到 `news_summaries`，`llm_keywords` 自动去重保存。

3. **后续处理（规划中）**
   - `tools/score_correlation_supabase.py` / `tools/export_high_correlation_supabase.py` 仍基于旧表结构，需改造为读取 `news_summaries`。
   - 可在 `news_summaries` 基础上编写 Streamlit 展示、导出日报等功能。

## 常见问题
- **抓取失败/403**：重新执行 `playwright install chromium`，必要时加 `--show-browser` 手动登录排查。
- **Supabase 连接失败**：确认 `.env.local` 中的 DB 密码、项目 URL 正确；若仅输出 JSON 可加 `--skip-supabase-upload`。
- **LLM 调用限流**：降低 `--concurrency` 或在 `.env.local` 中设定 `CONCURRENCY=1`，并准备备用模型。
- **关键词未命中**：`education_keywords.txt` 使用 UTF-8，注意实际文本编码；可在运行日志中查看被过滤数量。

## 适配旧脚本的建议
- 更新评分脚本，改为读取 `news_summaries.llm_summary` 并写回统一的评分字段（可在表中新增 `relevance_score`）。
- 导出逻辑可直接基于 `news_summaries` 构建，或增加视图聚合不同来源。
- 如需保留 SQLite 流程，请手动维护相关脚本；默认 README 不再覆盖。

## 后续规划
- 在 Supabase 侧添加触发器/视图，统一统计抓取量、摘要覆盖率。
- 为 `news_summaries` 增加索引（如 `publish_time`、`llm_keywords`）提升查询效率。
- 新增自动化测试，覆盖关键词过滤、摘要写入、重复处理等关键路径。
