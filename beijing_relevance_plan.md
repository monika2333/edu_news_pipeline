# Beijing Relevance Tagging Plan

## 背景与目标
- 现有 `news_summaries` 仅保存 `correlation` 分数，导出结果也只按分数排序。
- 业务希望新增“是否与北京相关”的标签，依据正文关键词匹配判断，并在导出时按“京内 / 京外”分组后保持分组内按分数降序展示。

## 总体方案
- 在数据库中新增布尔列 `is_beijing_related`，`NULL` 表示尚未判定。
- 摘要生成完成后即刻判定北京相关性并写回该列，使新增数据自动带标签。
- 导出时按标签拆分输出并记录历史，同时在 Feishu 通知中展示分组数量。
- 通过 CLI 子命令支持历史数据回填。

## 详细改动

### 1. 数据库 schema ✅
- 在 `database/schema.sql` 中新增 `is_beijing_related boolean`，并写入注释说明 `NULL` 代表未判定。
- 当前不添加索引；如未来存在高频查询再考虑部分索引。
- 部署流程：本地可直接应用 `schema.sql`；线上通过独立迁移脚本执行。

### 2. 关键词配置 ✅
- 在 `data/beijing_keywords.txt` 维护北京及环京关键词（北京、首都、延庆、廊坊等），后续调整直接更新该文件。
- `Settings` 新增 `beijing_keywords_path` 配置项，默认指向上述文件，可用环境变量覆盖。

### 3. 领域判定工具 ✅
- 新建 `src/domain/region.py`：
  - `load_beijing_keywords(path)` 读取文本、去重并统一小写。
  - `is_beijing_related(texts, keywords)` 对正文、摘要、关键词多来源文本做大小写无关的子串匹配。
- 在 `src/domain/__init__.py` 暴露上述方法供其他模块复用。

### 4. 摘要写入流程 ✅
- `PostgresAdapter.complete_summary` 支持 `beijing_related` 可选参数，写入 `is_beijing_related` 列。
- `src/workers/summarize.py`：
  - 加载北京关键词，在生成摘要后构造检测文本（正文、摘要、关键词）。
  - 调用 `is_beijing_related`，将结果传给 `complete_summary`；无法判定时保持 `NULL`。

### 5. 导出与历史记录 ✅
- `ExportCandidate` 增加 `is_beijing_related` 字段，对应查询 `fetch_export_candidates` 同步返回布尔值。
- `src/workers/export_brief.py`：
  - 根据标签拆分候选为“京内 / 京外”列表，保持原有相关性排序。
  - 输出文本形如：
    ```
    【京内】共 X 条
    标题
    摘要（来源）

    【京外】共 Y 条
    ...
    ```
  - `export_payload` 中记录 section (`"jingnei"` / `"jingwai"`)，`notify_export_summary` 传递 `category_counts={"京内": X, "京外": Y}`。
- `record_export` 的 metadata 加入 `is_beijing_related` 便于后续审计。

### 6. CLI 与回填任务 ✅
- 在 `src/cli/main.py` 新增 `geo-tag` 子命令：
  - 分批查询 `is_beijing_related IS NULL` 的摘要记录（默认 200 条）。
  - 利用统一检测函数判定后批量更新。
  - 运行结束输出处理数量及真/假计数，可重复执行以覆盖历史或关键词调整后的数据。
- 新 worker `src/workers/geo_tag.py` 实现上述逻辑，复用领域工具和配置。

### 7. 测试计划 ✅
- `tests/test_db_postgres_adapter.py` 补充：
  - `complete_summary` 写入 `is_beijing_related`；
  - `fetch_export_candidates` 返回布尔值；
  - 导出记录 metadata 包含新字段。
- 为 `src/domain/region.py` 编写单测，覆盖关键词命中与否、大小写、空文本等场景。
- `geo-tag` worker 编写测试或集成验证，确认分页更新与日志统计正确。
- `export_brief` 分组输出添加覆盖，确保“京内 / 京外”段落与 Feishu 统计一致。

### 8. 部署与验证步骤
1. 执行数据库迁移，确认 `news_summaries.is_beijing_related` 存在。
2. 部署代码与关键词文件，验证配置读取路径正确。
3. 运行 `edu-news geo-tag` 回填历史数据，抽样检查判定结果。
4. 执行 `edu-news export`，确认输出文件及 Feishu 通知按“京内 / 京外”分组且排序正确。
5. 在 README 或运营文档说明关键词文件位置与 `geo-tag` 命令的使用方式。

## 后续关注
- 关键词调整后需重新执行 `geo-tag` 以刷新历史数据。
- 若未来需要扩展更多地域标签，可在 `region.py` 里拓展判定结构。
