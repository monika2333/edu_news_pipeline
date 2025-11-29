# 筛选控制台实现计划

## 1. 背景与目标
- 现状：流水线生成的每日摘要需人工在 TXT 中挑选、修改，流程分散且易漏。
- 目标：提供一个基于 Streamlit 的 Web 控制台，完成「人工筛选 + 批量导出」闭环，替代 TXT 编辑。
- 范围：仅覆盖人工筛选、导出环节；不改动爬取/打分/摘要逻辑。

## 2. 技术方案与依赖
- 前端/交互：Streamlit（Python），单页应用，使用 `st.form` 和 `st.session_state` 管理分页及编辑态。
- 数据访问：沿用 `src.adapters.db.get_adapter()` 的 Postgres 连接，新增一层服务封装读取/更新。
- 环境：读取 `.env.local`/`.env`/`config/abstract.env`，核心变量 `DB_*`。
- 部署：本地/内网运行，初期直接 `streamlit run dashboard.py`；后续可加 FastAPI 反代或 Basic Auth（复用现有 console 认证方案）。

## 3. 数据与状态映射
- 基础表：`news_summaries`（见 `database/schema.sql`），包含 `title/llm_summary/score/status/is_beijing_related` 等。
- 手工筛选的存储方案：
  - 推荐：新增字段到 `news_summaries`（优先理由：查询/导出无需 join；写入和导出原子性好；表已经承载状态机，保持单表更直观）。字段：`manual_status/manual_summary/manual_score/manual_notes/manual_decided_by/manual_decided_at`。
- 候选池：仅拉取 `status='ready_for_export'` 且 `manual_status='pending'` 的记录。
- 排序：`score desc` 表示按分数从高到低排序。
- 导出策略：仅导出 `manual_status='approved'`，导出后批量更新为 `manual_status='exported'`；`discarded` 保留历史不再展示。

## 4. 功能设计
- 侧边栏（状态总览）：显示 `pending/approved/discarded/exported` 数量，可一键刷新。
- 主列表（分页 30）：按得分排序（score 降序）。
  - 卡片字段：复选框(Keep)、标题、可编辑摘要（初始用 `llm_summary`）、分数/来源/发布时间/情感/Beijing 标签。
  - 会话缓存：翻页返回时保留已编辑内容与勾选状态。
- 批量提交：底部“提交当前页”按钮。
  - 逻辑：将勾选项设为 `approved`（并写入编辑摘要/备注），未勾选项设为 `discarded`；空勾选弹出确认。
- 导出模块：按钮生成当日文案。
  - 查询 `manual_status='approved'`（可选时间/批次筛选）→ 拼装文本（沿用现有分组/排序）→ 页面 code block 展示、可复制并落盘到文件。
  - 导出后批量更新 `manual_status='exported'`，并记录批次号/操作者。
  - 预留 webhook/飞书通知钩子。
- 错误提示：DB 失败时 toast + 不阻塞前端展示。

## 5. 后端与数据交互
- 新建 `src/console/services/manual_filter.py`（或同名模块）封装：
  - `list_candidates(limit, offset, filters)`：返回列表 + total。
  - `bulk_decide(approved_ids, discarded_ids, edits, actor)`：参数化 SQL 批量更新。
  - `export_batch(filter_opts)`：查询 approved，生成文本块并更新 `manual_status`。
- DB 访问注意：
  - 使用参数化 SQL，避免字符串拼接 IN 子句。
  - 更新语句示例：
    ```sql
    update news_summaries
    set manual_status = 'approved',
        manual_summary = coalesce(%(summary)s, llm_summary),
        manual_notes = %(notes)s,
        manual_decided_by = %(user)s,
        manual_decided_at = now()
    where article_id = any(%(ids)s);
    ```
  - 导出批次信息可追加到 `brief_batches/brief_items` 或新建 `manual_export_batches`（需确认）。

## 6. 安全与权限
- 按当前需求：Streamlit 独立部署且不做认证（内部使用）。后续如需对外，可通过反代 + Basic/Token 加壳。
- 所有写操作可记录操作者标识（如果后续引入认证或通过环境变量/启动参数传入）。
- 记录操作日志：在控制台打印或写 `logs/`。

## 7. 迭代计划
- Milestone 1：DB 迁移（新增 manual 字段/表），服务层函数，导出文本拼装逻辑（含单元测试）。
- Milestone 2：Streamlit UI（列表/分页/提交/导出），集成服务层，基础异常提示。
- Milestone 3：认证/审计、导出批次记录、Webhook/飞书集成、空状态与加载体验优化。
- 验证：本地连测试库跑端到端；补充服务层 unit tests（可用 sqlite 替代），导出文本快照测试。

## 8. 待确认事项
（根据当前答复已锁定）
1) 候选仅 `ready_for_export`。
2) 更倾向新增字段；如需强审计/多版本可改用新表。
3) 导出保持现有分组与文件落盘。
4) Streamlit 独立部署，无认证。
5) 每页 30，`score desc` 即分数高→低。
6) 需要支持撤销/重新入队（pending）。

## 9. 实施 Checklist
- [x] 设计并执行 migration：为 `news_summaries` 增加 `manual_status/manual_summary/manual_score/manual_notes/manual_decided_by/manual_decided_at`。
- [x] 在 `src/console/services` 创建 `manual_filter.py`，实现 `list_candidates`、`bulk_decide`、`export_batch`，使用参数化 SQL。
- [x] 新增导出落盘逻辑（保留现有分组/排序），记录到现有 `brief_batches/brief_items`，并标注手工筛选来源（例如新增 batch 元数据字段）。
- [x] 开发 Streamlit `dashboard.py`：状态栏、分页列表（30 条）、编辑摘要、批量提交、导出显示与下载；使用 `st.form` + `st.session_state` 记忆勾选/编辑。
- [ ] 支持撤销/重新入队：提供操作入口将 `manual_status` 恢复为 `pending`。
- [ ] 增加轻量单测：服务层（选择/批量更新/导出文本拼装），可用 sqlite/fixtures。
- [ ] 本地联通测试库跑端到端，验证提交与导出后状态流转（pending → approved/discarded → exported）。
- [ ] 文档补充：运行方式、环境变量、导出文件路径、已知限制。
