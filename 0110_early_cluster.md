# 一、核心痛点 (Core Pain Points)

- **性能问题 (Performance)**: 每次打开控制台或审阅完一页新闻后，系统都需要"现场"重新进行聚类 (Clustering)。这个过程非常缓慢，严重影响体验。
- **并发与阻塞 (Concurrency & Blocking)**:
  - 系统目前是单线程/阻塞式的。
  - 在进行查询或其他耗时操作时，整个控制台卡死，无法进行任何其他操作。
  - 缺乏多用户支持，无法多人同时审阅或查看实时更新。

# 二、现状分析

经过对代码的阅读 (`src/console/manual_filter_cluster.py`, `src/adapters/title_cluster.py`, `src/adapters/db_postgres.py`)，发现以下技术瓶颈：

## 1. 聚类机制 (Clustering Mechanism)

- **现状**: 目前 `cluster_pending` 函数会在**每次请求**时：
  1. 从数据库拉取最多 5000 条待处理新闻。
  2. 在内存中加载 BGE (Transformer) 模型。
  3. 计算所有标题的 Embedding 并计算相似度矩阵 (CPU 密集型)。
  4. 进行聚类并分页返回。
- **问题**: 这是一个典型的 **CPU 密集型 (CPU-bound)** 任务。随着数据量增加，计算时间呈指数级或线性增长。在 Web 请求的主线程中执行此操作会直接阻塞服务器，导致页面响应极慢。

## 2. 数据库与线程模型 (DB & Threading)

- **现状**: `run_console.py` 使用 `uvicorn` 单进程运行。数据库适配器 `src/adapters/db_postgres.py` 使用的是 `psycopg` 的**同步 (Synchronous)** 连接模式。
- **问题**:
  - 由于数据库操作是同步的，任何慢查询都会阻塞 Worker 线程。
  - 结合 CPU 密集的聚类操作，这导致了"单线程卡死"现象。一个用户的操作（如查询）会阻塞事件循环，导致其他请求（甚至同一用户的后续请求）无法被处理。

# 三、具体方案：pgvector + 准实时聚类

### 1) 总体思路

- 保留 BGE 向量模型，向量落库（pgvector），聚类结果预计算。
- 前端读取“已聚类结果表”，避免请求内现场聚类。
- 准实时：后台任务周期性/触发式更新（例如 5 分钟一次）+ 手动刷新兜底。

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



> 修改思路：将三张表精简为 **1.5 张**（一张实表，一张扩展表）：
>
> #### **建议 A：取消“快照表 (Snapshots)”，改为字段**
>
> - **理由**：快照表的目的是为了标记“这一批聚类是什么时候生成的”。我们完全可以把这个信息直接写在 `manual_clusters` 表的 `created_at` 字段里。
> - **做法**：每次重新聚类时，删除该报型旧的记录，直接插入新的。查询时只查最新的记录即可。这样就**砍掉了 (C) 表**。
>
> #### **建议 B：取消“向量表 (Embeddings)”，合并入业务表**
>
> - **理由**：没必要为向量单独开一张表。
> - **做法**：直接在manual_reviews里增加一个 `title_embedding` 字段。
> - **好处**：查询时不需要 Join，一行代码就能拿到文章和它的向量，管理起来最简单。
>
> ------
>
> ### 最简方案：只需要增加一张表
>
> 如果你想通过“后台预聚类”来解决卡顿问题，**唯一必须新建**的表只有一张：
>
> #### **精简后的 `manual_clusters` (聚类结果表)**
>
> 这张表的作用是充当“缓存”，让前端不用现场计算。
>
> | **字段**                 | **类型**    | **说明**                                 |
> | ------------------------ | ----------- | ---------------------------------------- |
> | **cluster_id**           | text (PK)   | 聚类 ID。                                |
> | **report_type**          | text        | 标识是“总报”还是“晚报”。                 |
> | **representative_title** | text        | 这一组新闻的代表性标题（用于前端显示）。 |
> | **item_ids**             | text[]      | 这一组包含的所有文章 ID 列表。           |
> | **created_at**           | timestamptz | 记录这是什么时候生成的。                 |



#### (D) Snapshot 说明与保留建议

- **含义**：snapshot 表示一次“全量聚类结果”的批次 ID，用来保证读写隔离与一致性。
- **现状**：当前系统没有持久化 snapshot。
- **建议**：新增 `manual_cluster_snapshots` 并按 `report_type` 保留最近 3 个；前端只读取对应报型最新 snapshot。

### 3) 后台任务（准实时刷新）

- **触发策略**：
  - 定时任务由业务侧自行配置（例如 5 分钟），此处仅提供启动脚本。
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

### 5) 风险与应对

- **向量维度变更**：更换模型需重建向量表。
- **聚类延迟**：准实时存在分钟级延迟，通过手动刷新弥补。
- **数据一致性**：批次写入使用 `snapshot_id` 保证读写隔离。