# 多源接入与“raw_articles”统一化改造计划（含中国新闻网）

## 背景与目标
- 扩展为多源统一架构：把 `toutiao_articles` 更名为通用的 `raw_articles`，用于存储所有来源的原始抓取结果。
- 保持“两阶段”流程：
  1) 多源采集（列表+详情）→ 写入/更新 `raw_articles`。
  2) 关键词过滤 → 命中文章入队 `news_summaries`（状态 `pending`），复用 `summarize -> score -> export`。
- 统一 Worker：不再为每个来源单独建 worker。将现有 `crawl_toutiao` 更名并泛化为单一的“多源采集” Worker，按配置调度不同来源适配器（包括中国新闻网）。

## 表结构与迁移
- 表更名：`toutiao_articles` → `raw_articles`（保留现有列；非通用列允许为 NULL）。
  - 列含义泛化：
    - `source`：来源名或频道名（如 `ChinaNews`、`中新网`、`edu` 等）。
    - `url`：原文链接。
    - `title/summary/publish_time/publish_time_iso/content_markdown/detail_fetched_at/fetched_at`：与现有语义一致。
    - `token/profile_url/comment_count/digg_count`：保留以兼容头条，其他来源可置空。
- 索引与触发器：沿用现有时间索引与 `updated_at` 触发器（名称需更新为 raw 版本）。
- 迁移方式：新增 SQL migration 脚本，包含：
  1) `ALTER TABLE public.toutiao_articles RENAME TO raw_articles;`
  2) 重建或重命名相关索引/触发器（如 `toutiao_articles_*` → `raw_articles_*`）。
  3) 兼容老数据，无需数据变形。

## 代码重构（统一化）
1) DB 适配器（`src/adapters/db_postgres.py`）
   - 方法改名并改 SQL 指向 `raw_articles`：
     - `upsert_toutiao_feed_rows` → `upsert_raw_feed_rows`
     - `update_toutiao_article_details` → `update_raw_article_details`
     - `get_toutiao_articles_missing_content` → `get_raw_articles_missing_content`
     - `fetch_toutiao_articles_missing_content` → `fetch_raw_articles_missing_content`
     - `get_existing_toutiao_article_ids` → `get_existing_raw_article_ids`
     - `fetch_toutiao_articles_for_summary` → `fetch_raw_articles_for_summary`（如仍需）
   - 同步调整索引/触发器名。

2) 统一 Worker（改名并泛化现有 `src/workers/crawl_toutiao.py`）
   - 新名建议：`src/workers/crawl_sources.py`（或 `ingest_sources.py`）。
   - 主要职责：
     - 根据 CLI 参数 `--sources toutiao,chinanews` 解析需要启用的来源集合。
     - 为每个来源调度对应适配器：
       - 头条沿用 `src/adapters/http_toutiao.py` 能力（Playwright 抓 feed + info）。
       - 中国新闻网通过新适配器抓列表+详情（见下）。
     - 通用流程：
       1) 列表/Feed → 生成 `feed_rows` → `upsert_raw_feed_rows`。
       2) 发现缺正文文章 → 抓详情 → `update_raw_article_details`。
       3) 关键词过滤命中 → `insert_pending_summary`。
     - 保持现有日志/汇总风格，支持 `--limit` 为总体上限，内部按来源分配（如平均或顺序消耗）。

3) 适配器层
   - 目录结构：继续放在 `src/adapters/` 下；新增 `http_chinanews.py`。
   - 轻量接口约定（无需抽象类也可先行约定函数名）：
     - `list_items(limit, pages, existing_ids)` → 产出统一的 `FeedItemLike(title, url, source, publish_time_iso, raw)`。
     - `resolve_article_id(item)` → 规范化 `article_id`（加来源前缀防冲突，如 `toutiao:`、`chinanews:`）。
     - `feed_item_to_row(item, article_id, fetched_at)` → 行字典（对齐 `raw_articles` 列）。
     - `fetch_detail(article_id or url, ...)` + `build_detail_update(...)` → 详情更新行。
   - 中国新闻网实现要点：
     - 列表页：`/scroll-news/news1.html`，选择器 `.content_list li`（`.dd_bt a` 链接、`.dd_lm` 栏目、`.dd_time` 时间）。
     - 详情页：正文容器候选 `#p-detail/.left_zw/#content`，开发期用样本校验并回退多选择器。
     - URL 归一化、HTML→Markdown 清洗与 ID 生成规则与先前计划一致。

4) CLI 调整（`src/cli/main.py`）
   - 将 `crawl` 子命令改为通用多源采集：维持命令名 `crawl` 不变，避免破坏兼容。
   - 参数：
     - `--sources`：逗号分隔，默认 `toutiao`；可设为 `toutiao,chinanews`。
     - `--limit`：总抓取上限（默认 500）。
     - `--pages`：对基于分页的来源生效（默认 1）。
     - `--concurrency`：并发（Playwright/Toutiao 仍可利用；中国新闻网串行为主）。
   - `main()` 中将 `crawl` 路由到新的 `crawl_sources.run(...)`。

## 关键词过滤策略
- 沿用 `education_keywords.txt`，在 Worker 详情抓取完成后执行。
- 命中即入队 `news_summaries`（`insert_pending_summary(article_row, keywords=[...], fetched_at=now_iso)`）。
- `insert_pending_summary` 已通过 `ON CONFLICT (article_id)` 做幂等写入，且不会覆盖已 `completed` 的摘要。

## 中国新闻网页面要点（初版假设，开发期确认）
- 列表页：`/scroll-news/news1.html`，分页规则可能为 `news1-2.html` 或类似形式。
  - 选择器：`.content_list li`；其中 `.dd_bt a`（标题+链接）、`.dd_lm`（栏目）、`.dd_time`（时间）。
- 详情页：正文容器候选 `#p-detail`、`.left_zw`、`#content`（需在实现时验证样本并回退多选择器策略）。
- UA 与超时：设置桌面 UA、`Accept-Language: zh-CN`，超时 10–15s；无需执行 JS。

## 去重与稳定性
- 去重：
  - 列表阶段：构造 `article_id` 后用 `get_existing_raw_article_ids` 快速跳过。
  - DB 层：`upsert_raw_feed_rows`/`update_raw_article_details` 都是幂等更新。
- 容错：
  - 网络/解析异常按条跳过并计数。
  - 详情缺失或正文为空的记录不入 `news_summaries`。

## 验证路径
1) 执行 `edu-news crawl --sources toutiao`，确认仍正常写入/更新 `raw_articles` 并入队 pending。
2) 执行 `edu-news crawl --sources toutiao,chinanews`，确认两源均能写入 `raw_articles` 并有命中文章入队。
3) 运行 `summarize -> score -> export`，验证端到端产出。

## 里程碑
1) M1：数据库迁移脚本（表更名、索引/触发器调整），DB 适配器改为 `raw_*`。
2) M2：统一 Worker 重命名并切换至 `raw_*` 方法，确保头条路径无回归。
3) M3：新增 `http_chinanews.py` 适配器；把 `crawl` 的 `--sources` 支持接入到多源调度。
4) M4：分页完善、解析稳健性增强（多选择器、移动页回退）、README 更新。

## 任务拆解（执行顺序）
1) Migration：`toutiao_articles` → `raw_articles`，索引/触发器同步。
2) DB 适配器：方法改名为 `raw_*` 并切换 SQL。
3) Worker：`crawl_toutiao.py` → `crawl_sources.py`（重命名+泛化）；复用现有流程但支持多源调度与 `--sources` 参数。
4) 适配器：中国新闻网 `http_chinanews.py`（列表/详情/清洗/ID 规则）。
5) CLI：`crawl` 子命令接入多源参数；保持其他子命令不变。
6) 联调与验证；补充 README 与基础解析测试。

## 风险与应对
- 结构变更风险：使用多选择器与回退策略；解析失败按条降级。
- 反爬/跳转：设置 UA/语言，必要时切换抓取为 `m.chinanews.com` 的对应页面。
- 兼容性：保留原列，保障头条数据与逻辑不受影响。

---

后续我将先提交迁移脚本与 DB 适配器重命名，再补中国新闻网适配器与 Worker。如需，我也可先最小化提交一版跑通端到端的实现供你验证。
