# 删除刷新按钮及 force_refresh 链路（2026-09-22 交付）

> 状态：已合入 main（merge 249b63e）并推送 origin，worktree/分支已删。10 条变异（M1–M10）全捕获；全量 1206 passed。

## 改动口径

- 前端：manual_filter.html 两处按钮+tooltip、init.js 绑定、core.js `shouldForceClusterRefresh`、filter_tab_data.js force 参数、workspace.js 的 force_refresh 转发、layout.css 三个规则块全部删除；连带清掉 loadFilterData/reloadCurrentTab 的死 options 管道。
- 后端：`/api/manual_filter/candidates` 与 `/api/duty/shifts/*/clusters` 的 `force_refresh` 参数及 service 层透传、cluster_pending 的重建分支、duty_review_service 对 manual_filter_cluster 的导入全部移除。

## 保留的链路（重要）

**聚类缓存唯一重建入口**：Windows 计划任务 EduNewsManualClusterRefresh 每 5 分钟跑 `python -m src.cli.main refresh-manual-clusters` → `/api/manual_filter/trigger_clustering` 底层的 `refresh_clusters()`。该端点前端无调用者，**勿误判为死代码删除**。删按钮后新新闻进聚类分组最多延迟 5 分钟，属预期行为。

## 测试防护

两处 HTML absence 断言、JS/CSS 源码 absence 断言、`/candidates` 与值班 clusters 路由「不转发 force_refresh」行为断言、`test_list_clusters_does_not_rebuild_cluster_cache`（inspect.getsource 断言 duty_review_service 读路径无 refresh_clusters，避免变异时真连 DB）。

- 值班工作台 HTML 里 `data-workspace-action-tab="filter"` 随按钮一起消失（原是唯一 filter 域动作），duty 页测试断言已翻转为 not in。
- 动机：用户主动要求删按钮省地方；「刷新会重新聚类约 1 分钟」的体验被 5 分钟自动重建取代。
