# 数据流与表职责

本文档说明一条新闻在系统中的完整流转路径、每张表的职责，以及不能随意改动的契约级约束。

**这份文档的目的是补充代码读不出来的信息。** 表结构本身看 `database/schema.sql`；本文只写"为什么是这样"和"什么不能动"。

---

## 一、主干流转

一条新闻从抓取到报送，经过以下阶段。每个阶段对应一个 CLI 命令，由计划任务按顺序调用。

```
                    [抓取]
raw_articles ──────────────────────► crawl
     │
     │ 关键词初筛 + 去重指纹
     ▼
filtered_articles ─────────────────► crawl（同一步内完成）/ hash-primary
     │
     │ 选出代表文章（同一事件多篇报道 → 一篇主文）
     ▼
primary_articles ──────────────────► score
     │
     │ 生成摘要、情感、地域、外部重要性
     ▼
news_summaries ────────────────────► summarize / enrich-summary
     │                                geo-classify / external-filter
     │
     ├──────────────┬─────────────────┬─────────────────┐
     │              │                 │                 │
     ▼              ▼                 ▼                 ▼
manual_reviews   shift_reviews    submission_        brief_items /
（管理员复核）    （值班编辑复核）   duplicate_matches  brief_batches
     │              │              （与历史报送比对）  （简报初稿，自动
     │              │ 管理员采纳                        生成，不经人工）
     │◄─────────────┘
     │
     │ 人工采纳、编辑、定稿
     ▼
（实际报送稿由人工发出，系统内不留记录）


另一条独立入口：

submitted_reports ──► submitted_report_items ──► 回链到 news_summaries
（人工粘贴已报送的稿件）      （逐条拆分 + 相似度回链）
```

---

## 二、流水线各阶段

### 阶段 1：抓取（`crawl`）

**写入**：`raw_articles`、`filtered_articles`

各来源 adapter 抓取列表页后写入 `raw_articles`，同时做关键词初筛，命中的写入 `filtered_articles`。

`raw_articles` 的抓取分两步：先写列表信息（`upsert_raw_feed_rows`），再补正文（`update_raw_article_details`，同时写 `detail_fetched_at`）。

> **`detail_fetched_at` 是"正文是否已获取"的判据。** `crawl` 命令靠它找出缺正文的行。任何新的入库路径都必须遵守这个两阶段约定——如果一次性写入正文却不写 `detail_fetched_at`，这条数据会被 `crawl` 的详情补抓逻辑反复扫描；反之如果写了 `detail_fetched_at` 但正文为空，这条数据会被永久跳过。

### 阶段 2：去重与选主（`hash-primary`）

**读**：`filtered_articles`　**写**：`filtered_articles`（指纹字段）、`primary_articles`

同一事件常被多家媒体报道。这一步用 simhash 分桶找出相似文章，从中选一篇作为"主文"写入 `primary_articles`，其余标记 `primary_article_id` 指向主文。

`simhash_band1..4` 是为了加速相似度查找而拆出的分段索引，由 `simhash_bigint` 派生。

### 阶段 3：评分（`score`）

**读写**：`primary_articles`

对主文打相关性分。关键词加减分先基于标题、正文和已有关键词计算；如果模型原始分即使达到上限 100，加上净关键词分后仍低于 promotion 阈值，则不调用模型，直接写为 `filtered_out`。这只省略不可能改变结果的模型调用，不改变最终筛选集合。

未经模型评分的行约定为：`raw_relevance_score = NULL`，`keyword_bonus_score` 和 `score` 都写实际净关键词分；`score_details.matched_rules` 保留完整命中规则，并以 `llm_skipped = true`、`skip_reason = "keyword_bonus_below_threshold"` 明确区别于模型实际判出的低分。其余文章仍由模型原始分加关键词分得到 `score`，`score_details` 继续保存各组成部分，便于事后追查。

### 阶段 4：摘要与富化（`summarize`、`enrich-summary`、`geo-classify`、`external-filter`）

**写入**：`news_summaries`

这是全系统信息最密集的一张表。四个生成/富化步骤和后续报送查重分别填充不同的字段组：

| 步骤 | 状态字段 | 主要产出字段 |
|---|---|---|
| `summarize` | `summary_status` | `llm_summary`、`llm_keywords` |
| `enrich-summary` | `status` | `sentiment_label`、`sentiment_confidence`、`llm_source` |
| `geo-classify` | `external_importance_status`（见下方注意） | `is_beijing_related_llm`、`beijing_gate_raw` |
| `external-filter` | `external_importance_status` | `external_importance_score`、`external_importance_raw` |
| `submission-dedup` | 无独立状态字段 | `dedup_embedding`、`dedup_embedding_model`、`dedup_source_hash`、`dedup_embedded_at` |

前四个生成/富化步骤各有独立的 `*_attempted_at` 和 `*_fail_count`，用于重试控制和失败隔离——**某一步失败不会阻塞其他生成/富化步骤**。`submission-dedup` 的四个字段是可重建缓存，不承担流水线重试状态。

`llm_source` 表示模型识别出的发布/署名媒体名称。来源响应完成既有格式清洗后，只有长度不超过 64 个字符的结果才会进入来源名称归一化；归一化依次剥离一次渠道后缀、再按整串全等规则替换别名，规则来自 `config/source_aliases.json`，处理后的值才写入数据库。超过 64 个字符的内容视为模型未按格式返回，必须整体丢弃并写入 `NULL`，不得截断保存。来源为空时，导出与人工复核界面回退使用抓取来源。

> ⚠️ 新增富化步骤时，应当沿用这个模式：**独立的状态字段 + 独立的失败计数**，不要复用已有步骤的状态字段。

### 阶段 5：人工复核

分两条并行路径，见第三节。

### 阶段 6：导出（`export`）

**写入**：`brief_batches`、`brief_items`

取数条件为 `news_summaries.status = 'ready_for_export'` 且 `summary_status = 'completed'` 且 `score >= min_score`（默认读取 `SCORE_PROMOTION_THRESHOLD`），按报别生成 TXT 简报并推送飞书。

该查询**不涉及 `manual_reviews` 或 `shift_reviews`**：凡是过阈值的文章都会进入 `brief_items`，与人工是否采纳无关。这条路径产出的是供人工参考的初稿，不是最终报送内容。

`manual_reviews.status = 'exported'` 由控制台的「归档」操作写入（`POST /api/manual-filter/archive` → `manual_filter_decisions.archive_items`），是管理员的手动动作，与本阶段的导出脚本无关。

---

## 三、双轨复核模型

这是本系统最需要理解的部分。管理员和值班编辑用**两张不同的表**，互不干扰。

### `manual_reviews` —— 管理员工作区

- **一篇文章一行**（`article_id` 唯一）
- 状态：`pending` / `selected` / `backup` / `discarded` / `exported`
- `report_type` 只有 `zongbao` / `wanbao`，且**只在采纳时才有意义**——`pending` 状态下这个值不代表任何东西
- `version` 用于乐观锁，防止两个管理员同时改同一条

### `shift_reviews` —— 值班编辑工作区

- **一个班次一篇文章一行**（`shift_id` + `article_id`）
- 同一篇文章可以在多个班次里各有一行，互不冲突
- 值班编辑只能读写**自己班次**的行
- `finalized_batch_id` / `finalized_rank` 表示已定稿，两者必须同时有值或同时为空（有 CHECK 约束保证）
- 值班编辑按筛选条件批量放弃时，服务端直接用 `INSERT ... SELECT ... ON CONFLICT DO UPDATE` 写入本表；匹配条件复用管理员候选池的统一筛选器，但额外受班次归属约束。该路径不读取或写入 `manual_reviews`，不覆盖已有决定或已定稿条目，也不做逐行版本校验。

### 两者的关系

值班编辑先做初筛，管理员通过"送入管理员工作区"把结果导入 `manual_reviews`（`import_shift_reviews_into_manual`）。**导入时会复制内容字段**（编辑过的摘要等），此后两张表各自独立演进。

管理员查看值班结果时，默认的「未处理」范围会重新展示在全量新闻筛选中已被 `discarded` 的文章，给值班编辑的采纳或备选结论保留一次复核机会。此时全量页的放弃记录只作为后台历史存在：值班结果卡片按「未处理」呈现，管理员可以直接采纳、备选或放弃，不需要先撤回原决定，采纳或备选时也不触发版本选择。只有文章已在管理员工作区采纳、备选或归档，或者管理员已在值班结果页明确放弃（`shift_reviews.admin_discarded_at`）时，才从「未处理」中排除；切换到「全部」仍可查看该栏目的完整值班结果。值班结果页的栏目数字必须与当前「未处理 / 全部」范围使用相同口径。

### ⚠️ 契约级约束：收录时间与班次划分统一依据 `news_summaries.created_at`

一条新闻属于哪个班次，由这个条件决定：

```sql
ns.created_at >= s.starts_at AND ns.created_at < s.ends_at
```

**因此 `news_summaries.created_at` 必须是不可变的。** 任何会更新这个字段的操作（比如 upsert 时误写 `created_at = now()`）都会导致新闻在班次之间跳动，已经做过的复核记录会对不上。

面向用户的时间展示统一称为「收录时间」，原文内容接口读取该字段。筛选页的管理员视图显示全库最新收录时间；值班编辑视图只显示当前班次半开区间内的最大值，未来班次不显示。待处理新闻的日期筛选与批量放弃也以该字段转换后的 `Asia/Shanghai` 本地日期判定，条件为严格早于所选日期（不含当天）。`publish_time_iso` / `publish_time` 与 `fetched_at` 仍按原链路入库，但不再是这两类读取用途的时间口径。

班次边界默认在 22:00（由 `duty_shift_boundary_hour` 配置）。

---

## 四、报送存档与查重

这是一条**独立于主干流水线**的入口。

### 流程

1. 管理员在 `/submission-archive` 粘贴一份已经报出去的稿件，或白名单用户将格式完整的稿件私聊发送给飞书机器人 → `submitted_reports`
2. 系统解析拆分成条目 → `submitted_report_items`
3. 后台子进程做**自动回链**：把每个条目匹配回系统里的 `news_summaries`（标题相似度 + 正文相似度）
4. 编辑可以确认自动候选，也可以围绕报告 `compiled_date` 检索 `news_summaries.title` / `llm_summary`，人工绑定或解绑条目
5. 自动与人工结果都写回 `submitted_report_items`；人工绑定只改 `article_id`、`link_status`、`link_decided_by`、`link_matched_at`，解绑将状态恢复为 `unmatched`

飞书入口只接受首行匹配当前报送稿标题的私聊纯文本；普通聊天、群聊、非文本消息和非白名单用户消息不进入解析。识别成功后直接保存，解析警告随回复返回但不阻止入库；同报别同日期冲突绝不自动覆盖。`submitted_reports.ingest_source` 记录入口，`source_message_id` 以唯一索引提供跨进程重启的幂等保证，`source_sender_id` 保留提交人审计标识。控制台录入的 `ingest_source` 为 `console`，外部消息字段为空；飞书录入为 `feishu`。

`link_status` 的取值含义：

| 值 | 含义 |
|---|---|
| `processing` | 后台正在处理 |
| `matched` | 已匹配到系统新闻（自动匹配或人工确认均使用此状态） |
| `pending` | 相似度落在中间地带，需要人工确认 |
| `unmatched` | 没找到对应新闻 |
| `rejected` | 人工判定不匹配 |

数据库、worker、控制台 API 与前端统一使用 `matched`，报告汇总统一返回 `matched_count`；不区分标题完全一致、相似度自动通过、队列确认或人工检索绑定。`pending` 尚未人工确认，不算匹配成功。

人工检索绑定保留自动回链证据：`best_candidate_article_id` 与 `link_title_score` / `link_body_score` / `link_combined_score` 一律不改。因而当 `article_id` 与 `best_candidate_article_id` 不同时，可以事后识别人工检索介入。人工绑定与解绑都拒绝 `processing` 条目，避免独立 worker 的整体回写覆盖人工决定。

存档库详情页条目上的修改入口（铅笔图标，仅管理员）允许事后修正已入库条目的 `title` / `body` / `source` / `urls`：改标题会同步重算 `norm_title` / `norm_title_hash`；标题或正文任一变化会清空 `embedding` / `embedding_model` / `embedded_at`，由 `backfill-submission-embeddings` 在下一轮重算查重向量。与人工绑定/解绑一样，`processing` 条目拒绝修改；回链字段（`article_id`、`link_status`、相似度分数）不受条目修改影响。

### 查重（`submission-dedup`）

用向量相似度，把**当天全部 `ready_for_export` 新闻**与回看窗口内的**全部历史报送存档条目**比对，找出"这条已经报过了"的情况，结果写入 `submission_duplicate_matches`。这一步每轮都保持完整比对范围，不按向量缓存的新旧程度缩小候选集。

复核界面据此显示重复标记，编辑可以确认或忽略（`state`：`suspected` / `confirmed` / `dismissed`）。

存档侧向量保存在 `submitted_report_items.embedding`，由 `backfill-submission-embeddings` 补齐。新闻侧向量保存在 `news_summaries.dedup_embedding`，由 `submission-dedup` 在首次参与查重时写入；其编码文本固定为标题加 `llm_summary` 的前 `EMBED_BODY_CHARS` 个字符，不能复用只编码标题的 `news_title_embeddings`。

`dedup_embedding_model` 记录编码模型，读取已有向量时必须与代码中的当前模型常量完全一致，否则整轮报错终止。`dedup_source_hash` 是上述完整编码输入文本的 SHA-256；标题或 `llm_summary` 变化会导致哈希不一致，下一轮仅重新编码这些失效行并刷新 `dedup_embedded_at`，其余新闻直接复用缓存。常规摘要或主文 upsert 不写这四个字段，因此不会无意覆盖已生成缓存；摘要实际变化依靠源哈希在下轮失效。

---

## 五、表职责一览

### 流水线主干（数据逐级流动）

| 表 | 职责 | 权威性 |
|---|---|---|
| `raw_articles` | 抓取的原始数据 | 权威，只增 |
| `filtered_articles` | 关键词初筛通过的文章 + 去重指纹 | 派生自 raw |
| `primary_articles` | 去重后的代表文章 + 相关性分 | 派生 |
| `news_summaries` | 摘要、情感、地域、重要性——**复核环节的数据来源** | 权威 |

### 人工复核

| 表 | 职责 | 权威性 |
|---|---|---|
| `manual_reviews` | 管理员的采纳/放弃决定 | **权威，业务核心** |
| `shift_reviews` | 值班编辑的班次内决定 | **权威，业务核心** |
| `shift_review_finalization_batches` | 班次定稿批次 | 权威 |
| `duty_shifts` | 具体班次（谁、什么时间段） | 权威 |
| `duty_schedules` | 七天轮值模板，用于生成班次 | 权威 |
| `manual_clusters` | 聚类结果**缓存** | **缓存，可随时清空重建** |

### 导出与报送

| 表 | 职责 | 权威性 |
|---|---|---|
| `brief_batches` / `brief_items` | 自动生成的简报初稿批次与条目 | 对「导出事件本身」权威；**不代表人工采纳，也不代表已报送** |
| `submitted_reports` / `submitted_report_items` | 人工录入的已报送稿件 | 权威 |
| `submission_duplicate_matches` | 查重结果 | 派生，可重算 |

> **判断某篇是否报送过，一律以 `submitted_report_items` 为准，不得依据 `brief_items`。**
> `brief_items` 只说明这篇进入过自动初稿，所有过阈值的文章都会进入；实际报送稿由人工定稿后发出，系统内唯一的记录来源是人工录入的报送存档。

### 辅助

| 表 | 职责 |
|---|---|
| `console_users` / `console_user_sessions` | 账号与登录会话 |
| `review_events` | 审计日志，记录谁在什么时候改了什么 |
| `pipeline_runs` / `pipeline_run_steps` | 流水线执行记录，用于控制台的运行状态页 |
| `score_feedbacks` | 编辑对 AI 打分的反馈（偏高/偏低），按文章当前评分上下文（prompt_key + prompt_version）关联；人工筛选/值班工作区与全库检索卡片（经 `/api/articles/score-feedback`）都写这张表 |
| `news_title_embeddings` | 仅编码新闻标题的向量，用于人工筛选聚类；不参与报送查重 |
| `schema_migrations` | dbmate 迁移记录，**不要手工修改** |

---

## 六、契约级约束清单

以下约束一旦破坏会造成难以发现的数据错乱，修改相关代码前必须确认。

| 约束 | 原因 | 破坏后果 |
|---|---|---|
| `news_summaries.created_at` 不可变 | 班次划分的唯一依据 | 新闻在班次间跳动，复核记录对不上 |
| `manual_reviews.article_id` 唯一 | 一篇文章只有一个管理员决定 | 状态冲突，采纳结果不确定 |
| 各来源 `article_id` 必须稳定 | 全链路的关联键 | 同一篇文章重复入库，去重失效 |
| `detail_fetched_at` 与正文成对写入 | 缺正文修复的判据 | 数据被反复扫描或永久跳过 |
| `manual_clusters` 是缓存 | 由计划任务整表重建 | ——（可安全清空） |
| 聚类不区分报别 | 待筛选池本来就不分报别 | 加回报别维度会导致 `cluster_id` 唯一约束冲突 |
| `report_type` 的两个枚举不可合并 | 新闻报别两值，报送稿类型三值（含 `feedback`） | 值班编辑下拉出现"反馈"，或报送存档无法录入反馈 |
