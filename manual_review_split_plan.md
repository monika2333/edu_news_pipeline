# manual_reviews 拆表方案

面向目标：把人工筛选相关字段从 `news_summaries` 拆到独立表，避免稀疏列和耦合，让只有需要人工处理的文章才占用手工字段，便于后续扩展。

## 现状速记
- 当前手工字段（`manual_status/manual_summary/manual_rank/manual_decided_*` 等）混在 `news_summaries`，绝大部分记录为空；`manual_rank` 仅由 `manual_filter.py` 动态添加，`database/schema.sql` 未显式声明。
- 控制台逻辑集中在 `src/console/services/manual_filter.py`、调用适配器 `src/adapters/db_postgres.py`；`complete_external_filter` 等函数会把失败样本标记为 `manual_status='discarded'`。
- 队列查询依赖条件：`status = 'ready_for_export' AND manual_status = 'pending'`。

## 目标数据模型
- 新表 `manual_reviews`（仅对需要人工的文章建行）：
  ```sql
  create table if not exists manual_reviews (
      id uuid primary key default gen_random_uuid(),
      article_id text not null references news_summaries(article_id) on delete cascade,
      status text not null check (status in ('pending','selected','backup','discarded','exported')),
      summary text,
      rank double precision,
      notes text,
      score numeric(6,3),
      decided_by text,
      decided_at timestamptz,
      created_at timestamptz not null default now(),
      updated_at timestamptz not null default now(),
      unique (article_id)
  );
  create index if not exists manual_reviews_pending_idx
      on manual_reviews (status, rank asc nulls last, article_id)
      where status = 'pending';
  create index if not exists manual_reviews_status_idx
      on manual_reviews (status);
  ```

## 流程调整（设计稿）
- 入队：当文章通过外部过滤进入 `ready_for_export`（当前在 `db_postgres.complete_external_filter`），如果需要人工审阅则 `INSERT ... ON CONFLICT DO NOTHING` 到 `manual_reviews`（`status='pending'`，可同时带初始 `rank`）。只要未进入人工流程则不建行。
- 队列读取 / 聚类：`manual_filter.list_candidates/cluster_pending` 改为从 `manual_reviews` 取 `status='pending'` 并 JOIN `news_summaries` 获取标题、情感、地域等，再按现有排序规则工作。
- 批量决策 / 排序：`bulk_decide`、`update_ranks`、`reset_to_pending` 等改写为更新 `manual_reviews`，并维护 `rank/decided_by/decided_at`。导出/展示时直接使用 `manual_reviews.summary` 与主表 JOIN，不再回写旧列。
- 导出：`export_batch` 改为从 `manual_reviews` 取 `status='selected'`（JOIN 主表拉取文章信息）。导出后将 `manual_reviews.status` 更新为 `exported`。
- 自动丢弃：`complete_external_filter` / `mark_external_filter_failure` 等不再改 `news_summaries.manual_status`，而是若存在排队行则更新为 `discarded`；未入队则无需建行。
## 迁移步骤（零停机串行）
1) **准备**：确认 `pgcrypto`/`uuid-ossp` 可用（`gen_random_uuid`），备份 `news_summaries` 手工列（导出或快照）。
2) **建表/索引**：执行“目标数据模型”中的 DDL（不改旧表，安全可回滚）。
3) **回填数据**（仅迁移需要人工的信息，避免把整表默认 `pending` 全搬过来）：
   ```sql
   insert into manual_reviews (article_id, status, summary, rank, notes, score, decided_by, decided_at, created_at, updated_at)
   select ns.article_id, ns.manual_status, ns.manual_summary, ns.manual_rank, ns.manual_notes,
          ns.manual_score, ns.manual_decided_by, ns.manual_decided_at,
          coalesce(ns.created_at, now()), coalesce(ns.updated_at, now())
   from news_summaries ns
   where (ns.manual_status in ('selected','backup','discarded','exported'))
      or (ns.manual_status = 'pending' and ns.status = 'ready_for_export')
      or (ns.manual_summary is not null);
   ```
   - 如存在重复 `article_id`，先 `DELETE`/`DISTINCT ON` 解决冲突。
4) **代码切换**：
   - `src/console/services/manual_filter.py`：移除 `_ensure_manual_filter_schema`，全部 SQL 改为操作 `manual_reviews` JOIN `news_summaries`。
   - `src/adapters/db_postgres.py`：新增 `enqueue_manual_review`、`update_manual_review_statuses`、`fetch_manual_pending`、`update_manual_review_summaries` 等方法；`complete_external_filter` 等调用新方法；导出逻辑改用 JOIN。
   - `dashboard.py` 及相关路由调用新接口。
   - 更新测试 `tests/test_manual_filter_service.py`（构造 `manual_reviews` 假数据/adapter）。
5) **观测与比对**：对比拆表前后的 pending 数量、已选/已弃数量、导出结果（同样的输入批次跑一遍 dry-run 导出校验文本一致）。
6) **清理**（确认无消费旧列后）：
   - 删除 `news_summaries` 上的 `manual_*` 列和索引。
   - 更新 `database/schema.sql` 与迁移文件，去掉手工列，新增新表 DDL。

## 回滚思路
- 代码未切换：直接 `DROP TABLE manual_reviews CASCADE`（或保留快照），旧表不受影响。
- 代码已切换：保留 `manual_reviews` 数据，把关键列写回 `news_summaries` 后删除新表；恢复旧版本代码。

## 测试清单
- 单元：`manual_filter` 列表/聚类/批量决策/导出路径在新表下的行为；`db_postgres` 新增方法的行数返回和约束。
- 集成/回归：跑一小批真实数据（含 external filter 失败与成功案例），确认 pending 队列、决策后状态、导出文本与旧版一致。
- 迁移校验：回填后行数 ≈ 预期（`ready_for_export` pending 数 + 已经人工处理的行）；部分 spot-check JOIN 数据与原表手工列一致。

## 代码改动指引（落地顺序）
1) 写迁移：新增 `database/migrations/XXXX_manual_reviews.sql`（建表+索引+回填）。✅ 已完成（20251201093000_manual_reviews_split.sql）
2) 改适配器：添加新方法并替换旧的手工字段写入点，保持接口幂等/事务性。✅ 已完成（新增 manual_reviews 读写接口；外部过滤结果同步入队/丢弃）
3) 改服务层：`manual_filter` 只依赖新表；移除 `_ensure_manual_filter_schema` 的列创建逻辑。
4) 改控制台/测试：`dashboard.py`、相关路由与测试用例同步更新。
5) 清理：删除旧列，更新 `database/schema.sql`。
