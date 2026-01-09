# 0109 系统需求分析与技术方案

## 一、需求整理

### 1. 核心痛点 (Core Pain Points)
- **性能问题 (Performance)**: 每次打开控制台或审阅完一页新闻后，系统都需要"现场"重新进行聚类 (Clustering)。这个过程非常缓慢，严重影响体验。
- **并发与阻塞 (Concurrency & Blocking)**:
  - 系统目前是单线程/阻塞式的。
  - 在进行查询或其他耗时操作时，整个控制台卡死，无法进行任何其他操作。
  - 缺乏多用户支持，无法多人同时审阅或查看实时更新。

### 2. 功能需求 (Functional Requirements)
- **多用户预留**: 虽然不需要立刻实现完整的注册登录，但在架构设计上要为未来支持多用户登录、权限管理和并发操作预留空间。
- **代码重构**: 现有代码文件较多且杂乱，希望在增加新功能的同时进行整理（Refactoring），增加必要的注释和文档。
- **文档完善**: 需要清晰的代码注释和开发文档。

---

## 二、现状分析 (Current System Analysis)

经过对代码的阅读 (`src/console/manual_filter_cluster.py`, `src/adapters/title_cluster.py`, `src/adapters/db_postgres.py`)，发现以下技术瓶颈：

### 1. 聚类机制 (Clustering Mechanism)
- **现状**: 目前 `cluster_pending` 函数会在**每次请求**时：
  1. 从数据库拉取最多 5000 条待处理新闻。
  2. 在内存中加载 BGE (Transformer) 模型。
  3. 计算所有标题的 Embedding 并计算相似度矩阵 (CPU 密集型)。
  4. 进行聚类并分页返回。
- **问题**: 这是一个典型的 **CPU 密集型 (CPU-bound)** 任务。随着数据量增加，计算时间呈指数级或线性增长。在 Web 请求的主线程中执行此操作会直接阻塞服务器，导致页面响应极慢。

### 2. 数据库与线程模型 (DB & Threading)
- **现状**: `run_console.py` 使用 `uvicorn` 单进程运行。数据库适配器 `src/adapters/db_postgres.py` 使用的是 `psycopg` 的**同步 (Synchronous)** 连接模式。
- **问题**:
  - 由于数据库操作是同步的，任何慢查询都会阻塞 Worker 线程。
  - 结合 CPU 密集的聚类操作，这导致了"单线程卡死"现象。一个用户的操作（如查询）会阻塞事件循环，导致其他请求（甚至同一用户的后续请求）无法被处理。

---

## 三、技术方案建议 (Technical Proposal)

为了解决上述问题，建议采用 **"异步计算 + 预处理 + 读写分离"** 的策略。

### 1. 架构调整：引入异步任务与预计算 (Pre-computation)
**核心思路**: 既然"现场聚类"太慢，我们就改为"后台预聚类"。用户访问时，只读取已经聚类好的结果 (Read-Model)。

- **新增 `manual_clusters` 表**: 用于存储聚类结果。结构大致为 `cluster_id`, `item_ids`, `report_type`, `status`, `rank_key` 等（读取时 join）。
- **后台 Worker (Re-clustering Worker)**:
  - **触发机制**: 定期（如每 5-15 分钟）或由"新数据入库"事件触发。
  - **全量重算策略 (Snapshot Strategy)**:
    1. Worker 从 `manual_reviews` + `news_summaries` 拉取**所有**当前状态为 `pending` 且 `ready_for_export` 的新闻（按 report_type 分桶）。
    2. 对这整个集合进行一次完整的 `cluster_titles` 聚类计算。
    3. 将生成的聚类结果生成一个新的快照 (Snapshot)，覆盖或标记旧的聚类结果失效。
  - **优势**: 这完全满足您的需求——只要新闻还在 `pending` 池中，每次有新数据进来，它都会和新数据重新尝试"抱团"。
  - **状态流转**: 一旦用户在前端对某组新闻进行了处理（移入 Review Tab 或标记处理为 `selected` / `backup` / `discarded`），这些新闻的状态不再是 `pending`，下次 Worker 运行时自然会将它们排除，不再参与聚类。
- **前端改造**:
  - `manual_filter/list` 接口不再进行现场聚类，改为直接查询 `manual_clusters` 表。这是一个极快的数据库 SELECT 操作 (< 10ms)。
  - 当用户审阅一条新闻时，只需在数据库中通过 ID 移除或标记该新闻，不需要触发全局重聚类。

### 2. 解决并发阻塞 (Concurrency)
- **数据库异步化**: 建议逐步迁移到 `psycopg[binary,pool]` 的异步模式或使用 `asyncpg`，或者在短期内将所有慢速 DB 操作放入 `await run_in_executor` 中执行，释放事件循环。
- **多线程/多进程**: 确保耗时的计算任务（如聚类、导出的大量计算）不在 Web 服务的主进程中运行，而是交给 Worker。

### 3. 多用户架构预留 (User System Preparation)
- **数据库设计**: 新增 `users` 表。
  - 字段: `id`, `username`, `password_hash`, `role`, `created_at`.
- **鉴权中间件**: 在 `src/console/dependencies.py` 中增加 `get_current_user` 依赖。虽然初期可以写死或只做简单的 Token 验证，但这为未来多用户登录打好基础。
- **数据隔离/共享**:
  - 在 `manual_logs` 或操作记录表中增加 `operator_id` 字段，记录是谁进行了操作。

### 4. 代码重构与整理 (Refactoring)
建议对 `src` 目录进行分层梳理：
- `src/web`: 存放所有 API 路由 (`routers`), 依赖 (`dependencies`).
- `src/services`: 业务逻辑层 (聚类逻辑、导出逻辑移动到这里)。
- `src/core`: 核心配置、工具类、异常定义。
- `src/models`: Pydantic 模型与数据库模型定义。
- `src/adapters`: 保持与外部系统（DB, LLM, Crawler）的交互。

---

## 四、讨论点 (Discussion Points)

在开始实施前，需要确认以下决策：

1.  **预计算的时效性**: 采用"后台预聚类"意味着新抓取的新闻可能不会**毫秒级**出现在聚类列表中，而是会有几秒到一分钟的延迟（取决于 Worker 频率）。这是否可接受？（一般来说为了系统流畅度，这是最佳权衡）。
2.  **技术栈选择**: 是否同意引入由独立线程/进程管理的简单 Worker？（不需要引入 Redis/Celery 等重型组件，保持轻量级，使用简单的 `threading` 或 `multiprocessing` 配合 DB 锁即可）。
3.  **重构范围**: 是在本次修改中一次性完成文件结构的大重组，还是分阶段进行？一次性重组可能会涉及大量改动，建议分模块逐步迁移。

---

## 五、补充记录 (Addendum)

### 状态流转
- 一旦用户在前端对某组新闻进行了处理（移入 Review Tab 或标记处理为 `selected` / `backup` / `discarded`），这些新闻的状态不再是 `pending`，下次 Worker 运行时自然会将它们排除，不再参与聚类。

---

## 六、已确认决策 (Decisions)

- 向量检索：保留 BGE，优先 pgvector 作为向量存储/检索方案。
- 刷新策略：准实时 + 手动刷新兜底。
- 协作冲突：用户规模较小，暂不引入锁；采用 version 作为主校验，允许部分成功；`actor` 字符串占位审计。
- 账号体系：先预留字段和接口（本地用户表 + 角色 + 审计字段），保留 Basic/Token 作为管理员后门。
- 重构范围：以 `src/console` 为目标整理结构，融入新功能后保持高内聚低耦合。
- pgvector：允许安装扩展；索引使用 hnsw。
- manual_clusters：仅存 `item_ids`（读时 join），保留 `report_type` 与 `status` 字段。

---

## 七、方案细化（一）：乐观并发控制

### 1) 字段/数据模型
- `manual_reviews` 增加版本字段（推荐）：`version integer not null default 0`。
- 保留/利用现有 `updated_at`，并预留 `updated_by`（或复用 `decided_by` 但建议区分）：
  - `updated_by`：最后一次写入的用户标识（审计字段）。
  - `updated_at`：最后一次写入时间（已存在）。

> 说明：版本字段用于强一致 CAS；`updated_at` 可作为弱一致兜底（仅比对时间）。本期选择 version 作为主校验。

### 2) 接口/请求响应
- **列表/详情接口**返回 `version` + `updated_at`（用于前端携带）：
  - `GET /api/manual_filter/candidates`
  - `GET /api/manual_filter/review`
  - `GET /api/manual_filter/discarded`
- **写入接口**新增乐观并发字段（向后兼容，未传则不校验）：
  - `POST /api/manual_filter/decide`
  - `POST /api/manual_filter/edit`
  - `POST /api/manual_filter/order`
  - `POST /api/manual_filter/reset`（如有）

#### 推荐请求结构（兼容旧格式）
- `decide`：
  - 旧：`selected_ids`, `backup_ids`, `discarded_ids`, `pending_ids`
  - 新：`selected_items`, `backup_items`, `discarded_items`, `pending_items`
  - 每个 item：`{ article_id, expected_version }`
- `edit`：
  - `edits[article_id] = { summary, llm_source, expected_version }`
- `order`：
  - `selected_order_items = [{ article_id, expected_version }]`
  - `backup_order_items = [{ article_id, expected_version }]`

#### 服务端更新逻辑（示意）
- 单条写入使用 CAS：
  - `UPDATE ... SET ..., version = version + 1 WHERE article_id = %s AND version = %s`
- 批量写入：逐条尝试 CAS，记录冲突列表。

### 3) 冲突处理策略
- **默认策略：Best-effort**
  - 成功的更新直接生效；失败的记录进入 `conflicts` 列表返回给前端。
- **可选严格模式（预留）**
  - `strict=true` 时只要出现冲突则整体失败（返回 409 并提示刷新）。

#### 响应建议
- `200 OK`：
  - `updated_count`, `conflict_count`, `conflicts`（含 `article_id`、`current_version`、`current_status`、`updated_at`）
- `409 Conflict`（严格模式）：
  - `conflicts` 列表 + 建议刷新。

### 4) 前端处理建议
- 若返回 `conflicts`：
  - 弹出提示“部分条目已被他人修改，已自动刷新”。
  - 自动刷新当前列表或仅刷新冲突条目。
- 操作级别：
  - 单卡片编辑 -> 冲突则提示并回滚输入框。
  - 批量操作 -> 显示冲突数量并刷新。

### 5) 兼容与迁移
- 先新增字段与返回值，不改前端也可运行（不传 `expected_version` 就不校验）。
- 前端逐步升级为携带 `expected_version`，再打开“严格模式”开关。

---

## 八、方案细化（二）：Console 模块重构

### 1) 重构目标
- 以 `src/console` 为唯一入口，统一路由注册与模块组织。
- 按“路由 -> 服务 -> 数据模型/适配器”分层，避免跨层耦合与循环引用。
- manual_filter 作为复杂业务子域独立收敛，其他功能保持轻量清晰。
- 保持可逐步迁移，避免一次性大改导致不可用。

### 2) 目标目录结构（建议）
- `src/console/app.py`：FastAPI 应用与路由挂载。
- `src/console/security.py`：认证逻辑（保留 Basic/Token）。
- `src/console/articles_routes.py` / `articles_service.py` / `articles_schemas.py`
- `src/console/exports_routes.py` / `exports_service.py` / `exports_schemas.py`
- `src/console/runs_routes.py` / `runs_service.py` / `runs_schemas.py`
- `src/console/health_routes.py`
- `src/console/web_routes.py`：页面路由（Jinja2）。
- `src/console/manual_filter/`（高内聚子模块）
  - `routes.py`：API 路由
  - `service.py`：业务门面（对外唯一入口）
  - `schemas.py`：请求/响应模型
  - `clustering.py` / `decisions.py` / `export.py` / `helpers.py`
- `src/console/web_templates/` 与 `src/console/web_static/`

> 说明：manual_filter 子模块只在 `src/console/manual_filter` 内部拆分；外部只通过 `service.py` 访问，降低耦合。

### 3) 迁移步骤（渐进式）
1. **确定 manual_filter 的唯一入口**
   - 将现有 `src/console/manual_filter_*.py` 迁移到 `src/console/manual_filter/` 子模块。
   - 在旧路径保留薄封装（re-export），避免一次性改动所有引用。
2. **统一路由挂载**
   - `app.py` 只从 `src/console/*_routes.py` 和 `src/console/manual_filter/routes.py` 引入。
3. **分层清理**
   - 路由只做参数解析与返回；复杂逻辑下沉到 `service.py`。
   - helper 只保留纯函数（无数据库/外部依赖）。
4. **命名与 API 稳定性**
   - 统一 `*_routes.py`、`*_service.py`、`*_schemas.py` 的命名规则。
   - 旧路径标记“弃用”，在文档中说明迁移窗口。
5. **更新测试与文档**
   - 更新 `tests/test_manual_filter_service.py` 的导入路径。
   - 增补 `docs/console_refactor.md`：模块职责与依赖关系。

### 4) 与新功能的融合点
- 乐观并发控制：字段与校验逻辑放在 `manual_filter/decisions.py` + `schemas.py`。
- pgvector/聚类预计算：相关服务封装为 `manual_filter/clustering.py` 并在 service 中统一调用。

### 5) 产出物清单
- `src/console/manual_filter/` 子模块完成迁移与封装。
- 旧路径保留兼容层（短期）。
- 文档：`docs/console_refactor.md` + `0109_analysis.md` 更新。

---

## 九、方案细化（三）：pgvector + 准实时聚类

### 1) 总体思路
- 保留 BGE 向量模型，向量落库（pgvector），聚类结果预计算。
- 前端读取“已聚类结果表”，避免请求内现场聚类。
- 准实时：后台任务周期性/触发式更新（例如 1-5 分钟一次）+ 手动刷新兜底。

### 2) 数据表设计（建议）
#### (A) 向量表 `manual_title_embeddings`
- `article_id text primary key`
- `title text`
- `embedding vector(1024)`  (BGE-large-zh 维度 1024)
- `updated_at timestamptz not null default now()`
- 索引：`ivfflat` 或 `hnsw`（pgvector v0.5+ 支持 hnsw）。

#### (B) 聚类结果表 `manual_clusters`
- `cluster_id text primary key`
- `bucket_key text`（internal_positive 等）
- `report_type text`（zongbao/wanbao）
- `status text`（pending/selected/backup/discarded）
- `size integer`
- `representative_title text`
- `item_ids text[]`（读取时 join）
- `rank_key jsonb`（用于排序的分值）
- `snapshot_id uuid`（用于标记本次批次）
- `created_at`, `updated_at`

#### (C) 聚类快照表 `manual_cluster_snapshots`
- `snapshot_id uuid primary key`
- `pending_total integer`
- `generated_at timestamptz`
- `source_note text`

> 说明：本方案按 `report_type` 分开生成 snapshot，并每个报型保留最近 3 个（旧的软删除或删除）。

#### (D) Snapshot 说明与保留建议
- **含义**：snapshot 表示一次“全量聚类结果”的批次 ID，用来保证读写隔离与一致性。
- **现状**：当前系统没有持久化 snapshot。
- **建议**：新增 `manual_cluster_snapshots` 并按 `report_type` 保留最近 3 个；前端只读取对应报型最新 snapshot。

### 3) 后台任务（准实时刷新）
- **触发策略**：
  - 定时任务由业务侧自行配置（例如 3-5 分钟），此处仅提供启动脚本。
  - 若 `pending_total` 无变化可跳过重算（降低空转）。
  - 手动刷新按钮触发“立即生成新 snapshot”。
- **任务步骤**：
  1. 拉取 `manual_reviews` 中 `pending` 且 `news_summaries` 为 `ready_for_export` 的文章（按 report_type 分桶）。
  2. 若新文章标题无 embedding，则写入 `manual_title_embeddings`。
  3. 对每个 bucket 使用 `cluster_titles` 聚类（可分批/分区并行）。
  4. 写入 `manual_clusters`，关联新的 `snapshot_id`。
  5. 更新 `manual_cluster_snapshots`。

### 4) API 与前端改造
- `GET /api/manual_filter/candidates` 改为读取 `manual_clusters`（按 bucket + snapshot_id 过滤，读时 join 详情）。
- 加入 `snapshot_id` 作为返回字段，用于前端判定是否刷新。
- 前端刷新：
  - 定时轮询最新 `snapshot_id`，变化则刷新列表。
  - 手动刷新按钮调用“触发聚类”接口。

### 5) 性能与扩展
- 规模 < 1 万：pgvector 内部检索 + CPU 聚类足够。
- 规模上升：
  - 向量检索使用 `hnsw` 索引加速。
  - 聚类任务拆分为多进程（或 Celery/worker）。
  - 可引入 Redis 作为聚类结果缓存层（非必须）。

### 6) 风险与应对
- **向量维度变更**：更换模型需重建向量表。
- **聚类延迟**：准实时存在分钟级延迟，通过手动刷新弥补。
- **数据一致性**：批次写入使用 `snapshot_id` 保证读写隔离。
