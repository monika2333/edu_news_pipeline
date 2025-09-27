# 教育新闻自动化流水线

## 项目概览
- `tools/toutiao_scraper.py` 抓取今日头条作者主页并写入 Supabase `toutiao_articles` 表。
- `tools/summarize_supabase.py` 读取 `toutiao_articles`，按 `education_keywords.txt` 过滤后调用 SiliconFlow 生成摘要，upsert 至 `news_summaries`。
- `tools/score_correlation_supabase.py` 对 `news_summaries` 内缺失评分的文章调用 LLM，写回 `correlation` 分值。
- `tools/export_high_correlation_supabase.py` 从 `news_summaries` 导出高分摘要，并在 `brief_batches`/`brief_items` 追踪导出历史。

## 目录速览
- `tools/toutiao_scraper.py` / `tools/author.txt`：抓取脚本与作者列表示例。
- `tools/summarize_supabase.py`：摘要生成与 `news_summaries` 管理。
- `tools/score_correlation_supabase.py`：教育相关度评分，输出 `correlation`。
- `tools/export_high_correlation_supabase.py`：基于 `news_summaries` 导出文本并记录批次。
- `tools/supabase_adapter.py`：Supabase 访问封装，统一读取/写入逻辑。
- `supabase/schema.sql`：数据库结构模板，可在 Supabase 项目中初始化。
- `education_keywords.txt`：教育领域关键词（UTF-8，运行前请确认编码正确）。
- `data/`：运行期产物（如抓取缓存、摘要游标），除 `summarize_cursor.json` 外均可按需清理。
- `replace-news-JGW/`：用于人工修订，不参与流水线

## 环境准备
- Python 3.10+，建议 `python -m venv .venv && .venv/Scripts/activate`。
- 安装依赖：`pip install -r requirements.txt`；若需直连 Postgres，额外 `pip install "psycopg[binary]"`。
- Playwright 首次运行需 `playwright install chromium`。
- 默认顺序加载 `.env.local`、`.env`、`config/abstract.env`，建议仅保留 `.env.local` 并放入 `.gitignore`。

### 关键环境变量
**Supabase 抓取（Postgres 直连）**
- `SUPABASE_URL`
- `SUPABASE_DB_PASSWORD`
- 可选：`SUPABASE_DB_USER`（默认 `postgres`）、`SUPABASE_DB_PORT`（默认 `5432`）、`SUPABASE_DB_NAME`（默认 `postgres`）

**Supabase REST（摘要、评分、导出）**
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` 或 `SUPABASE_KEY`（建议服务密钥）

**SiliconFlow / LLM**
- `SILICONFLOW_API_KEY`
- 可选：`MODEL_NAME`、`CONCURRENCY`、`ENABLE_THINKING`、`SILICONFLOW_BASE_URL`

## 数据表摘要
- `toutiao_articles`：抓取产物，包含 token、文章标题、正文 `content_markdown`、抓取时间等。
- `news_summaries`：摘要结果，字段包括 `article_id`、`title`、`llm_summary`、`content_markdown`、`source`、`publish_time_iso`、`publish_time`、`url`、`llm_keywords`、`summary_generated_at`、`correlation`。
- `brief_batches` / `brief_items`：导出批次与条目记录，`brief_items.article_id` 对应 `news_summaries.article_id`，`metadata` 存储来源、分数等信息。

## 典型流程
1. **抓取今日头条作者**
   ```bash
   python tools/toutiao_scraper.py \
     --input tools/author.txt \
     --limit 150 \
   ```
   - `author.txt` 每行一个 token 或主页 URL，`#` 为注释。
   - 设置 Supabase Postgres 凭据并安装 `psycopg` 后，脚本会写入 `toutiao_articles`（可用 `--supabase-table` 指定目标表，`--reset-supabase-table` 清空重建）。
   - `--show-browser` 可开启有头浏览器便于排查封锁问题。

2. **关键词过滤 + 摘要**
   ```bash
   python tools/summarize_supabase.py \
     --keywords education_keywords.txt \
     --limit 200 \
     --concurrency 5
   ```
   - 默认读取/更新 `data/summarize_cursor.json`，从上一次成功的 `fetched_at` 继续；首次运行会自动创建。如需重新处理全部文章，请追加 `--reset-cursor`。
   - 核心字段缺失会被跳过，命令行 `--limit` 仅限制当次最大处理数量。
   - 经过关键词判定后再请求 LLM 摘要；完全相同的摘要会沿用原 `summary_generated_at`。
   - 写入 `news_summaries` 时会自动去重 `llm_keywords`。

3. **相关度评分**
   ```bash
   python tools/score_correlation_supabase.py --limit 200 --concurrency 5
   ```
   - 选择 `news_summaries` 中 `correlation` 为空的记录。
   - LLM 输出 0-100 分，写回 `correlation`。

4. **导出高相关摘要**
   ```bash
   python tools/export_high_correlation_supabase.py \
     --min-score 60 \
     --report-tag 2025-09-27-AM \
   ```
   - 从 `news_summaries` 选取 `correlation` ≥ 阈值的记录，按分类归组。
   - 文本末尾自动追加来源括号（如 `（北京日报客户端）`）。
   - 启用 `--record-history`（默认）会把导出写入 `brief_batches`/`brief_items`；`--skip-exported` 现在会同时参考历史导出（跨 tag）避免重复，如需强制重导可加 `--no-skip-exported`。

## 常见问题
- **Playwright 403/风控**：重新 `playwright install chromium`，或加 `--show-browser` 并手动处理验证。
- **Supabase 连接失败**：确认 `.env.local` 中 URL、密码无误；若仅需离线 JSON，可 `--skip-supabase-upload`。
- **LLM 限流**：将 `--concurrency` 降为 1，或设置 `CONCURRENCY=1` 后重试；准备备用模型。
- **关键词未命中**：确保 `education_keywords.txt` 保存为 UTF-8，运行日志会显示过滤数量。
- **重复导出**：使用不同 `--report-tag`，或在导出前执行 `--no-skip-exported` 重新生成。

## 后续计划
- 为 `news_summaries` 增加更多索引与视图（按日期、来源统计覆盖率）。
- 改造旧版导出/评分脚本的测试用例以覆盖 Supabase 新流程。
- 增补单元测试：抓取去重、关键词过滤、摘要复用、评分、导出历史等环节。
