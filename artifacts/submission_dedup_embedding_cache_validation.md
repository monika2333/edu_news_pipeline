# submission_dedup 新闻向量缓存验收报告

验证日期：2026-08-10（Asia/Shanghai）

分支：`codex/improve-pipeline-dedup-cache`

数据库：真实 PostgreSQL `edu_news_pipeline`（PostgreSQL 18.0），不是 FakeCursor

模型：代码常量 `EMBED_MODEL` 指向的本地 BGE 模型

## 实现摘要

- `news_summaries` 新增 `dedup_embedding bytea`、`dedup_embedding_model text`、`dedup_source_hash text`、`dedup_embedded_at timestamptz`。
- 编码输入仍为 `title + "\n" + llm_summary[:EMBED_BODY_CHARS]`；向量编码、序列化、反序列化继续复用 `title_cluster.encode_texts`、`pack_embedding`、`unpack_embedding`。
- 每轮仍读取“当天全部 `ready_for_export` 新闻 × 回看窗口内全部存档向量”。只减少新闻侧重复编码，不改变新闻或存档候选范围，也未修改阈值、Top K、回看天数、正文截断长度或模型常量。
- 每条新闻先计算编码输入的 SHA-256。仅在向量缺失或哈希变化时编码并持久化；哈希相同则直接反序列化复用。
- 只要读取到非空新闻缓存且 `dedup_embedding_model` 不等于当前 `EMBED_MODEL`，立即抛出 `RuntimeError`，不进入编码或匹配。

## 1. 功能等价（真实库、同一批数据）

在改造前代码上先读取真实库并执行原路径的存档校验、84 条新闻完整编码和 `_build_matches`；改造后对相同输入批次从持久化缓存读取向量，再执行相同 `_build_matches`。比较的是传给 `upsert_duplicate_matches` 的五字段 payload，排序后逐项包含：

`article_id`、`item_id`、`similarity`（以 IEEE-754 double 原始字节比较）、`match_method`、`state`。

| 项目 | 改造前直接编码 | 改造后缓存复用 |
|---|---:|---:|
| 新闻数 | 84 | 84 |
| 窗口内存档条目数 | 350 | 350 |
| 匹配对数 | 27 | 27 |
| 输入批次 SHA-256 | `fdd78f23100376a2915e7ff70584dcbbee5ac1766d41c4ddf2909405bcbc2806` | 相同 |
| 五字段结果 SHA-256 | `e9a252a648f65401f5543587ef820dec7c10a900ca3818b79d278408b1a4d461` | 相同 |
| 逐字段差异数 | 0 | 0 |

结果：`baseline_input_equal=true`，`all_match_fields_equal=true`。改造后第一轮真实 worker 也实际向 `submission_duplicate_matches` upsert 了 27 个匹配对。

## 2. 连续三轮性能（真实库）

三轮在同一进程中连续调用完整 `submission_dedup.run()`，均参与 84 条新闻和 350 条窗口内存档条目。

| 轮次 | 总耗时 | 新闻数 | 本轮编码 | 缓存复用 | upsert 匹配数 |
|---|---:|---:|---:|---:|---:|
| 1（建缓存） | 154.718057 s | 84 | 84 | 0 | 27 |
| 2 | 0.027983 s | 84 | 0 | 84 | 27 |
| 3 | 0.028336 s | 84 | 0 | 84 | 27 |

改造前同批 84 条新闻的直接编码实测为 153.427230 秒，即 1.826515 秒/条。第二轮完整 worker 实测为 `0.027983 / 84 = 0.000333` 秒/条，约为旧直接编码单条耗时的 0.018%。

## 3. 缓存失效（真实库事务，已回滚）

验证方式：在真实事务中选取两条已有缓存的当天新闻，仅把 `7672064345160892982` 的 `llm_summary` 前置测试标记；`qianlong:2026/0810/8709607` 不修改。随后执行真实查询、真实模型编码和真实缓存 UPDATE，检查更新结果后回滚事务。

结果：

- 新闻总数 84；编码调用 1 次，输入文本 1 条。
- 更新 payload 仅包含被修改的 `7672064345160892982`，数据库更新 1 行。
- 修改行的 `dedup_source_hash` 和 `dedup_embedded_at` 均已刷新。
- 未修改行被复用；其 embedding 字节、source hash、embedded_at 逐项保持不变。
- 汇总为：重新编码 1、复用 83，符合预期。
- 事务回滚后复查：测试摘要残留 0 条；当天 84 条缓存均恢复为当前模型。

## 4. 模型校验（真实库事务，已回滚）

验证方式：在真实事务中把一条非空缓存的 `dedup_embedding_model` 临时改为 `validation-wrong-model`，重新读取当天新闻并调用新闻向量准备逻辑；同时用哨兵替换编码函数以检测是否发生静默重算。

结果：程序抛出 `RuntimeError`，消息为“新闻查重向量模型与当前模型不一致，必须清空向量并重新生成后再查重”；`encode_called=false`，本轮在编码和匹配前终止。事务随后回滚。

## 5. upsert 覆盖风险确认

- `upsert_news_summaries_from_primary` 的 INSERT 列和 `update_parts` 均为函数内固定显式清单，不包含四个 dedup 列。
- `upsert_news_summary` 虽根据 `columns` 动态生成 `col = EXCLUDED.col`，但 `columns` 来自函数内部新建的固定 `payload`；调用方的 `article` 只逐字段取值，不能把任意键卷入列清单。异常重试路径的 `filtered_columns` 也只从同一固定清单移除 `fetched_at`。
- `insert_pending_summary` 同样从函数内部固定 payload 生成列，且显式 UPDATE 清单不包含 dedup 列。
- 新增回归测试实际检查上述两条主要 upsert SQL 中均不存在四个 dedup 列。

结论：常规主文 promotion 和摘要 upsert 不会覆盖缓存。`llm_summary` 变化后，缓存保留到下一轮，由 `dedup_source_hash` 不一致触发精确失效。

## 6. 迁移与文档

- dbmate 已实际应用 `20260810120000_add_submission_dedup_embeddings.sql`，耗时 50.6806 ms。
- `database/schema.sql` 已通过 `dbmate dump` 生成，没有手工编辑。
- `docs/data_flow.md` 更新了三处：阶段 4 的字段归属表；“报送存档与查重”中的完整比对范围、缓存写入和失效契约；辅助表中明确 `news_title_embeddings` 只用于标题聚类、不参与报送查重。

## 7. 测试结果

- 定向测试：`python -m pytest tests/test_submission_dedup_worker.py tests/test_db_postgres_submission_archive.py tests/test_db_postgres_news_summaries.py -v` → 12 passed。
- 完整测试：`python -m pytest` → 402 passed，2 failed，20 warnings。
- 两个失败均在未修改的 `tests/test_external_filter_worker.py`：测试期望 prompt version `v1`，当前实现返回 `v2`。该模块不在本任务白名单内，且本任务禁止改动其他 worker，因此未修改。
- FakeCursor 测试仅用于 SQL 文本和单元行为；本报告第 1 至第 4 节的等价、性能、失效和模型校验均使用真实 PostgreSQL 数据完成。
