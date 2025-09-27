# 教育新闻自动化流水线

## 项目概览
- 基于今日头条作者主页实时抓取教育类资讯，并保存为结构化 JSON 或直接写入 Supabase。
- 后续流程全部围绕 Supabase 架构：自动摘要、相关度评分与高相关内容导出。
- 关键词过滤规则集中在 `education_keywords.txt`，通过 LLM 只保留教育场景需要的素材。

## 环境准备
- Python 3.10+，建议使用虚拟环境：`python -m venv .venv && .venv/Scripts/activate`。
- 安装依赖：`pip install -r requirements.txt`，若需要将抓取结果写入数据库，请额外安装 `pip install "psycopg[binary]"`。
- Playwright 需要安装浏览器内核：`playwright install chromium`。
- `.env.local` / `.env` / `config/abstract.env` 会被自动读取，推荐在 `.env.local` 中维护密钥，仓库中不要提交该文件。

### Supabase 凭据
`tools/toutiao_scrapy/toutiao_scraper.py` 直接连接 Supabase Postgres，需要以下环境变量：
- `SUPABASE_URL`
- `SUPABASE_DB_PASSWORD`
- 可选：`SUPABASE_DB_USER`（默认 `postgres`）、`SUPABASE_DB_HOST`（默认根据 URL 推导）、`SUPABASE_DB_NAME`（默认 `postgres`）、`SUPABASE_DB_PORT`（默认 `5432`）、`SUPABASE_DB_SCHEMA`（默认 `public`）。

下游脚本通过 Supabase REST 访问，至少需要：
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` 或 `SUPABASE_KEY`（推荐使用服务密钥以便写操作）

SiliconFlow 调用所需：
- `SILICONFLOW_API_KEY`
- 可选：`MODEL_NAME`、`CONCURRENCY`、`ENABLE_THINKING`、`SILICONFLOW_BASE_URL`

## 数据流程
1. **采集今日头条作者文章**  
   `python tools/toutiao_scrapy/toutiao_scraper.py --input tools/toutiao_scrapy/author.txt --limit 150`  
   - `author.txt` 支持作者 token 或完整主页 URL，每行一个，`#` 开头为注释。  
   - 默认将结果写入 `tools/toutiao_scrapy/data/toutiao_articles.json`。若配置了 Supabase 凭据且安装了 `psycopg`，会同步写入 `public.toutiao_articles`（可用 `--supabase-table` 调整目标表，`--reset-supabase-table` 覆盖写入）。  
   - 运行时通过 Playwright 调用今日头条接口，`--show-browser` 可打开非无头模式便于排查；`--limit 0` 表示抓取所有可用内容。  
   - 再次运行时会基于 Supabase 中已存在的 `article_id` 自动跳过重复内容。

2. **落地到 Supabase 主流程表**  
   - Supabase 架构定义见 `supabase/schema.sql`，核心表为 `sources`、`raw_articles`、`filtered_articles`、`brief_batches`、`brief_items`。  
   - 可以直接在数据库中用 SQL 将 `toutiao_articles` 映射到 `raw_articles`，或编写一个小脚本调用 `tools.supabase_adapter.SupabaseAdapter.upsert_article` 把 JSON 结果写入 `raw_articles`（推荐按作者来源维护 `sources` 表，避免缺失外键）。

3. **关键词筛选与摘要**  
   `python tools/summarize_supabase.py --keywords education_keywords.txt --limit 200 --concurrency 5`  
   - 仅处理 `raw_articles` 中尚未生成摘要的记录，并基于关键词过滤内容。  
   - 成功后会在 `filtered_articles` 中创建或更新摘要、关键词、处理元数据。

4. **相关度打分**  
   `python tools/score_correlation_supabase.py --concurrency 5 --limit 200`  
   - 对待审核或未打分的摘要调用 LLM 输出 0-100 的教育相关度评分，并写回 `filtered_articles.relevance_score`。

5. **导出高相关摘要**  
   `python tools/export_high_correlation_supabase.py --min-score 65 --report-tag 2025-09-20-AM --output outputs/highlight.txt`  
   - 从 `filtered_articles` 中筛选达到阈值的摘要，按类别落在不同分组，输出到文本文件。  
   - `--skip-exported` / `--record-history` 会在 `brief_batches` 与 `brief_items` 中维护历史，防止重复导出。

## 目录速览
- `tools/toutiao_scrapy/`：今日头条抓取脚本及作者列表示例。
- `tools/supabase_adapter.py`：封装 Supabase 常用 CRUD（抓取落地、摘要候选、评分、导出记录等）。
- `tools/summarize_supabase.py`：关键词过滤 + SiliconFlow 摘要。
- `tools/score_correlation_supabase.py`：调用 LLM 进行教育相关度打分。
- `tools/export_high_correlation_supabase.py`：按分类导出摘要并记录批次。
- `supabase/schema.sql`：数据库结构定义，可直接在 Supabase 项目执行。
- `tests/test_export_high_correlation.py`：导出分类逻辑的单元测试示例。

## 常用排查
- Playwright 报错：重新执行 `playwright install chromium`，或加 `--show-browser` 查看页面是否被风控。
- Supabase 上传失败：确认已安装 `psycopg[binary]`，并检查 `SUPABASE_DB_*` 变量是否正确；若仅使用 REST 接口，可加 `--skip-supabase-upload` 先输出 JSON。
- LLM 接口超时：降低 `--concurrency`，或在 `.env.local` 中调小 `CONCURRENCY` 并配置备用模型。
- 导出无结果：确认相关度阈值、关键词配置，或在 Supabase 后台检查 `filtered_articles` 是否已填充摘要与评分。

## 后续计划
- 将 `tools/toutiao_scrapy/toutiao_scraper.py` 的产出自动写入 `raw_articles`，减少手动同步。
- 更新 `run_pipeline.py` 以适配新的 Supabase-only 流程。
- 补充更多自动化测试与告警，覆盖抓取失败和 LLM 超时场景。
