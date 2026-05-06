# Adapter Agent 指南

本目录是流水线和外部系统之间的边界，主要包含 PostgreSQL、新闻网站、LLM API、通知服务，以及少量面向外部系统的辅助代码。

当前爬虫 adapter 中存在历史遗留、重复实现和不同来源代码形态不一致的问题。不要把所有现有爬虫写法都当作推荐模式。处理这部分代码时，先保持现有行为，再有计划地收敛和规整。

## 期望方向

- 每个外部新闻源尽量保留一个 canonical adapter。
- `http_*.py` source adapter 负责 HTTP 抓取、来源专属解析、文章 ID 构造和入库行数据整理。
- 数据库访问统一通过 `db_postgres_core.PostgresAdapter` 对外暴露；SQL 较重的实现放在对应的 `db_postgres_*.py` 模块中，再由 `PostgresAdapter` 包装。
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
- 多个相关写入必须一起成功或失败时，应明确使用事务边界。
- SQL 必须使用 psycopg 参数化查询；不要把用户输入拼接进 SQL 字符串。
- 行数据标准化 helper 应靠近拥有该写入路径的 adapter 模块。
- 不要把控制台专用的展示格式放进数据库 adapter 方法。

## LLM 和外部 API 规则

- prompt 构造和响应解析必须能在不调用真实 API 的情况下测试。
- provider 配置通过 `src.config.get_settings()` 读取。
- 不要在 adapter 代码中硬编码模型名、API key、referer 或 endpoint secret。
- 新增 LLM 行为时，应覆盖畸形响应、空响应和边界响应的测试。

## 建议测试

- HTTP source 变更：`python -m pytest tests/test_http_gmw_adapter.py tests/adapters/test_http_laodongwubao.py`
- 数据库 adapter 变更：`python -m pytest tests/test_db_postgres_adapter.py tests/test_db_postgres_manual_reviews.py`
- LLM adapter 变更：`python -m pytest tests/test_llm_beijing_gate.py tests/test_external_filter_model.py tests/test_sentiment_prompt.py`
- 爬虫流程变更：`python -m pytest tests/test_crawl_gmw_flow.py`

