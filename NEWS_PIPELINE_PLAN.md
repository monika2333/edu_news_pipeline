# 新闻流水线重构规划

## 整体规划
- 梳理采集 → 分析 → 聚合 → 报告全流程，明确输入输出、运行频率与人工决策点，形成当前体系的基线图。
- 以分类（新增正/负向）、去重、事件聚合、综合评分四大模块为核心，评估可用数据、缺口与落地优先级。
- 在不影响现有产出表（`news_summaries`、`brief_batches`、`brief_items`）的前提下，增量扩展数据结构并强化可观测性。

## 现有数据库映射
- `raw_articles`：爬虫写入的主表，包含 `content_markdown`、发布时间、来源、互动指标等，实际已存储清洗后的正文，可作为事实表。
- `news_summaries`：LLM 摘要与关键词表，直接以 `article_id` 引用 `raw_articles`。
- `brief_batches` / `brief_items`：报告批次与条目表，对应人工最终筛选的结果。
- `pipeline_runs` / `pipeline_run_steps`：流水线运行与步骤日志，可继续作为监控数据源。
- `toutiao_articles_backup`：历史备份数据，可用于离线比对或模型训练。

> 结论：无需再增加 `news_processed` 表，直接在 `raw_articles` 基础上扩展特征、标签、去重、事件、评分等附表即可。

## 数据结构调整方案

### 1. 原始与标准化层（基于 `raw_articles`）
- 继续以现有字段为主，新增或复用字段：
  - `region_tag`（text）：沿用现有京内/京外标签。
  - `content_hash`（text）：正文哈希用于快速去重。
  - `fingerprint`（text 或 bytea）：SimHash/MinHash 指纹，用于相似度判断。
  - `processing_flags`（jsonb）：记录各阶段（features、labels、dedup、events、scores）是否完成。
- 索引建议：`(publish_time_iso DESC)`、`(source)`、`(content_hash)`。
- 可建立物化视图 `vw_articles_latest`，汇总 `raw_articles` 与常用派生信息，供报告层使用。

### 2. 特征与标签层
- `article_features`
  - `article_id`（PK，引用 `raw_articles.article_id`）
  - `keywords`（text[]）
  - `entities`（jsonb，地点/机构/人物/主题词）
  - `embedding_vector`（pgvector 或 jsonb，用于相似度检索）
  - `language_model_version`（text）
  - `updated_at`（timestamptz）
  - 索引：`(updated_at)`、`gin(entities)`，如使用 pgvector 则需创建向量索引。
- `article_labels`
  - `article_id`（FK）
  - `label_type`（如 `sentiment`、`topic`、`special_flag`）
  - `label_value`（如 `positive`、`education_policy`）
  - `confidence`（numeric）
  - `model_version`（text）
  - `labeled_at`（timestamptz）
  - 唯一键：`(article_id, label_type)`
- 京内/京外标签仍写在 `raw_articles.region_tag`；新增的正负向、主题标签写入 `article_labels`。

### 3. 去重与事件层
- `article_dedup`
  - `article_id`（PK）
  - `primary_article_id`（text，指向保留主文；主文自身=article_id）
  - `fingerprint_distance`（integer）
  - `resolution_strategy`（text，如 `source_priority`、`freshness`）
  - `checked_at`（timestamptz）
  - 索引：`(primary_article_id)`、`(fingerprint_distance)`
- `article_events`
  - `event_id`（uuid，PK）
  - `headline_summary`（text）
  - `first_seen_at`、`last_updated_at`（timestamptz）
  - `topic_tags`（text[]）
  - `status`（`active`/`archived`）
- `article_event_members`
  - `event_id`（FK）
  - `article_id`（FK）
  - `match_score`（numeric）
  - `role`（`representative`/`supporting`）
  - 唯一键：`(event_id, article_id)`
- 去重确保最终仅保留主文；事件聚合用于串联同一主题的多篇报道，两者结合满足需求 2 和 3。

### 4. 综合评分层
- `article_scores`
  - `article_id`（PK）
  - `score_total`（numeric）
  - `score_breakdown`（jsonb，例如 `{ "education_relevance":0.4, "sentiment":0.2, ... }`）
  - `scoring_profile`（text，标识权重配置版本）
  - `scored_at`（timestamptz）
- `scoring_profiles`
  - `profile_name`（PK）
  - `weights`（jsonb）
  - `effective_from` / `effective_to`（timestamptz，可选）
  - 支持历史权重记录与回滚。

### 5. 配置与反馈
- `source_priority`：定义媒体权威度、优先级（可新增）。
- `manual_feedback`
  - `feedback_id`（uuid，PK）
  - `article_id` / `event_id`
  - `feedback_type`（`label_override`、`score_adjust`、`event_merge` 等）
  - `payload`（jsonb，记录具体调整）
  - `handled_by`、`handled_at`
- 继续沿用 `pipeline_runs` 与 `pipeline_run_steps` 追踪任务执行。

## 模块拆分方案

### 分类
- 京内/京外：维持现有实现，仅监控准确率，无需结构调整。
- 正负向分类：
  - 规则版：关键词/模式加权打分，低门槛上线。
  - 模型版：准备 500~1000 条人工标注样本，训练轻量中文分类模型（fastText、BERT 微调等），写回 `article_labels` 并记录 `confidence` 和 `model_version`。
- 主题标签：同时写入 `article_labels`，标记是否与市委教委、重点活动等相关。

### 去重
- `raw_articles` 写入时生成 `content_hash` 和 `fingerprint`。
- 以哈希快速过滤 + 指纹距离精细判断，结果写入 `article_dedup`。
- 对于被判定为重复的文章，可选地在 `article_labels` 增加 `label_type='duplicate'`，方便查询与折叠。

### 同事件聚合
- 利用 `article_features.embedding_vector`、标题及关键词计算相似度。
- 按阈值策略归入既有事件或创建新事件，写入 `article_events` / `article_event_members`。
- `headline_summary` 可由规则或 LLM 自动生成，人工通过 `manual_feedback` 校正。
- 事件状态（`active`/`archived`）控制报告展示范围。

### 综合评分
- 定义可配置特征函数：教育相关度、媒体权威度、情感倾向、舆情风险、主题优先级等。
- 按 `scoring_profiles` 权重合成总分，写入 `article_scores`。
- 报告端依据分数与事件结构确定展示顺序，实现需求 4。

## 处理管道流程（结合现有结构）

### 0. 采集层（Crawler → `raw_articles`）
- 按来源配置抓取频率，写入 `content_markdown`、`publish_time_iso`、`fetched_at` 等现有字段。
- 新增：写入 `content_hash`、`fingerprint`，初始化 `processing_flags`（如 `{ "features":false, "labels":false, ... }`）。

### 1. 特征抽取（→ `article_features`）
- 读取 `processing_flags.features=false` 的文章。
- 执行分词、关键词提取、实体识别、向量化，写入 `article_features`。
- 更新 `processing_flags.features=true` 与 `article_features.updated_at`。

### 2. 分类与标签（→ `article_labels` 与 `raw_articles.region_tag`）
- 京内/京外沿用现有逻辑，仅在监控中统计误差。
- 正负向与主题标签写入 `article_labels`，低置信度样本推送至人工审查队列。
- 更新 `processing_flags.labels=true`。

### 3. 去重识别（→ `article_dedup`）
- 基于 `content_hash` 先做粗筛，再用 `fingerprint` 计算指纹距离。
- 确定主文后写入 `article_dedup`，并标记从属关系。
- 更新 `processing_flags.dedup=true`，必要时在 `article_labels` 标记 `duplicate`。

### 4. 事件聚合（→ `article_events`、`article_event_members`）
- 以向量相似度 + 关键词匹配归并相关文章，生成/更新事件。
- 记录 `match_score` 与事件摘要，更新 `processing_flags.events=true`。
- 聚合结果可写入缓存或物化视图供报告使用。

### 5. 综合评分（→ `article_scores`）
- 按配置计算各特征分值，汇总为总分。
- 写入 `article_scores`，记录 `scoring_profile` 与时间戳。
- 更新 `processing_flags.scores=true`。

### 6. 摘要与报告（`news_summaries`、`brief_*`）
- LLM 摘要继续写入 `news_summaries`，可增 `event_id`、`score_total` 等引用以支撑组合展示。
- 报告生成服务读取视图（`vw_articles_latest` + 事件/评分数据），生成 `brief_batches`、`brief_items`。
- 人工终审修改写回 `manual_feedback`，形成迭代闭环。

### 7. 监控与回溯
- 延续 `pipeline_runs`、`pipeline_run_steps` 记录每次执行状态、耗时、错误。
- 定期统计 KPI：重复率、事件聚合命中率、情感分类准确率、人工修改率。
- 支持按 `content_hash`、`event_id`、`report_date` 回溯特定批次的处理痕迹。

## 流水线服务架构
- 采集服务：保持现有爬虫逻辑，新增哈希/指纹计算即可。
- 处理服务：拆分为特征、分类、去重、事件、评分等任务，使用 Airflow/Celery/Dagster 等编排，支持失败重试与横向扩展。
- 报告服务：基于视图或物化视图组合文章、事件、评分、摘要信息，提供导出与可视化接口。
- 配置中心：统一管理源优先级、分类模型版本、评分权重等，并具备审计、灰度发布与回滚能力。

## 验证与监控
- 构建标注闭环：报告界面人工调整分类、事件或评分时写入 `manual_feedback`，定期抽样回流训练。
- 跟踪关键指标：重复率、事件聚合命中率、情感分类准确率、人工修改率等，按周复盘。
- 维护模型回归测试集：更新分类或评分模型前后对比核心指标，避免性能回退。

## 推进节奏建议
- 第一阶段：扩展 `raw_articles` 字段、搭建 `article_features`、`article_labels`、`article_dedup` 基础表，并梳理任务编排。
- 第二阶段：实现事件聚合与评分逻辑，选取一周数据做 POC，人工校验聚合与评分效果。
- 第三阶段：整合报告输出视图，搭建可视化面板或 Notebook，展示得分、事件聚类效果，并根据反馈迭代策略。
