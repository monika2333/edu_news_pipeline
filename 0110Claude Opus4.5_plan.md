# 预聚类优化方案

## 一、核心决策

| 项目 | 决策 |
|------|------|
| **Embedding 模型** | `BAAI/bge-large-zh`，维度 1024，归一化 |
| **聚类算法** | 现有 Python 聚类逻辑，阈值 0.9 |
| **数据源** | 完全以 DB 为准，cluster=true 时禁用本地缓存（不走内存缓存） |
| **刷新策略** | 定时 5 分钟 + 手动触发 + 读时过滤（立即剔除）；默认仅刷新 zongbao，wanbao 可按需启用 |
| **存储形态** | 使用 `item_ids`（读时 join） |
| **返回策略** | summary-only（manual_summary 优先，llm_summary 兜底），按现有接口分页（单页上限 200），聚类输入上限 5000 |

---

## 二、数据库设计

### `manual_clusters` 表

| 字段 | 类型 | 说明 |
|------|------|------|
| `report_type` | text NOT NULL | 报型（zongbao / wanbao） |
| `bucket_key` | text NOT NULL | 分桶标识（internal_positive 等） |
| `cluster_id` | text NOT NULL | 格式: `{report_type}-{bucket_key}-{index}` |
| `item_ids` | text[] NOT NULL | 该聚类包含的文章 ID 列表 |
| `created_at` | timestamptz | 默认 now() |
| `updated_at` | timestamptz | 默认 now() |

**索引**：`(report_type, bucket_key)`

**约束建议**：
- `UNIQUE (report_type, bucket_key, cluster_id)`
- `CHECK (bucket_key IN ('internal_positive','internal_negative','external_positive','external_negative'))`

**字段说明补充**：
- `report_type`：与审阅页的综报/晚报一致；条目从审阅页回退为 pending 时仍保留
  report_type，因此聚类结果需要按报型隔离。
- `bucket_key`：与筛选页的 4 个分类一一对应，便于按 region/sentiment 快速读取。
- `created_at`/`updated_at`：用于审计与排错，接口不依赖时间字段。

---

## 三、刷新流程（Write Path）

```python
def refresh_clusters(report_type: str) -> None:
    lock_key = f"manual_cluster_refresh:{report_type}"
    if not adapter.try_advisory_lock(lock_key):
        return

    rows = adapter.fetch_manual_pending_for_cluster(
        report_type=report_type, region=None, sentiment=None, fetch_limit=5000
    )

    buckets = bucket_by_region_and_sentiment(rows)
    clusters = []

    for bucket_key, items in buckets.items():
        if not items:
            continue
        items_sorted = sorted(items, key=_candidate_rank_key_by_record, reverse=True)
        titles = [i.get("title") or "" for i in items_sorted]
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

    try:
        with adapter.transaction():
            adapter.delete_manual_clusters(report_type=report_type)
            adapter.insert_manual_clusters(clusters)
    finally:
        adapter.release_advisory_lock(lock_key)
```

**要点**：advisory lock 防并发，事务内 delete + insert，失败回滚保留旧数据。

---

## 四、读取流程（Read Path）

```sql
WITH cluster_base AS (
    SELECT cluster_id, bucket_key, item_ids, updated_at
    FROM manual_clusters
    WHERE report_type = $1 AND ($2::text IS NULL OR bucket_key = $2)
),
cluster_items AS (
    SELECT cb.cluster_id, cb.bucket_key, cb.updated_at, unnest(cb.item_ids) AS article_id
    FROM cluster_base cb
)
SELECT ci.cluster_id, ci.bucket_key, ci.updated_at,
       mr.article_id, mr.summary AS manual_summary, mr.rank AS manual_rank,
       mr.manual_llm_source,
       ns.title, ns.llm_summary, ns.llm_source, ns.source, ns.url, ns.score,
       ns.external_importance_score, ns.sentiment_label, ns.is_beijing_related,
       ns.publish_time_iso, ns.publish_time,
       ns.score_details
FROM cluster_items ci
JOIN manual_reviews mr ON mr.article_id = ci.article_id
JOIN news_summaries ns ON ns.article_id = ci.article_id
WHERE mr.status = 'pending' AND ns.status = 'ready_for_export'
ORDER BY ci.cluster_id, ns.external_importance_score DESC NULLS LAST,
         mr.rank ASC NULLS LAST, ns.score DESC NULLS LAST;
```

**SQL 排序说明**：SQL 的 ORDER BY 仅用于稳定读取，同一 cluster 的最终排序以读时重建为准。

**读时重建**：先按 cluster_id 分组 → 过滤 pending + ready_for_export → items 按
`_candidate_rank_key_by_record` 排序（external_importance_score → rank → score →
publish_time）→ 选首条为 representative_title → cluster 级排序（按 representative
的 rank_key）→ 重算 size → 丢弃空 cluster → cluster 级分页（沿用现有 limit/offset）。

**排序时间说明**：`publish_time` 仅用于排序，不对用户展示，时区不敏感，直接取字段值即可。

**字段生成规则**：
- `llm_source_*`：沿用 `_attach_source_fields`，`llm_source_manual` 来自
  `manual_llm_source`，`llm_source_raw` 来自 `llm_source`，`llm_source_display`
  取手动覆盖 > LLM 识别 > 原始来源。
- `bonus_keywords`：沿用 `_bonus_keywords(score_details)`，从
  `score_details.matched_rules` 的 `label`/`rule_id` 提取。

---

## 五、API 设计

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/manual_filter/candidates` | GET | 保持现有接口；使用 `cluster=true` 返回聚类列表 |
| `/api/manual_filter/trigger_clustering` | POST | 手动触发刷新（可选新增，支持按报型） |

**请求参数（GET）**：
- `limit` / `offset`：cluster 级分页，沿用现有接口逻辑。
- `region` / `sentiment`：筛选 bucket，对应 internal/external + positive/negative。
- `cluster`：true 返回聚类；false 返回原列表。
- `force_refresh`：尽力触发刷新；若锁占用则直接返回当前 manual_clusters 结果。
- `report_type`：默认 `zongbao`，用于按报型过滤 pending；筛选页当前无切换 UI，默认仅展示 zongbao 的 pending。

**请求参数（POST）**：
- `report_type`：默认 `zongbao`；支持传 `wanbao` 触发对应报型刷新。

### 返回结构

```json
{
  "clusters": [{
    "cluster_id": "zongbao-internal_positive-0",
    "report_type": "zongbao",
    "bucket_key": "internal_positive",
    "size": 5,
    "representative_title": "...",
    "items": [{
      "article_id", "title", "summary", "source", "url", "score",
      "external_importance_score", "is_beijing_related", "sentiment_label",
      "llm_source_display", "llm_source_raw", "llm_source_manual", "bonus_keywords"
    }]
  }],
  "total": 10
}
```

**total 说明**：total 为“过滤并丢弃空 cluster 后的 cluster 总数”，用于分页与计数。

---

## 六、Adapter 新增方法

| 方法 | 说明 |
|------|------|
| `delete_manual_clusters(report_type)` | 删除指定报型的聚类结果 |
| `insert_manual_clusters(clusters)` | 批量插入聚类结果 |
| `fetch_manual_clusters(report_type, bucket_key)` | 读取聚类 + join 过滤 |
| `try_advisory_lock(name)` / `release_advisory_lock(name)` | 并发控制 |

---

## 七、风险与应对

| 风险 | 应对措施 |
|------|----------|
| **分钟级延迟** | 提供手动刷新按钮 |
| **并发冲突** | advisory lock + 事务保证原子性 |
| **cluster 代表过期** | 读时重算 size 和 representative |
| **刷新失败** | 事务回滚保留旧数据 |
