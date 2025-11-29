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
- 基础表：`news_summaries`（见 `database/schema.sql`），包含 `title/llm_summary/score/status/external_importance_status/is_beijing_related` 等。
- 手工筛选的新增字段（建议新增 migration）：
  - `manual_status text default 'pending'`：`pending|approved|discarded|exported`。
  - `manual_summary text`：保存用户编辑后的摘要。
  - `manual_score numeric(6,3)`：手调优先级（可选）。
  - `manual_notes text`：操作备注。
  - `manual_decided_by text` / `manual_decided_at timestamptz`。
- 候选池选择：默认拉取 `status in ('ready_for_export','pending_external_filter','pending_beijing_gate')` 且 `manual_status='pending'` 的记录，按 `score desc` 分页。
- 导出策略：仅导出 `manual_status='approved'`，导出后置为 `manual_status='exported'`；`discarded` 保留历史不再展示。

## 4. 功能设计
- 侧边栏（状态总览）：显示 `pending/approved/discarded/exported` 数量，可一键刷新。
- 主列表（分页 20，可配置）：按得分排序。
  - 卡片字段：复选框(Keep)、标题、可编辑摘要（初始用 `llm_summary`）、分数/来源/发布时间/情感/Beijing 标签。
  - 会话缓存：翻页返回时保留已编辑内容与勾选状态。
- 批量提交：底部“提交当前页”按钮。
  - 逻辑：将勾选项设为 `approved`（并写入编辑摘要/备注），未勾选项设为 `discarded`；空勾选弹出确认。
- 导出模块：按钮生成当日文案。
  - 查询 `manual_status='approved'`（可选时间/批次筛选）→ 拼装文本 → 页面 code block 展示及复制按钮。
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
  - 导出批次信息可追加到 `brief_batches/brief_items` 或新表 `manual_export_batches`（需确认）。

## 6. 安全与权限
- 优先复用现有 console 认证（Basic / Token），将 Streamlit 置于同一保护层；临时方案可用 Streamlit 内置密码提示。
- 所有写操作要求会话态的用户标识，落库 `manual_decided_by`。
- 记录操作日志：在控制台打印或写 `logs/`。

## 7. 迭代计划
- Milestone 1：DB 迁移（新增 manual 字段/表），服务层函数，导出文本拼装逻辑（含单元测试）。
- Milestone 2：Streamlit UI（列表/分页/提交/导出），集成服务层，基础异常提示。
- Milestone 3：认证/审计、导出批次记录、Webhook/飞书集成、空状态与加载体验优化。
- 验证：本地连测试库跑端到端；补充服务层 unit tests（可用 sqlite 替代），导出文本快照测试。

## 8. 待确认事项
1) 手工筛选作用的实际状态来源：是否仅挑 `ready_for_export`，还是包括 `pending_external_filter`/`pending_beijing_gate`。  
2) 是否接受在 `news_summaries` 增加 `manual_*` 字段，或更倾向新表记录决策。  
3) 导出格式：是否保持现有 TXT 的分组（京内/京外、正/负面，含 Emoji），是否需要文件落盘或仅展示复制。  
4) 认证方案：Streamlit 是否部署在现有 FastAPI 后面，是否强制 Token/Basic。  
5) 每页展示数量及排序（默认 20、按 `score desc` 是否满足）。  
6) 是否需要保留“撤销”或“重新入队”功能（将 discarded/approved 重新设为 pending）。
