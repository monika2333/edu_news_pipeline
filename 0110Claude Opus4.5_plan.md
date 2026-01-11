# 预聚类优化方案

## 一、核心决策

| 项目 | 决策 |
|------|------|
| **Embedding 模型** | `BAAI/bge-large-zh`，维度 1024，归一化 |
| **聚类算法** | 现有 Python 聚类逻辑，阈值 0.9 |
| **数据源** | 完全以 DB 为准，cluster=true 时禁用本地缓存（不走内存缓存） |
| **刷新策略** | 定时 5 分钟 + 手动触发 + 读时过滤（立即剔除）；默认仅刷新 zongbao，回退 pending 强制设为 zongbao |
| **存储形态** | 使用 `item_ids`（读时 join） |
| **返回策略** | summary-only（manual_summary 优先，llm_summary 兜底），按现有接口分页（单页上限 200），聚类输入上限 5000 |

---

## 二、数据库设计

### `manual_clusters` 表

| 字段 | 类型 | 说明 |
|------|------|------|
| `report_type` | text NOT NULL DEFAULT 'zongbao' | 固定 `zongbao`（保留字段用于兼容审阅页，不参与读写过滤） |
| `bucket_key` | text NOT NULL | 分桶标识（internal_positive 等） |
| `cluster_id` | text NOT NULL | 格式: `{bucket_key}-{index}` |
| `item_ids` | text[] NOT NULL | 该聚类包含的文章 ID 列表 |
| `created_at` | timestamptz | 默认 now() |

**索引**：`(bucket_key)`

**约束建议**：
- `UNIQUE (cluster_id)`
- `CHECK (bucket_key IN ('internal_positive','internal_negative','external_positive','external_negative'))`

**字段说明补充**：
- `report_type`：固定 `zongbao`，保留字段仅为兼容审阅页；条目从审阅页回退为 pending
  时强制设为 `zongbao`，避免 wanbao pending 消失。所有回退 pending 操作统一走
  `manual_filter_decisions.reset_to_pending`（内部强制 `report_type=DEFAULT_REPORT_TYPE`），
  最终由 `adapters.db_postgres.reset_manual_reviews_to_pending` 写入。
- `bucket_key`：与筛选页的 4 个分类一一对应，便于按 region/sentiment 快速读取。
- `created_at`：用于审计与排错，接口不依赖时间字段。

**缓存说明**：cluster=true 仅走 DB 结果，现有 `_cluster_cache` 与内存聚类分支将移除/不再使用。

---

## 三、刷新流程（Write Path）

```python
MANUAL_CLUSTER_LOCK_ID = 9001001

def refresh_clusters() -> None:
    lock_id = MANUAL_CLUSTER_LOCK_ID
    if not adapter.try_advisory_lock(lock_id):
        return

    rows = adapter.fetch_manual_pending_for_cluster(
        region=None, sentiment=None, fetch_limit=5000
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
                "cluster_id": f"{bucket_key}-{idx}",
                "bucket_key": bucket_key,
                "item_ids": [i["article_id"] for i in group_items],
            })

    try:
        with adapter.transaction():
            adapter.delete_manual_clusters()
            adapter.insert_manual_clusters(clusters)
    finally:
        adapter.release_advisory_lock(lock_id)
```

**要点**：advisory lock 使用固定 BIGINT 常量，防并发；事务内 delete + insert，失败回滚保留旧数据。

---

## 四、读取流程（Read Path）

```sql
WITH cluster_base AS (
    SELECT cluster_id, bucket_key, item_ids
    FROM manual_clusters
    WHERE ($1::text IS NULL OR bucket_key = $1)
),
cluster_items AS (
    SELECT cb.cluster_id, cb.bucket_key, unnest(cb.item_ids) AS article_id
    FROM cluster_base cb
)
SELECT ci.cluster_id, ci.bucket_key,
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
| `/api/manual_filter/trigger_clustering` | POST | 手动触发刷新（可选新增，固定 zongbao） |

**请求参数（GET）**：
- `limit` / `offset`：cluster 级分页，沿用现有接口逻辑。
- `region` / `sentiment`：筛选 bucket，对应 internal/external + positive/negative。
- `cluster`：true 返回聚类；false 返回原列表。
- `force_refresh`：尽力触发刷新；若锁占用则直接返回当前 manual_clusters 结果。
  前端默认同时传 `region` 与 `sentiment`，用于确定唯一 bucket_key。
  映射关系：internal + positive => internal_positive，internal + negative => internal_negative，
  external + positive => external_positive，external + negative => external_negative。

**请求参数（POST）**：无需参数（固定触发 zongbao 刷新）。

### 返回结构

```json
{
  "clusters": [{
    "cluster_id": "internal_positive-0",
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
| `delete_manual_clusters()` | 删除聚类结果（固定 zongbao） |
| `insert_manual_clusters(clusters)` | 批量插入聚类结果 |
| `fetch_manual_clusters(bucket_key)` | 读取聚类 + join 过滤（固定 zongbao） |
| `try_advisory_lock(lock_id)` / `release_advisory_lock(lock_id)` | 并发控制（`lock_id` 为固定 BIGINT 常量） |

---

## 七、修改执行清单

- [x] 新增 `manual_clusters` 表结构（字段/索引/约束）与迁移脚本
- [x] Adapter 实现：`delete_manual_clusters` / `insert_manual_clusters` / `fetch_manual_clusters`
- [x] Adapter 实现：`try_advisory_lock` / `release_advisory_lock`（使用固定 BIGINT `MANUAL_CLUSTER_LOCK_ID`）
- [ ] 写入刷新流程：拉取 pending + ready_for_export → 分桶 → 聚类 → 事务内 delete+insert → 释放锁
- [ ] 读取流程：按 `bucket_key` 读取 clusters → 展开 item_ids → join → 读时重建排序/代表标题/size → cluster 级分页
- [ ] API 调整：`/api/manual_filter/candidates` 支持 `cluster` / `force_refresh`；需要时新增 `/api/manual_filter/trigger_clustering`
- [ ] 移除 `_cluster_cache` 与旧内存聚类分支
- [ ] 基本验证：刷新一次并检查接口返回结构与排序
