# 京内二次判定与外部过滤联动计划

## 背景
- 目前 `summarize` worker 主要依赖关键词命中来推断 `is_beijing_related`，误判的稿件会直接进入「京内 → 直接出稿」链路。
- 希望在进入原有外部重要性模型之前，引入一次 LLM 复核流程，把与北京无关的稿件重新归类到「京外」链路。
- 目标是在保持既有「京外正面」评分流程不变的前提下，提升「京内」稿件的准确性与可追踪性。

## 当前流程速览
- `src/workers/summarize.py`：摘要写入 `news_summaries` 后，根据 `is_beijing_related` 与 `sentiment_label` 决定后续状态：
  - `True` → `status=ready_for_export`（直接进入内宣）。
  - 非真且情感为正 → `status=pending_external_filter`。
- `src/workers/external_filter.py`：仅消费 `pending_external_filter`，调用打分模型判定是否晋级。
- `news_summaries` 目前只有一个 `is_beijing_related` 字段，无法区分关键词判定与未来的 LLM 复核结果。

## 目标方案概述
1. 新增状态（暂定 `pending_beijing_gate`）承接原本直接晋级的「京内」稿件。
2. `external_filter` worker 在执行既有流程前，优先消费 `pending_beijing_gate` 队列：
   - 调用 LLM Prompt 判断是否真正与北京相关。
   - 存储 LLM 判定及原始响应，统计耗时。
   - 判定为非北京 → 更新 `is_beijing_related=False`，状态切换至 `pending_external_filter`，继续沿用原外部打分模型。
   - 判定为北京 → 保持 `ready_for_export`，并记录 LLM 判定结果。
3. LLM 调用异常时采用保守策略（默认依旧视为北京相关），避免丢失真实京内稿件。
4. 原 `pending_external_filter` → 打分 → `ready_for_export` / `external_filtered` 链路保持不变。

## 开发任务拆解

### 1. 数据结构与状态流
- `database/schema.sql` / 迁移脚本：
  - 增加状态常量 `pending_beijing_gate`。
  - 新增字段：`is_beijing_related_llm`、`beijing_gate_checked_at`、`beijing_gate_raw`、`beijing_gate_attempted_at`、`beijing_gate_fail_count`。
  - 根据需要补充索引与列注释，兼容历史数据（默认 NULL）。
- `src/domain/external_filter.py`：
  - 定义新的 `BeijingGateCandidate` dataclass，补充 LLM 判定字段。
  - 扩展 `ExternalFilterCandidate`，增加 `is_beijing_related_llm` 等字段，以便后续流程可读取。
- `src/adapters/db_postgres.py`：
  - 新增 `fetch_beijing_gate_candidates`、`complete_beijing_gate`、`mark_beijing_gate_failure`。
  - 调整状态过滤与更新逻辑，避免与现有 `status` / `external_importance_status` 冲突。
  - 更新 `fetch_external_filter_candidates`，忽略仍在 LLM 复核阶段的记录。

### 2. LLM 判定服务封装
- 新建 `src/adapters/llm_beijing_gate.py`（或在 `src/services/` 下）：
  - 参考 `external_filter_model` 的结构，提供 Prompt 构建、请求发送、响应解析、一致的重试与超时策略。
  - 输出结构化结果：`is_related`、`confidence`（可选）、`raw_text`。
- 在 `docs/` 下补充 `beijing_gate_prompt.md`，记录 Prompt 内容、输入字段说明、版本号管理。

### 3. Worker 调整
- `summarize` worker：
  - 将原来直接进入 `ready_for_export` 的京内稿件改写为 `pending_beijing_gate`。
  - 初始化新增字段（如 `beijing_gate_*`、`is_beijing_related_llm` 置空）。
  - 日志记录关键词命中情况，便于对比 LLM 复核结果。
- `external_filter` worker：
  - 启动后先批量拉取 `pending_beijing_gate`，使用线程池调用 LLM 判定。
  - 根据 LLM 结果进行状态迁移与字段更新，并记录日志/统计指标。
  - 处理失败或超时时增加失败计数，达到阈值后可降级为直接放行或人工介入。
  - 完成 LLM 阶段后继续原有的 `pending_external_filter` 打分逻辑。
  - 需保证幂等：重复执行不会导致状态震荡或数据丢失。

### 4. 配置与环境
- `src/config.py`：新增 LLM 判定相关配置项（模型名、超时、是否启用思考模式、最大重试次数等）。
- `.env.local`：补充对应环境变量示例，注明默认值与可选范围。
- 若沿用同一 API Key，注意速率限制与费用评估；如需独立 provider，需新增配置入口。

### 5. 监控与日志
- 扩展 `log_summary` 或在 worker 内打印关键指标：
  - `beijing_gate.total`、`beijing_gate.confirmed`、`beijing_gate.rerouted`、`beijing_gate.failures`。
  - LLM 调用耗时、重试次数、失败原因。
- 若有 metrics/埋点系统，追加字段上报；同时在运维文档中记录排查流程。

### 6. 测试计划
- 单元测试：
  - Mock LLM 响应，覆盖判定为北京/非北京/不确定及失败重试路径。
  - `db_postgres` 适配器对新增字段与状态的读写。
  - LLM 响应解析函数的健壮性测试。
- 集成/回归测试：
  - 构建 `pending_beijing_gate` → reroute → `pending_external_filter` → export 的端到端场景。
  - 并发与性能测试，验证线程池与重试策略表现。

### 7. 发布与回滚
- 发布顺序：先执行数据库迁移 → 回填历史数据至 `pending_beijing_gate`（若需要）→ 发布代码 → 灰度开启 LLM 判定。
- 若提供 Feature Flag，可通过环境变量控制新流程，便于快速回滚。
- 上线后重点观察：
  - 「京内」误报率变化（人工抽样）。
  - LLM 调用成本与延迟。
  - 「京外正面」进入外部过滤的增量情况。
- 回滚策略：关闭 LLM 判定开关后，系统应回退至原有流程且不会积压。

## 风险与待确认事项
- LLM 成本与延迟，需确认预算与速率限制，以及失败后的补救策略。
- Prompt 设计是否覆盖教育类新闻的语境，需要与业务侧 Review。
- 回流至 `pending_external_filter` 后，是否需要结合 LLM 结果调整权重或排序。
- 历史数据如何回填进入新状态，是否全部重跑或仅针对特定时间窗口。
- 长时间失败或网络异常的处理策略，避免记录永久卡在待判定状态。

## 验收标准
- 代码层面：新增字段、配置与 worker 改动均有单测/集成测试覆盖，并通过 CI。
- 数据层面：上线后一周 `beijing_gate.rerouted_ratio` 明显大于 0，且抽样显示误报下降。
- 运维层面：日志与指标可追踪任意稿件的判定链路（关键词 → LLM → 外部过滤）。
- 回滚层面：关闭 LLM 判定配置后流程自动恢复，不产生额外积压与错误。

## 实施 Checklist
- [x] 编写数据库迁移：新增 pending_beijing_gate 状态及 beijing_gate_* 字段，并更新索引和注释。
- [x] 更新 src/domain/external_filter.py dataclass，增加 LLM 判定字段及新的候选类型。
- [x] 在 src/adapters/db_postgres.py 中实现 fetch_beijing_gate_candidates、complete_beijing_gate、mark_beijing_gate_failure，并调整现有查询与状态写入逻辑。
- [x] 新建 src/adapters/llm_beijing_gate.py（或等效服务层），封装 prompt、请求与响应解析；补充 docs/beijing_gate_prompt.md。
- [x] 修改 src/workers/summarize.py：输出“京内”稿件时切换到 pending_beijing_gate 状态并初始化字段。
- [x] 扩展 src/workers/external_filter.py：插入 LLM 判定环节，处理通过/回流/失败逻辑与日志统计。
- [x] 扩展 src/config.py 与 .env.local 示例，纳入新的模型配置、超时和重试参数。
- [ ] 增补测试：数据库适配器、LLM 解析、worker 流程（mock LLM）及端到端路径。
- [ ] 更新运维/监控文档，记录新指标与回滚/重试策略；规划上线与回填步骤。
