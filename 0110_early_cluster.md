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

# 三、具体方案：pgvector + 极简准实时聚类

### 1) 总体思路

- **向量内置 (Embedding In-place)**：不再维护独立的向量表，直接在 `manual_reviews` 表中扩展 `title_embedding` 字段，简化管理并避免 Join。
- **预聚类结果表 (Results Cache)**：引入一张 `manual_clusters` 表作为“结果缓存”，后台任务定期计算聚类并写入，前端直接读取该表以实现“秒开”。
- **准实时更新**：后台任务周期性触发（如每 5 分钟），计算并全量替换 `manual_clusters` 记录。利用 `created_at` 标识批次，取消复杂的快照管理表。

### 2) 数据库设计（1.5 表方案）

#### (A) 业务表扩展 `manual_reviews`
在现有的 `manual_reviews` 表中增加向量字段：
- **字段**：`title_embedding vector(1024)` (适配 BGE-large-zh 维度 1024)。
- **索引**：建议使用 `hnsw` 索引：
  ```sql
  CREATE INDEX ON manual_reviews USING hnsw (title_embedding vector_l2_ops);
  ```

#### (B) 聚类结果表 `manual_clusters` (新增)
作为前端读取的“缓存”表。
| **字段**                 | **类型**    | **说明**                                 |
| ------------------------ | ----------- | ---------------------------------------- |
| **cluster_id**           | text (PK)   | 聚类 ID（如 UUID 或 Hash）。             |
| **report_type**          | text        | 标识是“总报”还是“晚报”。                 |
| **bucket_key**           | text        | 分桶标识（如 internal_positive）。       |
| **representative_title** | text        | 这一组新闻的代表性标题。                 |
| **item_ids**             | text[]      | 这一组包含的所有文章 ID 列表。           |
| **size**                 | integer     | 这一组包含的文章数量。                   |
| **created_at**           | timestamptz | 生成时间（用于前端判断数据刷新时间）。   |

---

### 3) 核心处理流程

1. **向量化 (In-flow/Async)**：
   - 建议在文章进入 `manual_reviews` 或进行摘要生成后，计算其标题的 Embedding 并更新到 `title_embedding` 字段。
2. **后台聚类任务 (Background Job)**：
   - **频率**：周期性（如每 5 分钟）或由管理后台手动触发。
   - **步骤**：
     1. 从 `manual_reviews` 拉取 `pending` 状态且已有向量的数据。
     2. 按 `report_type` + `bucket_key` 进行分组聚类计算。
     3. **全量更新缓存**：在一个事务中，删除旧的 `manual_clusters` 记录并写入新批次结果。
3. **前端渲染 (Frontend)**：
   - 前端切换标签页或打开控制台时，直接 `SELECT * FROM manual_clusters WHERE report_type = ...`。
   - 拿到聚类列表后，再根据 `item_ids` 拉取文章详情（或通过 Join 一次性获取）。

### 4) API 改造建议

- `GET /api/manual_filter/clusters`: 返回预计算好的聚类列表。
- `POST /api/manual_filter/trigger_clustering`: (选做) 允许手动触发后台任务，立即刷新缓存。

### 5) 优势与风险

- **优势**：
  - **极简架构**：仅需一张结果表和现有表的一个字段，维护成本极低。
  - **秒级响应**：前端查询直接命中缓存表，无需在 Web 请求内进行 CPU 密集计算。
- **风险与应对**：
  - **实时性**：会有分钟级延迟。*应对*：前端显示“最后更新时间”，并提供手动刷新按钮。
  - **并发冲突**：全量更新时可能存在短暂的读取空窗。*应对*：使用数据库事务 (Transaction) 确保更新过程原子化。