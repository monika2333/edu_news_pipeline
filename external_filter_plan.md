# External Filter 方案

## 背景与目标
- news_summaries 当前仅依赖 score + is_beijing_related + sentiment_label 来决定导出，所有京外正面稿件都会进入 export（database/schema.sql:142, src/adapters/db_postgres.py:1148）。
- 新需求：为京外正面稿件增加一层“外部重要性”审核，只有达到阈值才进入导出，其余被拦截，且可追踪拦截原因。

## 详细设计

### 1. Schema & 状态机
1. 在 `database/schema.sql:142` 增加字段：
   - `external_importance_status` (enum: pending, pending_external_filter, ready_for_export, external_filtered)。
   - `external_importance_score` (NUMERIC)、`external_importance_reason` (TEXT)、`external_importance_checked_at` (TIMESTAMP)、`external_importance_raw` (JSONB)。
2. 为 `(is_beijing_related, sentiment_label, external_importance_status)` 建联合索引，方便 worker 批量扫描。
3. 保持 status 字段与旧值兼容：京内稿不受影响，京外正面新增两个状态；失败重试次数和 `updated_at` 仍由触发器维护。

### 2. Summarize 写库逻辑
1. 在 `src/workers/summarize.py:131` 写 `complete_summary` 前判断 `is_beijing_related is not True` 且 `sentiment_label='positive'`：
   - 将 `status` 置为 `pending_external_filter`。
   - 清空/初始化新字段：score/checked_at/reason/raw 设 `NULL`。
2. 其他记录仍写 `ready_for_export`，保证已有流程不变。
3. `src/adapters/db_postgres.py:712` 等保存接口需允许新状态并正确 upsert 新列。

### 3. External Filter Worker
1. 新 worker 位于 `src/workers/external_filter.py`（仿 `score.py:11`）：
   - 批量拉取 `pending_external_filter` 记录（按 `updated_at` 升序 + 限流）。
   - 通过 `src/adapters/llm_scoring.py` 新增 `ExternalFilterScorer`，使用专用 prompt（写在 `prompts/external_filter.md`）。
2. 模型返回结构：
   - `score` (0-100)、`reason`、`raw_json`（保留模型原始输出）。
3. 状态转换：
   - `score >= external_filter_threshold` → 写 `external_importance_score/checked_at/reason/raw` 并将 `status` 置为 `ready_for_export`。
   - 否则 `status='external_filtered'`，同样记录原因，便于后续人工复核。
4. 失败与重试：
   - 对 LLM/network/解析失败计入 `retry_count`，超过 `EXTERNAL_FILTER_MAX_RETRIES` 后标记 `status='external_filtered'` 并附上失败描述。
   - Worker 暴露 `--dry-run/--limit` 方便回填和调试。

### 4. 导出链路
1. `src/adapters/db_postgres.py:1148` 的 `fetch_export_candidates`：
   - 排除 `external_importance_status='external_filtered'` 的记录。
   - 对京外正面仅选 `status='ready_for_export'`。
   - 将 `external_importance_score/reason/checked_at` 带入 `brief_items.metadata`（`src/adapters/db_postgres.py:1323` 写入时一并保存）。
2. `src/workers/export_brief.py:199` 在日志与最终输出中展示该分值和理由，方便人工审核。

### 5. 配置与运维
1. `src/config.py:104` 新增：
   - `external_filter_model`（默认复用 scoring 模型，可切换不同 provider）。
   - `external_filter_threshold`（数值型，默认 70）。
   - `external_filter_batch_size`、`external_filter_max_retries`。
2. 在 `Settings` 文档与 `.env.example` 中补充说明，并在 README/Runbook 更新部署步骤。
3. 在 `scripts/` 目录新增 `backfill_external_filter.py`：
   - 针对历史 `ready_for_export` 且京外正面的记录，批量改写为 `pending_external_filter` 以回流 worker。
   - 支持 `--dry-run` + 分页以避免一次性锁表。

### 6. 验证与监控
1. 单测：
   - Summarize 写库状态机、Adapter 新列序列化、Worker 状态转换/错误分支。
2. 集成测试：
   - 通过 `pytest` 模拟完整 pipeline：summarize → external_filter → export。
3. 观测：
   - Worker 输出 `metrics`（成功/拒绝/失败次数、平均分），并在日志中打印 prompt 版本号。
   - 在控制台 Dashboard 中补充枚举统计（后续迭代）。

## 发布顺序
1. 合并 schema 迁移并执行（生产备份后 apply）。
2. 运行 backfill，将存量京外正面改为 `pending_external_filter`。
3. 部署更新后的 summarize worker/db adapter，确保新状态写入。
4. 部署 external_filter worker（可先低频运行）。
5. 验证若干样本，确认导出列表变化后再上线 export 调整。
6. 最后开放阈值/模型配置，记录变更日志。

## 风险 & Open Questions
- LLM 评分成本：需要评估每日京外正面数量，可能需缓存或采用更便宜模型。
- 人工复核需求：是否需要 UI 来查看 `external_filtered` 列表并手动 override？
- SLA：如果 external_filter worker 宕机，京外正面会滞留在 pending 状态，需监控告警。
- Backfill 时长：估计数据量与锁时间，必要时分批或在低峰执行。
