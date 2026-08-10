# score 关键词前置跳过验收报告

验证日期：2026-08-10（Asia/Shanghai）

分支：`codex/score-keyword-precheck`

数据库：真实 PostgreSQL `edu_news_pipeline`，不是 FakeCursor

当前 promotion 阈值：30；当前关键词规则：23 条

## 实现摘要

- 单线程和多线程评分路径都先复用 `_calculate_keyword_bonus`；该函数继续复用原有 `_collect_text_sources`，匹配逻辑和文本来源未修改。
- 严格使用 `100 + bonus_score < score_promotion_threshold` 判据。规则字典为空时显式不跳过。
- 多线程路径先把全部 rows 分为跳过组和待调用组，只把待调用组提交给 `ThreadPoolExecutor`。
- 待调用组携带前置阶段得到的 `bonus_score` 和 `matched_rules`，模型返回后直接复用，不重复匹配。
- 跳过组直接进入既有 primary score UPDATE：`status=filtered_out`、`raw_relevance_score=NULL`、`keyword_bonus_score=bonus_score`、`score=bonus_score`。
- `score_details` 新增 `llm_skipped=true` 和 `skip_reason=keyword_bonus_below_threshold`，同时保留完整 `matched_rules`。
- 每轮日志显式输出因关键词上界判定省略的 LLM 调用数。

## 1. 行为等价（真实 PostgreSQL，1000 条）

从真实 `primary_articles` 选取按 `created_at DESC, article_id` 排序的最近 1000 条已有 `raw_relevance_score` 记录。全部 1000 条正文非空，因此旧路径原本都会实际进入 LLM 调用。

为排除外部模型重复调用产生的随机波动，并保证前后使用完全相同的模型原始分，改造前回放使用数据库中已持久化的真实 `raw_relevance_score`，按旧公式逐篇计算 `raw_score + bonus_score`；改造后实际执行 `_process_scores_multi_worker`，由回放 scorer 返回同一条已持久化原始分，再执行 `_prepare_updates`。数据源是真实 PostgreSQL，LLM 返回值固定为每条历史真实结果，不是合成分数或 FakeCursor。

两次回放的输入与旧结果摘要 SHA-256 均为：

`3a90da4409e0b5ede50b3da6d971ae4b34876eb7e65e5168d0e07fd1809267e6`

| 结果 | 改造前 | 改造后 |
|---|---:|---:|
| `scored` | 483 | 483 |
| `filtered_out` | 517 | 517 |
| 合计 | 1000 | 1000 |

逐篇 status 差异数：**0**。`filtered_out` 与 `scored` 两个 article_id 集合完全一致。

## 2. 调用量下降

- 真实批次大小：1000 条。
- 提前跳过：1 条。
- 进入 scorer / LLM 的记录：999 条。
- 减少 LLM 调用：1 次。
- 跳过占比：0.1%。
- 被跳过文章 `7672199970778382891` 的正文去空白后长度为 3149，旧 `_score_item` 会调用模型，不属于原本就因空正文跳过的情况。

本批次跳过比例取决于当前新闻内容和规则命中分布；实现没有扩大判据。

## 3. 边界用例

边界用例直接执行新的多线程处理函数，并记录 `_score_item` 是否被调用：

| 场景 | 净关键词分 | 理论最高最终分 | 结果 |
|---|---:|---:|---|
| 同时命中 `-100`、`+100`、`+100`，净值为正 | +100 | 200 | 正常调用模型，未跳过 |
| 规则字典为空，测试阈值设为 101 | 0 | 100 | 正常调用模型，未跳过 |
| 命中 `-100` 和 `+50`，净值为负但最高分恰好达到阈值 50 | -50 | 50 | 正常调用模型，未跳过 |

三项均验证 `calls == ["boundary"]`、`skipped == 0`。第三项同时证明判据使用严格小于号，不会把等于阈值的记录误杀。

另有回归检查确认每篇文章的 `_calculate_keyword_bonus` 只调用一次；待调用组不会在模型返回后重复计算。

## 4. 真实写入与 score_details

在真实 PostgreSQL 事务中，对真实文章 `7672199970778382891` 执行新的前置分组、`_prepare_updates` 和真实 `update_primary_article_scores`。数据库实际更新 1 行、候选调用数 0、promotion 数 0，读取到：

```json
{
  "status": "filtered_out",
  "score": -100,
  "raw_relevance_score": null,
  "keyword_bonus_score": -100,
  "score_details": {
    "final_score": -100,
    "llm_skipped": true,
    "skip_reason": "keyword_bonus_below_threshold",
    "matched_rules": [
      {
        "bonus": -100,
        "label": "人民论坛每日推荐",
        "rule_id": "keyword:人民论坛每日推荐"
      }
    ],
    "keyword_bonus_score": -100,
    "raw_relevance_score": null
  }
}
```

检查完成后事务已回滚，并逐字段确认原数据库记录恢复成功。

## 5. data_flow.md 更新

更新“阶段 3：评分（`score`）”的两个方面：

- 写明关键词净分在 LLM 之前计算，以及只有理论最高分仍低于阈值时才省略调用。
- 新增“未经模型评分”字段契约：`raw_relevance_score=NULL`、`score=keyword_bonus_score`、完整 `matched_rules`，以及显式 skip 标记和原因。

## 6. 测试

- 定向测试：`python -m pytest tests/test_score_worker.py -q` → 6 passed。
- 编译检查：`python -m compileall -q src/workers/score.py tests/test_score_worker.py` → 通过。
- diff 检查：`git diff --check` → 通过。
- score 与提示词版本相关测试：`python -m pytest tests/test_external_filter_worker.py tests/test_score_worker.py -v` → 10 passed。
- 完整测试：`python -m pytest` → 403 passed，20 warnings。

`config/prompts/VERSIONS` 已将 `internal_positive` 升级为 `v2`，因此同步把 `tests/test_external_filter_worker.py` 中两处仍期望 `v1` 的断言更新为 `v2`；`external_negative` 仍为 `v1`，对应断言保持不变。同步后完整套件全绿。
