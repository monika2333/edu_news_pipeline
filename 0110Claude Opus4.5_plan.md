# 预聚类优化方案（最终版）

基于 `0110_early_cluster.md` 原始提案和 `0110Codex_chat_history.txt` 的讨论，整理后的完整方案如下。

---

## 一、核心决策总结

| 项目 | 决策 |
|------|------|
| **Embedding 模型** | 继续使用 `BAAI/bge-large-zh`，维度 1024，归一化 |
| **聚类算法** | 保留现有 Python 聚类逻辑，阈值 0.9，不做修改 |
| **数据源** | 完全以 DB 为准，禁用本地缓存 |
| **刷新策略** | 定时 5 分钟 + 手动触发 + 用户操作后立即剔除（读时过滤） |
| **存储形态** | 使用 `item_ids`（读时 join），不使用 jsonb 快照 |
| **title_embedding** | 暂不添加，避免无效字段 |
| **返回策略** | summary-only 全量返回，上限 500/1000 |

---

## 二、数据库设计

### `manual_clusters` 表结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `report_type` | text NOT NULL | 报型（zongbao / wanbao） |
| `bucket_key` | text NOT NULL | 分桶标识（internal_positive 等） |
| `cluster_id` | text NOT NULL | 格式: `{report_type}-{bucket_key}-{index}` |
| `item_ids` | text[] NOT NULL | 该聚类包含的文章 ID 列表 |
| `created_at` | timestamptz | 默认 now() |
| `updated_at` | timestamptz | 默认 now() |

**索引建议**：
- `(report_type, bucket_key)`
- （可选）`item_ids` 的 GIN 索引，用于按 article_id 反查

> [!NOTE]
> 暂不存储 `model_name`、`version`、`threshold` 等元数据。保留 `updated_at` 用于前端"最后更新时间"。

---

## 三、刷新流程（Write Path）

### 流程伪代码

```python
def refresh_clusters(report_type: str) -> None:
    # 1. 获取 advisory lock，避免并发刷新
    if not adapter.try_advisory_lock("manual_cluster_refresh"):
        return

    # 2. 拉取 pending 数据（与现有筛选保持一致）
    rows = adapter.fetch_manual_pending_for_cluster(
        report_type=report_type,
        region=None,
        sentiment=None,
        fetch_limit=5000,
    )

    # 3. 按 is_beijing_related + sentiment_label 分 4 桶
    buckets = bucket_by_region_and_sentiment(rows)
    clusters = []

    for bucket_key, items in buckets.items():
        if not items:
            continue
        # 4. 按现有排序规则排序
        items_sorted = sorted(items, key=_candidate_rank_key_by_record, reverse=True)
        titles = [i.get("title") or "" for i in items_sorted]

        # 5. 使用现有 BGE + 阈值 0.9 聚类
        groups = cluster_titles(titles, threshold=0.9) or [list(range(len(items_sorted)))]

        for idx, group in enumerate(groups):
            group_items = [items_sorted[i] for i in group]
            if not group_items:
                continue
            group_items.sort(key=_candidate_rank_key_by_record, reverse=True)
            clusters.append({
                "cluster_id": f"{report_type}-{bucket_key}-{idx}",
                "report_type": report_type,
                "bucket_key": bucket_key,
                "item_ids": [i["article_id"] for i in group_items],
            })

    # 6. 事务内 delete + insert，失败回滚保留旧数据
    with adapter.transaction():
        adapter.delete_manual_clusters(report_type=report_type)
        adapter.insert_manual_clusters(clusters)

    adapter.release_advisory_lock("manual_cluster_refresh")
```

### 关键要点

- 使用 `pg_try_advisory_lock(hashtext('manual_cluster_refresh'))` 防止并发刷新
- **不要使用 TRUNCATE**，用 DELETE + INSERT 在同一事务中
- 失败回滚保证旧数据仍在
- 空结果也要 DELETE，避免前端看到旧集群

---

## 四、读取流程（Read Path）

### SQL 骨架

```sql
WITH cluster_base AS (
    SELECT cluster_id, bucket_key, item_ids, updated_at
    FROM manual_clusters
    WHERE report_type = $1
      AND ($2::text IS NULL OR bucket_key = $2)
),
cluster_items AS (
    SELECT cb.cluster_id,
           cb.bucket_key,
           cb.updated_at,
           unnest(cb.item_ids) AS article_id
    FROM cluster_base cb
)
SELECT
    ci.cluster_id,
    ci.bucket_key,
    ci.updated_at,
    mr.article_id,
    mr.status AS manual_status,
    mr.summary AS manual_summary,
    mr.rank AS manual_rank,
    COALESCE(mr.report_type, 'zongbao') AS report_type,
    ns.title,
    ns.llm_summary,
    ns.llm_source,
    ns.source,
    ns.url,
    ns.score,
    ns.external_importance_score,
    ns.sentiment_label,
    ns.is_beijing_related,
    ns.publish_time_iso,
    ns.publish_time,
    ns.score_details
FROM cluster_items ci
JOIN manual_reviews mr ON mr.article_id = ci.article_id
JOIN news_summaries ns ON ns.article_id = ci.article_id
WHERE mr.status = 'pending'
  AND ns.status = 'ready_for_export'
ORDER BY ci.cluster_id,
         ns.external_importance_score DESC NULLS LAST,
         mr.rank ASC NULLS LAST,
         ns.score DESC NULLS LAST,
         ns.publish_time_iso DESC NULLS LAST,
         mr.article_id ASC;
```

### 读时重建逻辑

1. 按 `report_type + bucket_key` 读出 clusters
2. 展开 `item_ids`，join `manual_reviews` + `news_summaries`
3. **读时过滤**：`pending` + `ready_for_export`
4. 对每个 cluster：
   - 重新计算 `size`
   - 用过滤后的首条作为 `representative_title`
   - items 按现有 rank 规则排序
5. 过滤后为空的 cluster 丢弃
6. 过滤后再分页，避免"分页空洞"

### `refreshed_at` 查询

```sql
SELECT MAX(updated_at) FROM manual_clusters WHERE report_type = $1;
```

---

## 五、API 设计

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/manual_filter/clusters` | GET | 返回预计算的聚类列表 |
| `/api/manual_filter/trigger_clustering` | POST | 手动触发聚类任务刷新 |

### 返回结构

```json
{
  "clusters": [
    {
      "cluster_id": "zongbao-internal_positive-0",
      "report_type": "zongbao",
      "bucket_key": "internal_positive",
      "size": 5,
      "representative_title": "...",
      "items": [
        {
          "article_id": "...",
          "title": "...",
          "summary": "...",
          "source": "...",
          "url": "...",
          "score": 85,
          "external_importance_score": 10,
          "is_beijing_related": true,
          "sentiment_label": "positive",
          "llm_source_display": "...",
          "llm_source_raw": "...",
          "llm_source_manual": "...",
          "bonus_keywords": []
        }
      ]
    }
  ],
  "total": 10,
  "refreshed_at": "2026-01-10T10:00:00Z"
}
```

---

## 六、立即剔除实现

- **不依赖本地缓存**，读时过滤 `pending` 即可实现"操作后立即剔除"
- 前端无需改逻辑，刷新列表就会看到最新状态
- 若 cluster 的代表条目被剔除，读时会自动切换为新的 representative
- 手动编辑 summary 会立刻体现在列表中（join 实时取最新值）

---

## 七、Adapter 需要新增的方法

| 方法 | 说明 |
|------|------|
| `delete_manual_clusters(report_type)` | 删除指定报型的聚类结果 |
| `insert_manual_clusters(clusters: list[dict])` | 批量插入聚类结果 |
| `fetch_manual_clusters(report_type, bucket_key=None)` | 读取聚类 + join 读时过滤 |
| `try_advisory_lock(name)` | 获取 PostgreSQL advisory lock |
| `release_advisory_lock(name)` | 释放 advisory lock |

---

## 八、风险与应对

| 风险 | 应对措施 |
|------|----------|
| **分钟级延迟** | 前端显示"最后更新时间"，提供手动刷新按钮 |
| **并发冲突** | 使用 advisory lock + 事务保证原子性 |
| **cluster 代表过期** | 读时重算 size 和 representative |
| **刷新失败** | 事务回滚保留旧数据 |
| **数据量控制** | 返回上限 500/1000，避免首屏过重 |

---

## 九、与原始方案的差异

| 原始方案 | 最终方案 |
|----------|----------|
| 新增 `title_embedding` 字段 | **暂不添加**，避免无效字段 |
| 使用 `vector_l2_ops` 索引 | **不使用**，保留现有聚类算法 |
| 存储 `representative_title` 和 `size` | **不存储**，读时动态计算 |
| 未明确过滤条件 | **明确与现有筛选保持一致** |
| 未明确立即剔除机制 | **读时过滤实现立即剔除** |
| 未明确并发控制 | **使用 advisory lock** |
