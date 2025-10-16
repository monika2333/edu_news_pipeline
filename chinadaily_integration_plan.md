# China Daily 整合计划（crawl 集成）

## 目标
- 在 `src/adapters` 新增 `http_chinadaily.py`，对接 China Daily 中文网频道列表与详情页，输出统一结构。
- 在 `src/workers/crawl_sources.py` 增加 China Daily 爬取流程（`_run_chinadaily_flow`）与 `run()` 的 source 分支。
- 与现有源一致：支持 limit/pages、关键词过滤、详情补齐、入库、以及“连续 N 条已存在则提前停止”（默认 5）。
- 完成后删除 `chinadaily_crawler` 目录（用户提供的参考脚本），避免重复维护。

## 预期产出与影响
- 新增文件：`src/adapters/http_chinadaily.py`。
- 修改文件：`src/workers/crawl_sources.py`（导入、运行分支、环境变量接入）。
- 可选文档更新：在 `README.md` 增加使用示例（如：`--sources chinadaily`）。
- 清理：删除 `chinadaily_crawler/` 下的所有文件。

## 适配器设计（http_chinadaily.py）
- 基础：
  - 统一 UA、会话复用、响应编码修正（参考 `http_chinanews.py`）。
  - `make_article_id(url)`：从 URL path 规范化为 `chinadaily:/...` 前缀的稳定 ID。
- 列表抓取：
  - `list_items(limit: Optional[int], pages: Optional[int], existing_ids: Optional[Set[str]]) -> List[FeedItemLike]`。
  - 默认入口从环境变量 `CHINADAILY_START_URL` 读取（缺省为参考脚本中的频道 URL）。
  - 解析 `div.left-liebiao h3 a` 为条目，提取标题、URL、发布时间（从同块 `<p>` 或文本中解析）。
  - 翻页：跟随 `a.pagestyle` 文本含“下一页”的链接。
  - 提前停止：当 `existing_ids` 存在且命中同一页/跨页连续已存在条数达到 `CHINADAILY_EXISTING_CONSECUTIVE_STOP`（默认 5，0 为不提前停止）时停止抓取。
- 详情抓取：
  - `fetch_detail(url) -> Dict[str, Any]`：
    - 标题：优先 `<h1>`，回退 `og:title`、`twitter:title`、`<title>`。
    - 发布时间：从常见 meta（`publishdate`、`article:published_time` 等）或可见时间节点提取。
    - 正文容器：在 `#Content`、`.content`、`.main-content`、`.article`、`.TRS_Editor` 等选择器中择优；必要时基于包含 `content/article` 的 div 兜底。
    - 正文转 Markdown：处理换行、链接、图片、列表、表格，生成紧凑文本（参考提供脚本）。
    - `source`：若页面未给出，使用常量 `ChinaDaily`（或 `中国日报`），与库内展示保持一致。
  - 不覆盖列表给定的时间，除非详情能提供更可靠的 ISO 时间。
- 行映射：
  - `feed_item_to_row()` 与 `build_detail_update()` 与现有适配器对齐字段：`article_id/title/source/publish_time(_iso)/url/content_markdown/...`。

## Worker 集成（crawl_sources.py）
- 新增 `_run_chinadaily_flow(adapter, keywords, remaining_limit, pages)`：
  - 读取本地已存在 ID（用于去重与提前停止）。
  - 调用 `list_items(limit=remaining_limit, pages=pages, existing_ids=...)` 收集；
  - upsert feed；找出缺正文的文章请求 `fetch_detail()`；`update_raw_article_details()`；
  - 命中关键词则入 `pending_summary`；
  - 统计 `consumed/ok/failed/skipped` 返回，并扣减 `remaining_limit`。
- 在 `run()` 中加入 `elif source == 'chinadaily':` 分支；默认 sources 是否包含，将与现有行为对齐：
  - 保持默认只跑 `toutiao`，由使用者通过 `--sources` 显式选择（如：`--sources chinadaily` 或与其他源组合）。

## 环境变量（新）
- `CHINADAILY_START_URL`：China Daily 频道起始列表页，默认取参考脚本默认值。
- `CHINADAILY_TIMEOUT`：请求超时，默认 `20`。
- `CHINADAILY_EXISTING_CONSECUTIVE_STOP`：连续命中已存在条目的提前停止阈值，默认 `5`，`0` 表示不提前停止。

## 实施步骤
1) 复用/精简参考脚本的解析逻辑，完成 `http_chinadaily.py` 基础实现。
2) 在 `crawl_sources.py` 增加导入与 `_run_chinadaily_flow`，接入 `run()`。
3) 本地小样本验证：
   - 仅跑 `chinadaily`，限制 `--limit 10 --pages 1`，观察日志与入库结果；
   - 验证“连续 5 次已存在提前停止”按预期生效。
4) 更新 `README.md` 用法片段（可选）。
5) 删除 `chinadaily_crawler/` 目录。

## 验证清单
- 列表页：能获取条目、翻页正常、limit/pages 生效。
- 去重与提前停止：命中既有 ID 时连续计数与停止点正确。
- 详情页：标题/正文/时间/来源解析稳健，Markdown 无大段空行与噪声。
- 入库：`upsert_raw_feed_rows` 与 `update_raw_article_details` 无异常；`pending_summary` 只在命中关键词时写入。
- 与现有源并行使用：`--sources toutiao,chinanews,chinadaily,gmw` 正常运行并统计。

## 回滚/清理
- 如需回滚：移除 `http_chinadaily.py` 与 `crawl_sources.py` 中相关导入与分支。
- 清理：在验收通过后删除 `chinadaily_crawler/` 下文件，减少冗余。

## 时间预估
- 适配器实现与联调：4–6 小时（含健壮性处理）。
- Worker 集成与本地验证：1–2 小时。
- 文档与清理：0.5 小时。

