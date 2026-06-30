# Environment Reference

本项目会按顺序读取 `.env.local`、`.env`、`config/abstract.env`。显式系统环境变量优先级最高；文件中后读取到的同名变量不会覆盖已经存在的值。

`.env.local` 不应提交到 Git。下面示例只放占位值，不包含真实密钥。

## 必须填写

### 数据库

```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=edu_news_pipeline
DB_USER=postgres
DB_PASSWORD=replace-with-your-password
DB_SCHEMA=public
```

这些字段用于应用代码连接 PostgreSQL。`DB_PASSWORD` 是否必须取决于本地数据库配置；如果数据库允许无密码连接，可以不写。

### LLM API

运行 `score`、`summarize`、`external-filter` 等 LLM 步骤时必须设置 API key：

```env
LLM_API_KEY=replace-with-your-llm-api-key
```

项目默认使用 OpenRouter 兼容接口和 DeepSeek V4 Flash。如果保持默认供应商和模型，下面两个可以不写；建议在共享模板中显式写出来，便于别人看懂当前配置：

```env
LLM_API_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=deepseek/deepseek-v4-flash
```

## 建议填写

控制台如果不是严格只在本机访问，建议启用至少一种认证方式：

```env
CONSOLE_BASIC_USERNAME=admin
CONSOLE_BASIC_PASSWORD=replace-with-a-strong-password
```

## 数据库迁移

Dbmate 不读取分开的 `DB_*` 字段，需要单独的 `DATABASE_URL`：

```env
DATABASE_URL=postgres://postgres:replace-with-your-password@localhost:5432/edu_news_pipeline?sslmode=disable
DBMATE_MIGRATIONS_DIR=database/migrations
DBMATE_SCHEMA_FILE=database/schema.sql
```

## LLM 配置

### 推荐只设置这些

```env
LLM_API_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=replace-with-your-llm-api-key
LLM_MODEL=deepseek/deepseek-v4-flash
```

OpenRouter 可选请求标识：

```env
LLM_API_HTTP_REFERER=https://your-project.example
LLM_API_TITLE=Edu News Pipeline
```

默认行为：

- 所有 LLM 任务默认继承 `LLM_MODEL`。
- 评分、外部重要性判断、北京 gate、来源识别、情感判断默认开启 reasoning。
- 摘要生成默认不开启 reasoning。
- reasoning 默认不传 `effort`，并设置 `exclude=true`，避免响应里返回思考内容。
- LLM timeout 默认 90 秒。

### 按任务覆盖模型

只有当某个任务确实需要不同模型时再设置：

```env
LLM_SCORING_MODEL=deepseek/deepseek-v4-flash
LLM_SUMMARY_MODEL=deepseek/deepseek-v4-flash
LLM_SOURCE_MODEL=deepseek/deepseek-v4-flash
LLM_SENTIMENT_MODEL=deepseek/deepseek-v4-flash
LLM_EXTERNAL_FILTER_MODEL=deepseek/deepseek-v4-flash
LLM_BEIJING_GATE_MODEL=deepseek/deepseek-v4-flash
```

### 按任务覆盖 reasoning

通常不需要设置。需要临时调试时可用：

```env
LLM_REASONING_ENABLED=true
LLM_SUMMARY_REASONING_ENABLED=false
LLM_SOURCE_REASONING_ENABLED=true
LLM_SENTIMENT_REASONING_ENABLED=true
LLM_REASONING_EFFORT=high
LLM_REASONING_EXCLUDE=true
```

### 覆盖 timeout

通常不需要设置。需要应对慢模型或限速时可用：

```env
LLM_TIMEOUT=90
LLM_SCORING_TIMEOUT=90
LLM_SUMMARY_TIMEOUT=90
LLM_EXTERNAL_FILTER_TIMEOUT=90
LLM_BEIJING_GATE_TIMEOUT=90
```

### 额度不足飞书提醒

默认启用被动告警：当 LLM 调用返回明确的余额、额度、计费、欠费或 payment 类错误时，系统会复用飞书应用凭证发送文本提醒。普通 429 限速不会触发，除非响应正文明确指向余额或计费问题。

```env
LLM_QUOTA_ALERT_ENABLED=true
LLM_QUOTA_ALERT_COOLDOWN_SECONDS=21600
LLM_QUOTA_ALERT_STATE_PATH=logs/llm_quota_alert_state.json
```

`LLM_QUOTA_ALERT_COOLDOWN_SECONDS` 默认 21600 秒，即同类 LLM 额度/计费问题 6 小时内最多提醒一次。`LLM_QUOTA_ALERT_STATE_PATH` 用于跨定时任务进程记录最近一次提醒时间。

## 控制台认证

本地开发可以不设置。部署到外网或多人使用时必须启用至少一种：

```env
# Browser-friendly basic auth
CONSOLE_BASIC_USERNAME=admin
CONSOLE_BASIC_PASSWORD=replace-with-a-strong-password

# API client bearer token
CONSOLE_API_TOKEN=replace-with-a-long-random-token
```

两种同时设置时，任一认证方式都可通过。

## 流水线运行参数

这些都有代码默认值，通常不需要设置：

```env
PROCESS_LIMIT=5000
CONCURRENCY=50
SUMMARY_CONCURRENCY=50
```

评分和重要性阈值：

```env
SCORE_PROMOTION_THRESHOLD=60
EXTERNAL_FILTER_THRESHOLD=20
EXTERNAL_FILTER_NEGATIVE_THRESHOLD=20
INTERNAL_FILTER_THRESHOLD=20
INTERNAL_FILTER_NEGATIVE_THRESHOLD=20
```

外部过滤批处理和重试：

```env
EXTERNAL_FILTER_BATCH_SIZE=50
EXTERNAL_FILTER_MAX_RETRIES=3
BEIJING_GATE_MAX_RETRIES=3
```

## 关键词和提示词路径

关键词加分规则使用本地配置文件，该文件不会被 Git 跟踪。首次使用时从示例复制：

```powershell
Copy-Item config/score_keyword_bonuses.example.json config/score_keyword_bonuses.json
```

默认路径及其他关键词、提示词路径如下：

```env
KEYWORDS_PATH=config/education_keywords.txt
BEIJING_KEYWORDS_PATH=config/beijing_keywords.txt
SCORE_KEYWORD_BONUSES_PATH=config/score_keyword_bonuses.json
INTERNAL_FILTER_PROMPT_PATH=docs/internal_importance_prompt.md
```

也可以直接内联关键词加分规则：

```env
SCORE_KEYWORD_BONUSES={"高考":10,"中考":8}
```

## 抓取来源

一次性流水线可用 `CRAWL_SOURCES` 选择来源：

```env
CRAWL_SOURCES=toutiao,tencent,chinanews,jyb,chinadaily,gmw,qianlong,laodongwubao
```

当前支持的值：`toutiao`、`tencent`/`qq`、`chinanews`、`jyb`、`chinadaily`、`gmw`、`qianlong`、`laodongwubao`/`ldwb`、`bjrb`/`beijingdaily`。

北京日报不建议加入常规小时流水线的全局 `CRAWL_SOURCES`。服务器每日定时抓取时，优先调用 `scripts/run_bjrb_daily.ps1`；该脚本会在任务进程内临时设置 `CRAWL_SOURCES=bjrb`。

部分来源可选配置：

```env
# Toutiao
TOUTIAO_AUTHORS_PATH=config/toutiao_author.txt
TOUTIAO_FETCH_TIMEOUT=20
TOUTIAO_LANG=zh-CN
TOUTIAO_SHOW_BROWSER=false
TOUTIAO_EXISTING_CONSECUTIVE_STOP=5

# Tencent
TENCENT_AUTHORS_PATH=config/qq_author.txt
TENCENT_DETAIL_DELAY=0.5
TENCENT_EXISTING_CONSECUTIVE_STOP=5

# China Education Daily / JYB
JYB_TIMEOUT=20
JYB_SEARCH_API_URL=
JYB_START_URL=
JYB_KEYWORDS=
JYB_EXISTING_CONSECUTIVE_STOP=5

# China Daily
CHINADAILY_TIMEOUT=20
CHINADAILY_START_URL=
CHINADAILY_EXISTING_CONSECUTIVE_STOP=5

# China News
CHINANEWS_EXISTING_CONSECUTIVE_STOP=5

# GMW
GMW_BASE_URL=
GMW_TIMEOUT=15
GMW_EXISTING_CONSECUTIVE_STOP=5

# Qianlong
QIANLONG_BASE_URL=
QIANLONG_TIMEOUT=15
QIANLONG_DELAY=0.5
QIANLONG_PAGES=3
# QIANLONG_MAX_PAGES is an older alias; prefer QIANLONG_PAGES.
QIANLONG_EXISTING_CONSECUTIVE_STOP=5

# Laodongwubao
LDWB_TIMEOUT=20
LDWB_VERIFY_TLS=true

# Beijing Daily / BJRB
BJRB_DATE=
BJRB_TIMEOUT=20
BJRB_DELAY=0.2
BJRB_BASE_URL=https://bjrbdzb.bjd.com.cn/bjrb
```

`BJRB_DATE` 为空时默认抓取当前北京时间日期；历史回补可临时设置为 `YYYYMMDD`。北京日报建议在服务器每日北京时间 06:00 调用 `scripts/run_bjrb_daily.ps1`，08:00 可补偿重跑一次；本仓库不在开发机注册该定时任务。

## Feishu 通知

需要飞书推送导出结果或 LLM 额度不足提醒时设置：

```env
FEISHU_APP_ID=replace-with-feishu-app-id
FEISHU_APP_SECRET=replace-with-feishu-app-secret
FEISHU_RECEIVE_ID=replace-with-open-id
FEISHU_RECEIVE_ID_TYPE=open_id
```

`FEISHU_RECEIVE_ID_TYPE` 可选：`open_id`、`user_id`、`union_id`。

如果未配置飞书凭证，LLM 额度类错误仍会按原异常失败并写日志，但不会发送提醒。

## Hugging Face / title clustering

标题聚类会使用 `sentence-transformers`。项目默认设置：

```env
HF_HUB_ETAG_TIMEOUT=20
```

通常不需要写入 `.env.local`。如果模型下载网络较慢，可以临时调大。

## 本地推荐模板

大多数本地开发者可以从这份精简模板开始：

```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=edu_news_pipeline
DB_USER=postgres
DB_PASSWORD=replace-with-your-password
DB_SCHEMA=public

DATABASE_URL=postgres://postgres:replace-with-your-password@localhost:5432/edu_news_pipeline?sslmode=disable

LLM_API_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=replace-with-your-llm-api-key
LLM_MODEL=deepseek/deepseek-v4-flash

CONSOLE_BASIC_USERNAME=admin
CONSOLE_BASIC_PASSWORD=replace-with-a-strong-password
```
