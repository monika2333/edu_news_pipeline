# 京内二次判定与外部过滤联动计划

## 背景
- 目前 `summarize` worker 仅依赖关键词命中推断 `is_beijing_related`，误判会直接进入「京内→直接出稿」链路。
- 希望在进入原始外部重要性打分前，引入大模型对「京内」结果做二次审核，把不相关的新闻回流到「京外」判定流程。
- 目标是提升「京内」精准度，并保持现有「京外正面」重要性判定/评分流程的复用与稳定性。

## 当前流程速览
- `src/workers/summarize.py`：对摘要完成后写入 `news_summaries`，按 `is_beijing_related`/`sentiment_label` 决定状态：
  - `True` → `status=ready_for_export`（直接进入内宣）
  - 非真且情感正面 → `status=pending_external_filter`
- `src/workers/external_filter.py`：仅消费 `pending_external_filter`，调用打分模型决定是否晋级。
- `news_summaries` 表仅存储单一字段 `is_beijing_related`，无法区分「关键词判定」与未来的「LLM 二次判定」结果。

## 目标方案概述
1. 引入新的等待队列（如 `pending_beijing_gate`）承接原本直接晋级的「京内」候选。
2. 在 `external_filter` worker 启动阶段优先消费该队列：
   - 调用大模型 Prompt，判断是否真正北京相关。
   - 为每条记录写入 LLM 判定结果、原始响应与耗时指标。
   - 判定为非北京：更新 `is_beijing_related`、状态切换为 `pending_external_filter`，继续沿用原打分逻辑；判定为北京：恢复到 `ready_for_export`。
3. 若 LLM 调用失败或超时：保留原判定为真，避免漏掉真实「京内」。
4. 保持原有 `pending_external_filter` 流程不变，确保正向新闻仍使用原重要性模型。

## 开发任务拆解

### 1. 数据结构 & 状态流
- `database/schema.sql` / 迁移脚本：
  - 新增状态枚举值或常量：`pending_beijing_gate`。
  - 增加字段：`is_beijing_related_llm`、`beijing_gate_checked_at`、`beijing_gate_raw`、`beijing_gate_fail_count`、`beijing_gate_attempted_at`（与现有 `external_filter_*` 字段对齐），必要时建索引。
  - 补充列注释与回填逻辑（历史数据默认 NULL）。
- `src/domain/external_filter.py` / 新 dataclass：扩展为 `BeijingGateCandidate`（LLM 判定）与调整 `ExternalFilterCandidate` 字段。
- `src/adapters/db_postgres.py`：
  - 新增 `fetch_beijing_gate_candidates()`、`complete_beijing_gate()`、`mark_beijing_gate_failure()`。
  - 梳理状态转换矩阵，避免和现有 `status`/`external_importance_status` 冲突。
  - 调整 `fetch_external_filter_candidates()` 过滤逻辑（需忽略正在二次判定的记录）。

### 2. LLM 判定服务封装
- 新建 `src/adapters/llm_beijing_gate.py`（或 `src/services/`）：
  - 与已有 `external_filter_model` 风格一致：Prompt 加载、请求重试、响应解析。
  - 输出结构化结果：`is_related: bool | None`、`reasoning`、`raw_text`。
  - 统一配置项（模型名、超时时间、最多重试、是否启用思考模式等）。
- 在 `docs/` 下新增 Prompt 说明文件（如 `docs/beijing_gate_prompt.md`），标明输入内容、输出 contract、版本管理方式。

### 3. Worker 调整
- `src/workers/summarize.py`：
  - 将原 `ready_for_export` 的「京内」记录写入新状态 `pending_beijing_gate`，并初始化 `beijing_gate` 相关字段。
  - 日志补充：记录关键词命中信息，便于后续对照 LLM 判定。
- `src/workers/external_filter.py`：
  - 启动时先批量拉取 `pending_beijing_gate`，按并发池执行 LLM 判定。
  - 根据结果：
    - `True` → 更新 `is_beijing_related=True`、`is_beijing_related_llm=True`、状态改为 `ready_for_export`。
    - `False` → 更新 `is_beijing_related=False`、`is_beijing_related_llm=False`、状态改为 `pending_external_filter`，并将 `external_filter_fail_count` 置零。
    - `None` / 异常 → 标记失败次数 + 回退策略（如失败 N 次后直接按原逻辑视为真，避免卡死）。
  - 完成上述队列后，继续原有 `pending_external_filter` 打分流程（可在同一循环内执行）。
  - 加入统计日志：判定数量、回流数量、失败数量、平均 LLM 耗时。
- 需要考虑幂等：重复执行不应导致状态震荡或无限重试。

### 4. 配置与环境
- `src/config.py`：新增配置项（模型名、超时、阈值、最大重试、是否启用思考模式）。
- `.env.local` 示例补充新变量说明。
- 若使用同一 API Key，可沿用现有 SiliconFlow 参数；否则考虑单独 provider。

### 5. 监控 & 日志
- `src/workers/__init__.py` 日志工具：扩展 `log_summary` 以记录新指标（或局部打印）。
- 在 `logs/` 或监控面板中增加统计：
  - `beijing_gate.total / rerouted / confirmed`。
  - `beijing_gate.failures`、连续失败告警阈值。
- 若有埋点/metrics 管线，追加埋点字段。

### 6. 测试计划
- 单元测试：
  - `tests/` 新增 `test_beijing_gate_worker.py`（Mock LLM，覆盖三种判定结果与失败重试）。
  - `test_db_postgres_adapter.py`：补充对新字段、新状态的读写断言。
  - LLM 解析器（Prompt 输出→布尔）的健壮性测试。
- 集成/回归：
  - 构造 end-to-end 流程：`pending_beijing_gate` → reroute → external_filter → export。
  - 压测/并发：模拟 100+ 条待判定记录，验证线程池与状态更新正确性。

### 7. 发布流程
- 迁移执行顺序：先部署 schema → 回填历史数据为 `pending_beijing_gate`（对当前 ready_for_export 且键盘判定的记录批量迁移）→ 灰度上线 worker。
- 若支持 Feature Flag，可通过环境变量开启 LLM 判定，便于快速回滚。
- 上线后一段时间对比指标：
  - 「京内」误报率变化（人工抽样）。
  - LLM 调用成本与耗时。
  - 「京外正面」进入外部过滤的增量情况。

## 风险与待确认事项
- API 成本与延迟：需要确认调用 QPS / 超时配置，避免阻塞主流程。
- Prompt 设计需覆盖教育新闻语境，必要时准备 fallback prompt 版本。
- 回流后的 `external_filter` 打分是否需要对 LLM 结果加权（目前先保持独立）。
- 历史数据回填策略：哪些记录需要重新走 LLM 判定，需要与业务侧确认。
- 失败重试策略与上限（避免因为网络问题导致大量记录卡在队列）。

## 验收标准
- 代码层面：所有新增字段、配置、worker 改动均具备单元/集成测试覆盖；CI 通过。
- 数据层面：上线后一周监控中 `beijing_gate.rerouted_ratio` 明显 > 0 且误判 case 数量降低。
- 运维层面：日志可追踪任意文章的判定链路（关键词 → LLM → 外部过滤）。
- 回滚预案：关闭 LLM 判定开关后系统自动回退至旧逻辑，无阻塞积压。
