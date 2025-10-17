# 新闻流水线重构规划

## 整体规划
- 梳理现有流水线的采集、清洗、入库、筛选、出报全流程，明确数据字段、处理逻辑、运行频率与人工决策点，形成基线流程图。
- 按模块拆分需求：分类、去重、事件聚合、评分四大模块分别评估可用数据、缺口、优先级与验证方式。

## 模块拆分方案

### 分类
- 建立统一的新闻要素结构：标题、摘要、正文、媒体、发布时间、地区标签、关键词、实体识别结果等。
- 京内/京外分类：继续沿用当前规则体系，短期仅增加监控指标以观察准确率，无需改动实现。
- 正面/负面分类：
  - 阶段性方案：关键词或模式匹配加权打分（如“整改”“示警”倾向负向，“表彰”“成绩”倾向正向），快速上线。
  - 中长期方案：收集历史新闻人工标注样本（500~1000 条），训练轻量中文文本分类模型（fastText、BERT 微调等），输出标签与置信度。
- 分类结果写入 `news_classification`（含标签、置信度、模型版本），并预留人工校正回写能力。

### 去重
- 入库时生成正文指纹（SimHash 或 MinHash），计算哈希距离，小于阈值即视为重复。
- 保留权威源首条记录，后续转载记录关联主记录 ID（`duplicate_of`），并基于“新闻源优先级”配置确定保留策略。
- 提供去重命中日志，便于人工审查与规则调优。

### 同事件聚合
- 在去重基础上对标题、关键词、核心实体生成向量（BERT 句向量或 TF-IDF），按相似度聚类。
- 建立事件聚类表：`event_id`、`headline_summary`、`member_news_ids`、聚类时间、主题标签。
- 每日增量处理：新新闻优先尝试归入现有事件，若相似度低于阈值则创建新事件。
- 报告结构按事件块呈现：事件摘要、代表新闻、相关媒体列表，辅助人工筛选。

### 综合评分框架
- 设计可配置的加权评分：`score = Σ(weight_i * feature_i)`。
- 特征示例：教育相关度、主题优先级、媒体权威度、情感倾向、舆情风险、与市委教委关联度等，每个特征输出 0~1 分。

## 数据结构细化

### 核心业务表
- `news_raw`  
  - `id`：主键，采集任务唯一标识。  
  - `source_id`：对应爬虫配置或媒体标识。  
  - `raw_payload`：原始 HTML/JSON 原文，供回溯。  
  - `fetched_at`：抓取时间。  
  - `ingest_batch_id`：批次号，追踪任务运行。  
  - 索引：`(source_id, fetched_at)`，便于快速定位来源数据。

- `news_processed`  
  - `id`：与 `news_raw.id` 一一对应。  
  - `title`、`summary`、`content`：清洗后文本。  
  - `publish_time`、`media_name`、`media_level`。  
  - `region_tag`：京内/京外标签。  
  - `language_features`：结构化字段（关键词列表、实体列表、词频向量 ID 等）。  
  - `processing_status`：`pending`/`completed`/`failed`，用于重试。  
  - 索引：`(publish_time)`、`(media_name)`、全文索引 `content` 支撑检索。

- `news_classification`  
  - `news_id`：外键关联 `news_processed`.  
  - `category`：标签名（如 `sentiment_positive`、`sentiment_negative`）。  
  - `confidence`：模型或规则置信度。  
  - `model_version`：分类模型或规则集版本号。  
  - `updated_at`：最后一次分类时间。  
  - 复合唯一键：`(news_id, category)` 防止重复写入。

- `news_duplicates`  
  - `news_id`：当前记录 ID。  
  - `fingerprint`：指纹值（64 位二进制或十六进制字符串）。  
  - `primary_id`：主记录 ID；若自身为主，则等于 `news_id`。  
  - `distance`：与主记录的指纹距离。  
  - `decision_reason`：保留/合并原因，便于审计。  
  - 索引：`(fingerprint)`、`(primary_id)`。

- `news_events`  
  - `event_id`：主键。  
  - `headline_summary`：事件主题摘要。  
  - `first_seen_at`、`last_updated_at`：事件时间范围。  
  - `topic_tags`：主题标签列表（教育改革、招生政策等）。  
  - `status`：`active`/`archived`，控制报告展示。

- `event_members`  
  - `event_id`：外键。  
  - `news_id`：成员新闻。  
  - `match_score`：聚类相似度。  
  - `is_representative`：是否作为报告展示主文。  
  - 复合唯一键：`(event_id, news_id)`。

- `news_scores`  
  - `news_id`：外键。  
  - `score_total`：综合得分。  
  - `score_breakdown`：JSON 存储各特征分值。  
  - `scoring_profile`：权重配置版本。  
  - `scored_at`：打分时间。

### 配置与日志表
- `source_priority`：媒体优先级、可信度配置。  
- `processing_jobs`：记录各批次处理任务状态、耗时、错误信息。  
- `manual_feedback`：人工调整记录（修正分类、事件、评分），用于二次训练与审计。  
- `feature_dictionary`：关键词、主题词、实体别名字典，供 NLP 模块使用。

## 处理管道流程

### 0. 采集层（Ingestion）
- 触发频率：按来源配置（小时级/天级）。  
- 步骤：爬虫拉取 → 存入 `news_raw` → 写入 `processing_jobs` 记录。  
- 校验：去除明显空内容、重复 URL，失败写入失败队列供重试。

### 1. 文本清洗与标准化
- 从 `news_raw` 拉取待处理数据，执行正文抽取、HTML 去噪、日期解析。  
- 输出写回 `news_processed`，更新 `processing_status`。  
- 若解析失败：标记 `failed` 并写错误日志，配合重跑脚本。

### 2. 语言特征抽取
- 对 `news_processed` 文本执行：分词、关键词提取、实体识别（地名、机构、人物、主题词）。  
- 结果存入 `language_features`（结构化 JSON）与 `feature_dictionary` 索引。  
- 同时为后续聚类准备向量表示（如句向量，存入向量存储或向量表）。

### 3. 分类模块
- 京内/京外：沿用现有规则，仅在 `language_features` 中记录地理实体，用于后续监控。  
- 正负向分类：执行规则打分或模型预测，将标签与置信度写入 `news_classification`。  
- 产出监控：每日统计分类分布、低置信度样本，写入监控报表。

### 4. 去重识别
- 对新入库的 `news_processed` 生成指纹，并与当天/最近一周指纹对比。  
- 命中重复时，将从属记录写入 `news_duplicates` 并指向主记录，同时在 `processing_jobs` 中计数。  
- 未命中重复的记录作为主新闻存储指纹，等待事件聚合。

### 5. 事件聚合
- 使用向量相似度（阈值分层：高阈值直接归类，中阈值待人工确认，低阈值新建事件）。  
- 更新或创建 `news_events`、`event_members`，并为事件生成摘要（可通过标题拼接或抽象算法）。  
- 将聚合结果同步至报告服务的缓存或视图。

### 6. 综合评分
- 读取分类、去重、事件等结果，按配置执行特征函数计算分值。  
- 汇总写入 `news_scores`，同时记录 `scoring_profile` 版本。  
- 根据分数自动设置报告展示优先级和折叠逻辑。

### 7. 报告生成
- 汇总 `news_events` 中活跃事件，选取代表新闻与相关条目。  
- 组合 `news_scores`、`news_classification` 等信息，生成结构化报告（Markdown/Excel）。  
- 输出提交给人工终审界面，同时将人工反馈写入 `manual_feedback`。

### 8. 监控与回溯
- `processing_jobs` 记录全链路处理时长、失败率。  
- 定期导出 KPI（重复率、分类准确率、事件聚合命中率、人工修改率）供周报使用。  
- 提供回溯工具：按 `ingest_batch_id` 复盘某批次的完整数据流。

## 流水线服务架构
- 采集服务：负责与各来源交互，保证数据按批次写入 `news_raw` 与 `processing_jobs`。  
- 处理服务：可拆分为多个独立任务（清洗、特征、分类、去重、聚类、评分），通过任务队列（Airflow、Celery、Dagster 等）串联，支持失败重试与横向扩展。  
- 报告服务：读取汇总视图或物化视图，提供导出与可视化接口；支持事件层级展示。  
- 各服务共享统一配置中心（源优先级、评分权重、分类模型版本），配置更新需具备审计与回滚机制。

## 验证与监控
- 建立标注反馈闭环：报告界面人工修改分类、事件或评分时回写训练集。  
- 定期追踪质量指标：重复率、聚合准确率、分类准确率、人工修改率等，按周审查。  
- 设置模型回归测试集，在规则或模型更新时对比关键指标，防止回归。

## 推进节奏建议
- 优先完成数据结构与处理管道重构，为后续模块提供基础。  
- 选取一周数据做 POC，人工校验分类、去重、聚合效果，迭代阈值与规则。  
- 搭建可视化后台或 Notebook，展示事件聚类、分数分布，辅助策略调优与决策。
