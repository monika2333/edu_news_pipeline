# Beijing Relevance Tagging Plan

## 背景与目标
- 现有 `news_summaries` 仅保存 `correlation` 分数，导出结果也仅按分数排序。
- 业务希望新增“是否与北京相关”的标签，依据关键词匹配正文，并在导出时按“京内 / 京外”分组后依旧按分数降序展示。

## 总体方案
- 在数据库中新增布尔列 `is_beijing_related`，`NULL` 表示尚未判定。
- 在摘要生成成功后即刻判定北京相关性并写回该列，新数据自动带标签。
- 导出时按标签拆分输出和记录历史，同时在 Feishu 通知中展示分组数量。
- 新增 CLI 子命令用于给历史数据回填标签。

## 详细改动

### 1. 数据库 schema
- `ALTER TABLE news_summaries ADD COLUMN is_beijing_related boolean;`
- 视需要补充 `COMMENT`，默认 `NULL` 即“未判定”；暂不加索引，若未来存在高频查询可再建部分索引。
- 部署流程：本地直接应用 `schema.sql`，线上通过独立迁移脚本执行。

### 2. 关键词配置
- 在 `data/beijing_keywords.txt` 维护关键词列表（北京、首都、朝阳区、延庆、廊坊等，一行一个词），该文件即为权威来源，后续调整直接修改此文件。
- 在 `src/config.py` 增加 `BEIJING_KEYWORDS_PATH` 配置项，默认指向上述文件，可通过环境变量覆盖。

### 3. 领域判定工具
- 在 `src/domain/region.py` 实现判定工具：
  - `load_beijing_keywords(path: Optional[Path]) -> Set[str]`：读取文件、去重、统一大小写。
  - `is_beijing_related(texts: Iterable[str], keywords: Set[str]) -> bool`：对正文、摘要、关键词等输入统一小写化后检测任一关键词即可为真。
- 这样保持领域逻辑集中在 `domain`，供 worker、CLI 复用。

### 4. 摘要写入流程
- `PostgresAdapter.complete_summary` 新增可选参数 `beijing_related: Optional[bool]`，写入 `is_beijing_related`。
- `src/workers/summarize.run` 内部：
  - 在拿到正文后调用 `is_beijing_related`，得到布尔值。
  - 调用 `complete_summary(..., beijing_related=value)`；无法判定时传 `None` 保留 `NULL`。

### 5. 导出与历史记录
- `src/domain/models.ExportCandidate` 新增 `is_beijing_related: Optional[bool]` 字段。
- `PostgresAdapter.fetch_export_candidates` 查询并填充该字段。
- `src/workers/export_brief.run`：
  - 按 `is_beijing_related` 拆分候选为京内/京外列表，内部按相关性保持原有排序。
  - 输出文本结构示例：
    ```
    【京内】共 X 条
    标题
    摘要（来源）

    【京外】共 Y 条
    ...
    ```
  - `export_payload` 的 section 使用 `"jingnei"` / `"jingwai"`，同时将 `category_counts` 设为 `{"京内": X, "京外": Y}` 传给 Feishu 通知。
- `PostgresAdapter.record_export` 中的 metadata 增加 `is_beijing_related` 字段。

### 6. CLI 与回填任务
- 在 `src/cli/main.py` 集成新的 `geo-tag` 子命令：
  - 查询 `news_summaries` 中 `is_beijing_related IS NULL` 的记录，分批（如 200 条一批）加载正文。
  - 调用 `is_beijing_related` 判定后批量更新，复用与总结流程相同的工具函数，避免重复实现。
  - 处理结束输出回填数量统计，便于验证。
- 该命令既可一次性补历史数据，也能在关键词调整或导入旧数据时再次运行。

### 7. 测试计划
- 更新 `tests/test_db_postgres_adapter.py` 覆盖：
  - `complete_summary` 写入 `is_beijing_related`；
  - `fetch_export_candidates` 返回布尔值；
  - 导出 payload 中包含新字段。
- 新增 `src/domain/region.py` 的单元测试，覆盖命中/未命中、大小写、空文本等情况。
- 为 `geo-tag` 命令添加测试或集成验证，确保分页更新与终端输出正确。
- 若调整 `export_brief`，补充针对分组输出的测试。

### 8. 部署与验证
1. 应用数据库迁移，确认 `is_beijing_related` 列存在。
2. 部署代码及新的关键词文件，检查配置项是否读取。
3. 执行 `edu-news geo-tag` 回填历史数据，抽样核对结果。
4. 跑 `edu-news export`，确认输出与 Feishu 通知按“京内 / 京外”分组且排序正常。
5. 在 README 或该方案文档中标明关键词文件位置与命令使用方式。

## 后续关注
- 关键词变更：虽然预期不频繁，但调整后需重新执行 `geo-tag` 回填逻辑。
- 判定范围扩展：若未来需要区分更多区域，可在 `region.py` 中扩展结构和配置。
