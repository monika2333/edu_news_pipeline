# 新闻流水线重构计划（关键词驱动的最小闭环）

## 背景
- 现有流程在 `raw_articles` 中保存所有抓取原文，并对全部文章执行指纹、情感、摘要等处理，资源消耗较大。
- 实际业务只需要关注教育关键词命中的稿件；未命中关键词的稿件只需留档，无需进一步处理。
- 目标：将“关键词命中的文章”拆分到独立数据层，仅对这批数据执行去重、情感、摘要、评分和导出。

## 核心设计

### 数据层次划分
1. **raw_articles（原始留档层）**
   - 继续承载所有抓取的原文。
   - 不再在此表上执行指纹、情感等昂贵计算。
   - 字段保持现状（包括 `content_markdown` 等），供回溯和补算使用。

2. **filtered_articles（关键词命中层，新建）**
   - 仅存储命中 `education_keywords.txt` 的文章。
   - 建议字段：
     - `article_id`（PK，对应 raw_articles.article_id）
     - `keywords`（text[]，命中的关键词列表）
     - `content_markdown` / `title` / `source` / `publish_time` / `url`（摘要/评分所需字段）
     - `content_hash`、`fingerprint`（用于重复检测，命中后再计算）
     - `primary_article_id`（主文标识，主文写自身 ID，从文写主文 ID）
     - `sentiment_label`、`sentiment_confidence`
     - `inserted_at`、`updated_at`
   - 索引：`BTREE (primary_article_id)`，`BTREE (sentiment_label)`（可选），`BTREE (inserted_at DESC)`。

3. **news_summaries（摘要层）**
   - 仅对 `filtered_articles` 中的主文生成摘要；字段结构沿用现有设计。
   - 可以增加外键引用 `filtered_articles.article_id` 以确保一致性。

4. **衍生服务层**
   - `filtered_articles` 是情感、事件聚合、评分、导出的主要入口。
   - 未命中关键词的文章若在未来需要处理，可从 `raw_articles` 迁移至 `filtered_articles` 再执行后续流程。

### 流程重构
1. **采集 / 留档**
   - 爬虫仍写入 `raw_articles`。
   - 不在此层计算指纹或情感。

2. **关键词过滤任务**
   - 接收 `raw_articles` 新增/更新的正文。
   - 命中关键词时，将文章信息写入 `filtered_articles`。
   - 可记录命中的关键词列表，用于后续分析。

3. **去重与情感（仅作用于 filtered_articles）**
   - 对新入库的 `filtered_articles` 计算 `content_hash`、`fingerprint`。
   - 执行主文判定（来源优先级 + 发布时间）。
   - 仅对 `filtered_articles` 执行情感分类（小模型先判、大模型兜底）。

4. **摘要 / 评分 / 导出**
   - 以 `filtered_articles` 中 `primary_article_id = article_id` 的主文为来源写入 `news_summaries`。
   - 摘要、评分、导出流程读取 `news_summaries` + `filtered_articles` 辅助字段（情感、来源、事件信息等）。
   - 导出时按“京内/京外 × 正/负”顺序生成报告，并继续支持 Feishu 通知。

5. **回溯与重算**
   - 若需要对未命中关键词的原文进行处理，可重新运行关键词过滤任务，将其导入 `filtered_articles` 后再走后续管道。

## 数据库改动总结
- 新建 `filtered_articles` 表（结构如上）。
- 可选: 为 `news_summaries.article_id` 添加外键指向 `filtered_articles.article_id`。
- `raw_articles` 无新增字段，只保留原样（可视情况保留 `content_hash`、`fingerprint` 作为历史字段，但不再更新）。

## 服务与任务调整
1. **关键词过滤 Worker（新）**
   - 从 `raw_articles` 中读取新抓取或更新的文章。
   - 命中关键词后插入/更新 `filtered_articles`。
   - 可将命中情况写入日志和监控，对关键词命中率进行统计。

2. **Dedup / Sentiment Worker（改造）**
   - 输入改为 `filtered_articles`（增量扫描 `updated_at`）。
   - 更新 `filtered_articles` 的指纹、情感、主文字段。

3. **Summarize / Score / Export Worker（改造）**
   - 在生成摘要前确认 `primary_article_id = article_id`。
   - 调用 `filtered_articles` 上的情感信息，完成四象限排序。

4. **Pipeline 编排**
   - 新的执行顺序示例：
     1. `crawl`
     2. `keyword_filter`（新任务）
     3. `dedup`
     4. `sentiment`
     5. `summarize`
     6. `score`
     7. `export`
   - 可选：将 `dedup` 和 `sentiment` 合并成一个处理任务，减少调度次数。

## 监控与维护
- **覆盖率监控**：关注 `filtered_articles` 的增量及命中率；统计“命中关键词 → 主文 → 摘要”的漏斗。
- **重复率 / 情感分布**：沿用现有 `pipeline_metrics`，但数据源改为 `filtered_articles`。
- **重算策略**：当关键词列表或算法更新时，只对 `filtered_articles` 重新跑流程；必要时可从 `raw_articles` 重新过滤。
- **数据回滚**：保留 `raw_articles` 原文，确保流程失误时可重放。

## 推进步骤建议

### 近期优先事项
1. **DDL 落地**：起草 `filtered_articles` 表结构，包含 `article_id` 主键、`primary_article_id` 外键（自指主文规则）、`content_hash` 唯一索引，并保留情感/摘要所需字段。
2. **历史数据回填**：编写迁移脚本回溯既有命中文章，校验主文自指关系与 `news_summaries` 的摘要对应是否一致，确保唯一约束不会被触发。
3. **Worker 串联**：调整去重、情感、摘要相关任务改为读取 `filtered_articles`，按照“哈希去重 → 情感 → 主文摘要/导出”的顺序处理，确认最小闭环可运行。
## 风险与注意事项
- 关键词列表需及时维护，以免遗漏重要稿件。
- `filtered_articles` 与 `raw_articles` 的数据一致性（article_id 唯一性、更新同步）需通过触发器或应用逻辑保证。
- 如果未来要做事件聚合（非完全重复的相似新闻），可以基于 `filtered_articles` 的主文集合继续扩展聚类逻辑。
- 若用户想查看未命中关键词的新闻，需要明确提供查询手段（例如独立报表或 CLI 命令）。

---

该设计可以显著缩短处理链条的资源消耗，同时保留原始数据和未来扩展能力。待确认后可进入迁移与开发阶段。*** End Patch
