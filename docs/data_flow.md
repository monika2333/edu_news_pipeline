# 数据流与表职责

本文档说明一条新闻在系统中的完整流转路径、每张表的职责，以及不能随意改动的契约级约束。

**这份文档的目的是补充代码读不出来的信息。** 表结构本身看 `database/schema.sql`；本文只写"为什么是这样"和"什么不能动"。

---

## 一、主干流转

一条新闻从抓取到报送，经过以下阶段。每个阶段对应一个 CLI 命令，由计划任务按顺序调用。

```
                    [抓取]
raw_articles ──────────────────────► crawl / repair
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
     ├──────────────┬─────────────────┐
     │              │                 │
     ▼              ▼                 ▼
manual_reviews   shift_reviews    submission_duplicate_matches
（管理员复核）    （值班编辑复核）   （与历史报送比对）
     │              │
     │              │ 管理员采纳
     │◄─────────────┘
     │
     │ 导出
     ▼
brief_items / brief_batches
（简报批次，TXT 导出 + 飞书推送）


另一条独立入口：

submitted_reports ──► submitted_report_items ──► 回链到 news_summaries
（人工粘贴已报送的稿件）      （逐条拆分 + 相似度回链）
```

---

## 二、流水线各阶段

### 阶段 1：抓取（`crawl`、`repair`）

**写入**：`raw_articles`、`filtered_articles`

各来源 adapter 抓取列表页后写入 `raw_articles`，同时做关键词初筛，命中的写入 `filtered_articles`。

`raw_articles` 的抓取分两步：先写列表信息（`upsert_raw_feed_rows`），再补正文（`update_raw_article_details`，同时写 `detail_fetched_at`）。

> **`detail_fetched_at` 是"正文是否已获取"的判据。** `repair` 命令靠它找出缺正文的行。任何新的入库路径都必须遵守这个两阶段约定——如果一次性写入正文却不写 `detail_fetched_at`，这条数据会被"缺正文修复"逻辑反复扫描；反之如果写了 `detail_fetched_at` 但正文为空，这条数据会被永久跳过。

### 阶段 2：去重与选主（`hash-primary`）

**读**：`filtered_articles`　**写**：`filtered_articles`（指纹字段）、`primary_articles`

同一事件常被多家媒体报道。这一步用 simhash 分桶找出相似文章，从中选一篇作为"主文"写入 `primary_articles`，其余标记 `primary_article_id` 指向主文。

`simhash_band1..4` 是为了加速相似度查找而拆出的分段索引，由 `simhash_bigint` 派生。

### 阶段 3：评分（`score`）

**读写**：`primary_articles`

对主文打相关性分。`score_details` 存放打分过程的原始返回，便于事后追查。

### 阶段 4：摘要与富化（`summarize`、`enrich-summary`、`geo-classify`、`external-filter`）

**写入**：`news_summaries`

这是全系统信息最密集的一张表。它由四个独立步骤分别填充不同的字段组：

| 步骤 | 状态字段 | 主要产出字段 |
|---|---|---|
| `summarize` | `summary_status` | `llm_summary`、`llm_keywords`、`llm_source` |
| `enrich-summary` | `status` | `sentiment_label`、`sentiment_confidence` |
| `geo-classify` | `external_importance_status`（见下方注意） | `is_beijing_related_llm`、`beijing_gate_raw` |
| `external-filter` | `external_importance_status` | `external_importance_score`、`external_importance_raw` |

每个步骤都有独立的 `*_attempted_at` 和 `*_fail_count`，用于重试控制和失败隔离——**某一步失败不会阻塞其他步骤**。

> ⚠️ 新增富化步骤时，应当沿用这个模式：**独立的状态字段 + 独立的失败计数**，不要复用已有步骤的状态字段。

### 阶段 5：人工复核

分两条并行路径，见第三节。

### 阶段 6：导出（`export`）

**写入**：`brief_batches`、`brief_items`

按报别生成 TXT 简报，推送飞书，并把对应 `manual_reviews` 行标记为 `exported`。

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

### ⚠️ 契约级约束：收录时间与班次划分统一依据 `news_summaries.created_at`

一条新闻属于哪个班次，由这个条件决定：

```sql
ns.created_at >= s.starts_at AND ns.created_at < s.ends_at
```

**因此 `news_summaries.created_at` 必须是不可变的。** 任何会更新这个字段的操作（比如 upsert 时误写 `created_at = now()`）都会导致新闻在班次之间跳动，已经做过的复核记录会对不上。

面向用户的时间展示统一称为「收录时间」，原文内容接口读取该字段。待处理新闻的日期筛选与批量放弃也以该字段转换后的 `Asia/Shanghai` 本地日期判定，条件为严格早于所选日期（不含当天）。`publish_time_iso` / `publish_time` 与 `fetched_at` 仍按原链路入库，但不再是这两类读取用途的时间口径。

班次边界默认在 22:00（由 `duty_shift_boundary_hour` 配置）。

---

## 四、报送存档与查重

这是一条**独立于主干流水线**的入口。

### 流程

1. 管理员在 `/submission-archive` 粘贴一份已经报出去的稿件 → `submitted_reports`
2. 系统解析拆分成条目 → `submitted_report_items`
3. 后台子进程做**回链**：把每个条目匹配回系统里的 `news_summaries`（标题相似度 + 正文相似度）
4. 匹配结果写回 `submitted_report_items.link_status`

`link_status` 的取值含义：

| 值 | 含义 |
|---|---|
| `processing` | 后台正在处理 |
| `exact` / `fuzzy` | 自动匹配成功 |
| `pending` | 相似度落在中间地带，需要人工确认 |
| `manual` | 人工确认过 |
| `unmatched` | 没找到对应新闻 |
| `rejected` | 人工判定不匹配 |

### 查重（`submission-dedup`）

用向量相似度，把**新入库的新闻**与**历史报送存档**比对，找出"这条已经报过了"的情况，结果写入 `submission_duplicate_matches`。

复核界面据此显示重复标记，编辑可以确认或忽略（`state`：`suspected` / `confirmed` / `dismissed`）。

`news_title_embeddings` 和 `submitted_report_items.embedding` 分别存两侧的向量，由 `backfill-submission-embeddings` 补齐。

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
| `brief_batches` / `brief_items` | 简报导出批次与条目 | 权威，历史留档 |
| `submitted_reports` / `submitted_report_items` | 人工录入的已报送稿件 | 权威 |
| `submission_duplicate_matches` | 查重结果 | 派生，可重算 |

### 辅助

| 表 | 职责 |
|---|---|
| `console_users` / `console_user_sessions` | 账号与登录会话 |
| `review_events` | 审计日志，记录谁在什么时候改了什么 |
| `pipeline_runs` / `pipeline_run_steps` | 流水线执行记录，用于控制台的运行状态页 |
| `score_feedbacks` | 编辑对 AI 打分的反馈（偏高/偏低） |
| `news_title_embeddings` | 新闻标题向量，用于聚类和查重 |
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
