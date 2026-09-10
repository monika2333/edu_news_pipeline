# Adapter Agent 指南

本目录是流水线和外部系统之间的边界，主要包含 PostgreSQL、新闻网站、LLM API、通知服务，以及少量面向外部系统的辅助代码。

当前爬虫 adapter 中存在历史遗留、重复实现和不同来源代码形态不一致的问题。不要把所有现有爬虫写法都当作推荐模式。处理这部分代码时，先保持现有行为，再有计划地收敛和规整。

## 期望方向

- 每个外部新闻源尽量保留一个 canonical adapter。
- `http_*.py` source adapter 负责 HTTP 抓取、来源专属解析、文章 ID 构造和入库行数据整理。
- 数据库访问统一通过 `db_postgres_core.PostgresAdapter` 对外暴露；单表读写由对应 `db_postgres_*.py` 中的显式命名空间类提供，跨表审计事务和连接层原语保留在 `PostgresAdapter` 顶层。
- LLM 请求、响应解析和接口兼容逻辑放在 `llm_*.py` 或现有模型 adapter 模块中。
- 业务决策应放在 `src/domain`、`src/workers` 或 `src/console` service 中，不要塞进 adapter。
- 新增 adapter 代码时，优先使用 dataclass、TypedDict 或其他结构化返回值，避免继续扩散临时 tuple。

## 爬虫重构规则

- `src/workers/crawl_sources.py` 是当前爬虫编排入口。source adapter 默认不应直接写数据库，除非现有行为已经如此且本次重构明确要兼容。
- 删除或替换某个爬虫实现前，必须检查 `src/workers`、`src/cli`、`scripts` 和 `tests` 中的引用。
- 替换重复解析或抓取逻辑前，先为对应 source adapter 添加或更新测试。
- 保持各来源的 `article_id` 稳定。已有 ID 可能已经进入数据库，并被后续流水线步骤引用。
- 如果某个来源现有逻辑区分 feed/detail 两阶段，应保留这个语义：feed rows 写入原始文章元数据，detail rows 补齐正文内容。
- 如果同一个网站存在多个可运行实现，删除前先在代码或测试中明确标记废弃路径。
- 不要在没有来源专属原因的情况下扩大抓取范围、修改限速策略，或引入浏览器自动化。

## 数据库 Adapter 规则

- 应用代码默认通过 `src.adapters.db_postgres_core.get_adapter()` 获取数据库 adapter。
- 单表读写使用命名空间形式，例如 `adapter.manual_reviews.fetch(...)`；跨表且写审计日志的事务操作使用 adapter 顶层形式，例如 `adapter.update_manual_review_statuses_as_user(...)`。
- 新增单表读写方法时，将它加入对应 `db_postgres_*.py` 的命名空间类，不要加回 `db_postgres_core.py`。
- 命名空间类显式持有 adapter 引用；不要使用 mixin、多重继承、`__getattr__` 或其他动态转发。
- 多个相关写入必须一起成功或失败时，应明确使用事务边界。
- SQL 必须使用 psycopg 参数化查询；不要把用户输入拼接进 SQL 字符串。
- 行数据标准化 helper 应靠近拥有该写入路径的 adapter 模块。
- 不要把控制台专用的展示格式放进数据库 adapter 方法。

### 已拆分的 adapter 包：函数该放哪个文件

`db_postgres_submission_archive` 与 `db_postgres_manual_reviews` 已从单文件改为同名 package。对外导入路径、命名空间类和 `__all__` 与拆分前完全一致，调用方无需感知。新增函数时按下表落位，不要再往 `__init__.py` 里写实现。

`db_postgres_submission_archive`（报送单、条目、回链与查重）：

| 文件 | 承载内容 |
|---|---|
| `__init__.py` | 门面：`SubmissionArchiveNamespace` 与其显式再导出，无实现 |
| `_base.py` | 共享底座：三个结果 TypedDict、`PRIOR_MATCH_REPORT_TYPES`、`_ITEM_PUBLIC_COLUMNS` |
| `reports.py` | 报送单本身与导出：增删、列表、按来源消息幂等查找、导出取数与计数、`prior_match_completed` 标记 |
| `links.py` | 条目与新闻的回链：候选标题/正文、待处理列表、自动与人工的匹配和解除匹配 |
| `items.py` | 条目编辑与检索：字段改写、关键词检索、embedding 的补算与写入 |
| `dedup.py` | 两级查重：条目级 prior-match（`submission_item_duplicate_matches`，`fetch_item_*` 一族）与新闻级去重（`submission_duplicate_matches`，`*_duplicate_*` 一族） |

`dedup.py` 把两级查重放在一起是有意的：两者共用「相似度 + 匹配方式 + 人工判定」这套语义，改动其一时通常要同时确认另一处；分到两个文件会让这条关联重新变成跨文件隐式约定。

`db_postgres_manual_reviews`（人工审阅队列、聚类缓存与值班导入）：

| 文件 | 承载内容 |
|---|---|
| `__init__.py` | 门面：`ManualReviewsNamespace` 与其显式再导出，无实现 |
| `_base.py` | 共享底座：SQL 片段常量（`SEARCH_TEXT_EXPRESSION` 等）、`ManualReviewConflictError`、`MANUAL_REVIEW_DECISION_LOCK_ID`、`report_type_expr`、`_build_manual_review_filters`、`manual_review_max_rank` |
| `_queries.py` | 队列读取与排序：入队、`fetch_manual_reviews`、聚簇取待处理、审阅桶取行 |
| `_filters.py` | 候选筛选与批量丢弃：候选筛选器构造、按日期计数与 `FOR UPDATE` 取行、按日期批量丢弃 |
| `_clusters.py` | `manual_clusters` 整表重建与读取，以及 advisory lock 原语 |
| `_counts.py` | 状态计数与待处理数（只读聚合） |
| `_writes.py` | 无版本号写入：状态、重置待处理、摘要编辑 |
| `_imports.py` | 值班结果导入人工审阅：预览与导入 |
| `_versions.py` | 版本化写入：取行、版本校验、决定位次分配、带版本的状态/摘要/排序更新 |

`manual_review_max_rank` 被 `_imports.py` 与 `_versions.py` 共同使用，所以它放在 `_base.py` 而不是定义处的 `_counts.py`；`_counts.py` 反过来从 `_base.py` import 它。

`_base.py` 是共享底座，同包其他模块只能从它 import 共享常量与 helper。`_queries.py`、`_filters.py`、`_clusters.py`、`_counts.py`、`_writes.py`、`_imports.py`、`_versions.py` 之间不要互相 import：拆分的目的是让改动范围可以按文件界定，横向依赖会把这个性质抵消掉。新增的跨文件共享内容先放进 `_base.py`，不要从 `_filters.py` 之类的地方反向借用。

`db_postgres_submission_archive` 有两条同包依赖是有意保留的，新增代码不要照此扩散：

- `reports.py` import `dedup.py` 的 `fetch_item_duplicate_match_summaries`。这是拆分前就存在的唯一一处跨组调用（`fetch_report` 要给出条目的 prior_match 摘要），按普通模块 import 处理，没有为了消除它而搬动函数。
- `reports.py` 通过 `import ... as _facade` 读取 `PRIOR_MATCH_REPORT_TYPES`。拆分前它是本模块全局名，`fetch_report` 在调用时从模块命名空间取值，`tests/test_db_postgres_submission_archive.py` 正是对这个公开名字打 monkeypatch；直接 `from ... import` 会把取值冻结在导入时刻，静默让该 patch 失效。取值语义与拆分前一致，定义仍在 `_base.py`。

`db_postgres_shift_reviews` 依赖 `_base.py` 的 `SEARCH_TEXT_EXPRESSION`、`CREATED_LOCAL_DATE_EXPRESSION`、`SCORE_FEEDBACK_JOIN`、`_build_manual_review_filters`，以及 `_filters.py` 的 `_build_manual_candidate_filters`。这些名字仍从 `db_postgres_manual_reviews` 包对外暴露，重命名或移动前先查引用。

## LLM 和外部 API 规则

- prompt 构造和响应解析必须能在不调用真实 API 的情况下测试。
- provider 配置通过 `src.config.get_settings()` 读取。
- 不要在 adapter 代码中硬编码模型名、API key、referer 或 endpoint secret。
- 新增 LLM 行为时，应覆盖畸形响应、空响应和边界响应的测试。

## 建议测试

- HTTP source 变更：`python -m pytest tests/test_http_gmw_adapter.py tests/adapters/test_http_laodongwubao.py tests/adapters/test_http_toutiao.py tests/adapters/test_http_linked_page_rows.py tests/adapters/test_http_chinanews_xj.py tests/adapters/test_http_btime.py tests/adapters/test_http_beijinghao.py`
- 数据库 adapter 变更：`python -m pytest tests/test_db_postgres_adapter.py tests/test_db_postgres_manual_reviews.py`
- LLM adapter 变更：`python -m pytest tests/test_llm_beijing_gate.py tests/test_external_filter_model.py tests/test_sentiment_prompt.py`
- 爬虫流程变更：`python -m pytest tests/test_crawl_gmw_flow.py`

