# 中国教育报（jyb.cn）整合计划（crawl 集成）

## 目标
- 在 `src/adapters` 新增 `http_chinaeducationdaily.py`（避免与已有 `http_chinadaily.py` 混淆），对接中国教育报（jyb.cn）的列表/搜索与详情页，输出统一结构。
- 在 `src/workers/crawl_sources.py` 增加中国教育报的抓取流程（`_run_jyb_flow`）与 `run()` 的 source 分支。
- 与其他信息源一致：支持 limit/pages、详情补齐、关键词过滤、入库，以及“连续 N 条已存在则提前停止”（默认 5）。
- 完成后清理参考脚本目录：`jyb_crawl/`（用户提供的 `jyb_spider.py`），避免重复维护。（注：此前 `chinadaily_crawler/` 已清理）

## 适配器设计（http_chinaeducationdaily.py）
- Session/Headers：
  - 统一 UA、Accept-Language、Accept、Referer（`http://www.jyb.cn`），必要时添加随机 `X-Forwarded-For` 以缓解 403（参考 `jyb_spider.py`）。
  - 响应编码：优先 `apparent_encoding` 兜底，兼容可能的 GBK 内容。
- URL/ID：
  - `normalize_url`、`make_article_id(url)` -> 形如 `jyb:/path` 的稳定 ID。
- 列表抓取：
  - 站点以搜索/频道为入口：
    - 优先 JSON API：`http://new.jyb.cn/jybuc/hyBaseCol/search.action`（参考脚本）
    - 兜底解析搜索页/频道页 HTML 元素（如 `.res-list li`、`.search-result li`、`.clist li` 等）
  - 导出条目：`FeedItemLike(title, url, section, publish_time_iso, raw)`；
  - 提前停止：当 `existing_ids` 连续命中达到 `JYB_EXISTING_CONSECUTIVE_STOP`（默认 `5`，`0` 不启用）即停止。
- 详情抓取：
  - 标题：`<h1>`、`<title>`。
  - 时间：meta 或正文可见文本里的时间（用正则 `20\d{2}-..` 提取）。
  - 正文容器：`#js_content, .xl_text, .new_content, #content, .content, .TRS_Editor` 等；必要时选文本量最大的 `<div>`；
  - Markdown 转换：处理换行、链接、图片、列表、表格，生成紧凑文本（参考脚本的 `render_*` 思路）。
  - `source`：常量“`中国教育报`”。
- 行映射：
  - `feed_item_to_row()` 与 `build_detail_update()` 对齐现有字段：`article_id/title/source/publish_time(_iso)/url/content_markdown/...`。

## Worker 集成（crawl_sources.py）
- 新增 `_run_jyb_flow(adapter, keywords, remaining_limit, pages)`：
  - 读取已存在 ID；
  - 调用 `list_items(limit=remaining_limit, pages=pages, existing_ids=...)`；
  - upsert feed；对缺正文的请求 `fetch_detail()`；`update_raw_article_details()`；
  - 命中关键词则入 `pending_summary`；
  - 统计 `consumed/ok/failed/skipped`，并在 `run()` 扣减 `remaining_limit`。
- `run()` 增加 `elif source == 'jyb':` 分支；默认 sources 不改变（仍按 `.env.local` 设置）。

## 环境变量（新）
- `JYB_START_URL`：搜索/频道起始页（默认使用搜索页或站点首页）。
- `JYB_SEARCH_API_URL`：JSON 搜索 API（默认 `http://new.jyb.cn/jybuc/hyBaseCol/search.action`）。
- `JYB_KEYWORDS`：可选，搜索关键词（留空为站点默认；也可设计为多个关键词逗号分隔，适配器轮询）。
- `JYB_TIMEOUT`：请求超时（默认 `15`–`20s`）。
- `JYB_EXISTING_CONSECUTIVE_STOP`：连续已存在阈值（默认 `5`，`0` 关闭）。

## 实施步骤
1) 基于 `jyb_spider.py` 提炼 API/HTML 两套抓取逻辑，完成 `http_chinaeducationdaily.py`：
   - 列表：优先 API；无数据时回退 HTML；
   - 详情：容器选择 + Markdown 转换；
   - 提前停止逻辑；
   - 环境变量接入。
2) 在 `crawl_sources.py` 增加导入与 `_run_jyb_flow`，接入 `run()`；
3) 本地验证：
   - `list_items(limit=30, pages=3, existing_ids=set())` 样本；
   - `fetch_detail()` 抽样验证；
4) CLI 验证（有库）：
   - `python -m src.cli.main crawl --sources jyb --limit 50 --pages 3`；
5) 更新 README 用法（可选）；
6) 清理 `jyb_crawl/` 目录文件。

## 验证清单
- 列表/分页：返回条目、limit/pages 生效；
- 提前停止：命中既有 ID 时连续计数与停止点正确；
- 详情：标题/时间/正文 Markdown 稳健；
- 入库：feed upsert、detail update 无异常；
- 关键词：命中则入 `pending_summary`；
- 与其他源并行：`--sources toutiao,chinanews,chinadaily,jyb,gmw` 正常运行。

## 回滚/清理
- 回滚：移除 `http_chinaeducationdaily.py` 与 worker 中相关导入/分支；
- 清理：合并并验收后删除 `jyb_crawl/` 文件。

## 时间预估
- 适配器实现与联调：4–6 小时；
- Worker 集成与本地验证：1–2 小时；
- 文档与清理：0.5 小时。

