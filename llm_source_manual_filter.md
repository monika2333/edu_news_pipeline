# 人工筛选控制台接入并可编辑 `llm_source` 的方案

## 目标
- 控制台列表/审阅卡片中展示 LLM 识别出的来源 (`llm_source`)，并允许人工修改。
- 保存人工修改的来源；导出时优先使用人工填写的来源，其次用 LLM 识别值，最后回退抓取来源 (`source`)。

## 现状梳理
- `manual_reviews` 只存人工摘要等，不存来源；`llm_source` 仅存在于 `news_summaries`。
- 控制台前端仅显示 `source`；提交时只保存摘要，导出时 `ExportCandidate.llm_source` 传入 `None`，导出文本使用 `source`。
- 相关代码位置：
  - 数据库：`database/schema.sql` `manual_reviews` 表。
  - 后端：`src/console/services/manual_filter.py`、`src/adapters/db_postgres.py`、`src/console/routes/manual_filter.py`。
  - 前端：`src/console/web/templates/manual_filter.html`、`src/console/web/static/js/dashboard.js`。

## 设计方案
### 数据层
1) 在 `manual_reviews` 表新增可空列 `manual_llm_source text`（表示人工修改后的来源）。  
   - 新建迁移：`database/migrations/<timestamp>_add_manual_llm_source_to_manual_reviews.sql`，`ALTER TABLE manual_reviews ADD COLUMN IF NOT EXISTS manual_llm_source text;`。
2) Postgres 适配器调整：
   - 所有读取 `manual_reviews` 的查询返回 `manual_llm_source`，同时保留 `news_summaries.llm_source` 与 `source`。
   - `update_manual_review_summaries` 支持写入 `manual_llm_source`（当 edit payload 中带上时更新）。

### 服务 & API 层
1) `manual_filter.list_candidates/cluster_pending/list_review/list_discarded`：返回字段增加
   - `llm_source_manual`（或 `manual_llm_source`）：人工值
   - `llm_source_raw`：LLM 识别值
   - `source`：抓取来源
   - `llm_source_display`：后端直接计算好，优先顺序 `manual_llm_source > llm_source > source`，供前端直接使用。
2) `save_edits` 接口支持 `llm_source` 字段（和 `summary` 并列）；服务层将其写入 `manual_llm_source`。
3) 导出逻辑 `export_batch`：
   - 生成 `ExportCandidate` 时设置 `llm_source` = `manual_llm_source or llm_source or source`。
   - 文本拼装时的 `source_text` 改为上述优先级。

### 前端
1) 过滤/聚类列表卡片 & 审阅列表卡片中，新增一个可编辑的来源输入框：
   - Label 示例：`来源（LLM识别，可改）`。
   - 初始值：使用接口返回的 `llm_source_display`，并保留原始 `llm_source_raw` 供对比（可在 placeholder 中显示 “原始：xxx”）。
2) 提交/保存时：
   - `submitFilter` 构造 `edits[id] = { summary, llm_source }`。
   - 审阅页 `handleSummaryUpdate`、状态切换等请求也一并发送当前来源值。
3) 界面提示：当来源为空时用 placeholder 提示“留空则导出时回退抓取来源”。

## 开发步骤清单
- [x] **数据库**：新增迁移并更新 `database/schema.sql`（已完成；请在目标 DB 执行迁移）。
- [x] **后端**：
  - `db_postgres.update_manual_review_summaries` 支持 `manual_llm_source` 更新；相关 SELECT 补充列。
  - `manual_filter` 服务中构建返回体与导出候选时使用来源优先级。
  - Pydantic `SaveEditsRequest` 增加 `llm_source: Optional[str]`。
- [ ] **前端**：
  - `manual_filter.html`：在摘要下方插入来源输入框。
  - `dashboard.js`：渲染和事件中读写 `llm_source`，随保存/提交一并发送；导出前不需要额外动作。
- [ ] **测试/验证**：
  - 新增/修改一条卡片的来源，确认 `/api/manual_filter/edit` 响应正常，刷新后值被回填。
  - 导出预览文本中来源显示为人工值；清空后回退到 LLM/source。
  - 兼容聚类展开、排序模式、批量提交等路径。

## 验收要点
- 控制台所有列表都能看到来源且可编辑，保存后刷新不丢失。
- 导出文本来源与最新人工输入一致；未填人工时不改变现有行为。
- 迁移前的老数据/接口仍可正常使用。
