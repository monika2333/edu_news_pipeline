# Export 并发指南

## 当前两条导出路径
- **人工筛选导出（console）**：`src/console/services/manual_filter.py:export_batch`，取 `manual_reviews` 中已选条目，生成文本并调用 `adapter.record_export(report_tag, export_payload, output_path)`；默认 `mark_exported=True` 把这些文章状态标记为 exported。
- **export worker**：`src/workers/export_brief.py:run`，按分数等规则挑选候选、分桶/聚类、写文本并同样调用 `adapter.record_export(...)`；默认 `skip_exported=True`，会查询已导出文章跳过重复。

## 共享资源（潜在冲突点）
- **DB 表**：两者都写 `brief_batches`/`brief_items`，共用 `report_tag` 查/建 batch；同一 tag 会落到同一 batch。
- **缺少来源字段**：`brief_items` 里没有来源标记（manual/worker），不同 tag 只是分属不同 batch，但数据仍混在一张表里。
- **去重方式**：仅在代码层面用 `get_export_history(tag)`/`get_all_exported_article_ids()` 做“先读后插”；表上未见 `(brief_batch_id, article_id)` 唯一约束，存在竞争窗口。
- **批次载荷**：`brief_batches.export_payload` 每次导出都会更新，最后写入者覆盖。
- **文章状态**：人工导出可选 `mark_exported=True` 触发 `update_manual_review_statuses`，worker 不会改状态，但会根据现有 `brief_items` 跳过导出。

## 并发运行会发生什么
- **不同 report_tag**：各自创建独立 batch，互不影响，只共享 `brief_items` 全局历史（用于 worker 跳过已导出）；记录仍落在同一张 `brief_items` 表，且没有来源字段，后续查询时需要靠 batch 过滤才能区分。
- **相同 report_tag**：两进程可能同时读取“当前 batch 无此 article_id”然后各自插入，导致 `brief_items` 重复；或某一方插入后另一方再写 `export_payload`，出现覆盖。若 DB 加了唯一键则会抛错（当前代码未处理重试）。

## 规避冲突的建议
1) **区分 report_tag**：并行时为人工导出或 worker 加上不同后缀（例如 `2024-07-01-manual` / `2024-07-01-auto`）。
2) **加唯一约束并处理冲突**：在 DB 为 `(brief_batch_id, article_id)` 建唯一索引；`record_export` 捕获冲突后跳过/重试，可彻底避免重复插入。
3) **分表隔离（推荐）**：新增 `manual_export_batches` / `manual_export_items` 专供人工筛选导出，worker 继续用现有表；这样数据物理隔离，无交叉影响。若不想分表，备选是给 `brief_items` 增来源字段（manual/worker）并按 `(brief_batch_id, article_id, source)` 加唯一索引。
4) **串行化同一 tag**：如果必须共享同一 report_tag，避免同时跑；或在应用层加锁（如基于 report_tag 的分布式锁）。

## 日常建议
- 默认情况下，同时跑两条线可行，但请务必使用**不同的 report_tag**；否则存在重复/覆盖风险。
- 即便区分了 report_tag，`brief_items` 仍共享一张表；若后续查询/分析需要区分来源，请依赖 batch 过滤或新增来源列/分表。
- 若准备长期并行运行，优先实施分表方案（`manual_export_batches` / `manual_export_items`）；次优为来源字段 + 唯一约束，使行为可预测。
