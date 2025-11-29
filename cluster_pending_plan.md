# 筛选阶段聚类方案（复用 export worker 聚类逻辑）

## 参考实现（export_worker）
- 路径：`src/workers/export_brief.py`，使用 `cluster_titles`（`src/adapters/title_cluster.py`，BGE embedding + cosine + greedy 分组）。
- 流程：同一情感/地域桶内先排序（rank/score/time），再按标题聚类；每个簇保留排序；簇按代表项排序。

## 目标
- 在筛选页点击“刷新”时，对所有 pending 文章聚类，聚类后前端按簇展示，簇级一次性选择采纳/备选/放弃（默认放弃）。
- 不改 DB schema，聚类结果临时返回给前端；审阅阶段仍可细调。

## 后端实现
1) 依赖：直接复用 `cluster_titles`（BGE embedding）。
2) 新服务函数：`cluster_pending(region: Optional[str], sentiment: Optional[str]) -> {clusters, total}`。
   - 取 pending + ready_for_export 数据（可复用 `_paginate_by_status` 查询，但不分页，或设置较大上限）。
   - 分桶：内部/外部、正/负，与筛选过滤一致。
   - 聚类：对每桶标题列表调用 `cluster_titles`，若空则单簇；构建结构：
     ```
     {
       cluster_id,  // 可以用 f"{bucket}-{idx}"
       bucket: {region, sentiment},
       items: [article_id...],
       ordered_items: 按 export_brief 相同排序 (rank->score->time)
       representative: 顶部项标题
     }
     ```
3) API：`GET /api/manual_filter/candidates` 增加参数 `cluster=true`。
   - `cluster=false` 返回现有列表；`cluster=true` 返回 `{clusters, total}`，其中 total 为 pending 总数或聚类项数。
4) 不写持久化：聚类结果不落 DB，只在响应中返回。

## 前端实现
1) 筛选页增加“聚类视图”开关（或自动按 cluster=true 获取）。
2) 展示：
   - 按桶（京内/京外×正/负）分区可选；簇卡片展示代表标题+数量。
   - 簇级状态单选（采纳/备选/放弃），簇内单条可覆写（可选简化：簇级决定全部）。
3) 提交：
   - 将簇级选择展开为所有 `article_id` 的状态，沿用现有 `/decide` + `/edit` 提交。
4) 交互：
   - 刷新时带 `cluster=true`；切换桶过滤继续使用现有 `region/sentiment` 参数。

## 排序与一致性
- 排序键：沿用 export_brief 的 `_candidate_rank_key`（ext_score, score, publish_time）。
- 簇排序：按簇内最高排序键降序。
- 簇内排序：保持排序键降序。

## 影响与风险
- 性能：BGE embedding 需加载模型，首次冷启动会耗时；可考虑懒加载/缓存 embeddings。
- 误聚类：标题语义模型可能将不相关项放一起；簇级批量操作需可撤回（默认放弃、可覆写）。
- 数据量：如 pending 很大，需限制一次聚类的上限或分批处理。

## 实施步骤
1) 后端：实现 `cluster_pending` 服务 + `cluster=true` API 参数，结构同上；共用 `title_cluster.py`。
2) 前端：增加聚类视图开关 + 簇级选择 UI；提交时展开到 per-article。
3) 性能验证：本地跑一次 pending 聚类，评估耗时；必要时增加阈值参数/批次限制。
