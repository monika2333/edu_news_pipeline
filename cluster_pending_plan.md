# 筛选阶段聚类方案（复用 export 聚类，默认开启）

## 参考实现（export_brief）
- 路径：`src/workers/export_brief.py`，使用 `cluster_titles`（`src/adapters/title_cluster.py`，BGE embedding + cosine + greedy 分组）。
- 逻辑：同一情感/地域桶内先排序（rank/score/time），再按标题聚类；簇内保持排序，簇按代表项排序。

## 目标
- 筛选页刷新时默认对 pending+ready_for_export 文章聚类，并按簇展示。
- 簇级一次性选择采纳/备选/放弃（默认放弃），簇内保留单条信息；审阅阶段可再细调。

## 后端实现（已采用的思路）
- 参数：`GET /api/manual_filter/candidates` 支持 `cluster=true`，可同时用 `region/sentiment` 过滤。
- 数据：查询 pending+ready_for_export（限制上限，如 1000 条），按地域/情感桶分组。
- 聚类：每桶对标题调用 `cluster_titles`，无结果则单簇；结构包含 `cluster_id`、region/sentiment、size、representative、items（排序后）。
- 排序：簇内与 export 一致（ext_score, score, publish_time）；簇按代表项排序。
- 不落库：聚类结果仅在响应返回，不改 DB schema。

## 前端实现（已采用的思路）
- 默认聚类模式：刷新时请求 `cluster=true`；左侧过滤按钮（全部/京内正/京内负/京外正/京外负）触发重新拉取聚类结果。
- 展示：簇卡片含代表标题与数量，簇级单选（采纳/备选/放弃）一键应用簇内全部；簇内保留原卡片信息、摘要输入、原文链接。
- 提交：簇级选择展开为 per-article 状态，复用现有 `/decide` + `/edit`。
- 提示：聚类仅供辅助，审阅阶段可再调整。

## 风险与注意
- 性能：BGE 模型首次加载耗时，可考虑缓存/批次上限；如 pending 很大需限量或分页聚类。
- 误聚类：语义相似度可能误分，簇级操作应可覆盖/撤回（默认放弃兜底）。
- 过滤一致性：`region/sentiment` 过滤与左侧按钮保持一致；cluster=true 为默认。

## 后续可选优化
- 可配置聚类阈值/上限；必要时支持切换 TF-IDF 轻量方案作为降级。
- 支持簇内单条覆写状态（当前簇级为主，单条仍可编辑摘要）。
