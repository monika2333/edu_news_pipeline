## 背景/目标
- 解决聚类接口缓存过期问题：缓存的 clusters total 与数据库 pending 总数不一致时自动重算，避免页面看到 pending>0 但 clusters 为空。

## 核心思路（对比总数触发重算）
1) 获取缓存：从 `_cluster_cache` 取当前 key 对应的结果（包含 clusters/total）。
2) 获取真实总数：执行轻量查询拿到当前 pending 的真实总数（最好按 region/sentiment 对齐；若实现成本高，可先用全局 pending 也能触发刷新）。
3) 判断：若无缓存，或缓存 total 与真实总数不一致，则触发重算（调用现有聚类逻辑 `perform_clustering`/`cluster_pending(..., force_refresh=True)`, 并更新缓存）。
4) 否则直接返回缓存。

## 预期修改点
- `src/console/services/manual_filter.py`：在 `cluster_pending` 命中缓存时增加“真实总数对比”逻辑，依赖新的计数查询；若不同则重算。
- `src/adapters/db_postgres.py`（或适配器接口）：增加获取 pending 总数的轻量方法（采用全局 pending 总数，不拆 region/sentiment），供 `cluster_pending` 调用。

## 注意事项
- 计数查询要走索引（status），保持轻量；避免大范围锁。
- 仍需保留 `force_refresh` 按钮逻辑；对比失败时也写回缓存，避免重复重算。
- 对比时谨慎处理 None/缺少字段，避免 KeyError。

## 验证思路
- 人工：先确认 `/api/manual_filter/stats` pending>0；在无缓存或缓存 total=0 时请求 `cluster=true` 应返回聚类结果；修改数据后（如新增 pending），再请求应触发重算并得到新簇。
- 自动化（如有时间）：为 `cluster_pending` 增加单测，mock adapter 返回不同的 pending_count，断言在 count 不一致时调用聚类并更新缓存。
