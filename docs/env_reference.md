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

运行 `score`、`summarize`、`enrich-summary`、`geo-classify`、`external-filter` 等 LLM 步骤时必须设置 API key。`geo-classify` 会先执行本地规则，再对疑似北京文章调用 Beijing Gate：

```env
LLM_API_KEY=replace-with-your-llm-api-key
```

模型与接入点（endpoint）配置已迁入控制台设置页（`llm_endpoints` / `llm_models` 分区）：每个 LLM 步骤可以指定接入点，接入点保存服务地址与 Key 所在的环境变量名，API Key 本身仍只存放在 `.env`。修改 `.env.local` 后无需重启服务：在控制台设置页「环境（只读）」块点「重新加载环境变量」即可让文件里的改动生效（数据库连接等少数配置仍需重启）。接入点地址的主机必须在 `LLM_ALLOWED_HOSTS` 白名单内：

```env
LLM_ALLOWED_HOSTS=openrouter.ai,api.deepseek.com,open.bigmodel.cn
```

未配置时使用内置默认值（即上面这一组）。精确匹配主机名、大小写不敏感、不支持通配符、只允许 443 端口；新增服务商时需要同时在该变量中加入其域名，然后在控制台点「重新加载环境变量」（或重启服务）再保存接入点。`LLM_API_BASE_URL` 已不再生效（仅单一地址、无法按步骤区分），设置后启动时会告警提示移除。

注意失败半径：白名单在配置加载时对整个 `llm_endpoints` 分区生效——如果从这个变量删掉某个域名，而数据库里仍有接入点指向它，**整条流水线的配置加载都会失败**（`load_business_config` 报「数据库业务配置无效」），不只是用到该接入点的步骤。清理该变量前必须先在控制台删掉指向对应域名的接入点。

## 建议填写

浏览器控制台使用数据库账号登录。下面两项只为旧 Basic 认证兼容保留：

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
LLM_API_KEY=replace-with-your-llm-api-key
LLM_ALLOWED_HOSTS=openrouter.ai,api.deepseek.com,open.bigmodel.cn
```

`LLM_API_BASE_URL` 已失效：服务地址改由控制台「接入点」配置管理，不要在 `.env` 中保留。

OpenRouter 可选请求标识（仅对 `api_style: openrouter` 的接入点发送）：

```env
LLM_API_HTTP_REFERER=https://your-project.example
LLM_API_TITLE=Edu News Pipeline
```

默认行为：

- 各步骤模型、默认模型、reasoning 开关及所用接入点已迁入控制台设置页。
- reasoning 默认不传 `effort`，并设置 `exclude=true`，避免响应里返回思考内容。
- `api_style: thinking` 的接入点（DeepSeek、GLM 官方接口）关闭思考时显式发送 `{"thinking": {"type": "disabled"}}`。
- LLM timeout 默认 90 秒。

### 模型配置

默认模型、七个步骤的模型覆盖、各步骤 reasoning 开关及各步骤使用的接入点已迁入控制台
设置页；旧的 `LLM_MODEL`、各 `LLM_*_MODEL`、`LLM_REASONING_ENABLED`、
`LLM_SUMMARY_REASONING_ENABLED`、`LLM_SOURCE_REASONING_ENABLED`、
`LLM_SENTIMENT_REASONING_ENABLED` 和 `LLM_API_BASE_URL` 变量不再生效。

### reasoning 的全局参数

effort、max tokens 和是否从响应排除 reasoning 仍由环境变量统一控制：

```env
LLM_REASONING_EFFORT=high
LLM_REASONING_MAX_TOKENS=2048
LLM_REASONING_EXCLUDE=true
```

### 覆盖 timeout

这些值是单次 HTTP 读取的间隔超时：若相邻响应字节之间超过该时长，请求会失败。通常不需要设置；需要应对慢模型或限速时可用：

```env
LLM_TIMEOUT=90
LLM_SCORING_TIMEOUT=90
LLM_SUMMARY_TIMEOUT=90
LLM_EXTERNAL_FILTER_TIMEOUT=90
LLM_BEIJING_GATE_TIMEOUT=90
```

### 覆盖单任务墙钟预算

墙钟预算限制一次业务调用的总耗时，包括该条任务内部的全部 HTTP 请求、重试与退避等待。它与上面的 timeout 不同：`LLM_TIMEOUT` 系列限制单次读取的字节间隔，`LLM_BUDGET` 系列限制整次业务调用的累计墙钟时长。

全局预算默认 180 秒。该值是尚待生产耗时分布校准的初值；各阶段未配置覆盖项时继承全局值：

```env
LLM_BUDGET=180
LLM_BEIJING_GATE_BUDGET=180
LLM_SCORING_BUDGET=180
LLM_SUMMARY_BUDGET=180
LLM_EXTERNAL_FILTER_BUDGET=180
LLM_SENTIMENT_BUDGET=180
LLM_SOURCE_BUDGET=180
LLM_DUPLICATE_REVIEW_BUDGET=180
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

浏览器使用数据库账号和可撤销的服务端会话。先执行迁移，再通过
`python -m src.cli.main create-console-user` 创建管理员账号。

```env
# HTTPS 部署必须设为 true；本地 HTTP 开发保持 false
CONSOLE_COOKIE_SECURE=false

# 服务端会话有效期，默认 14 天
CONSOLE_SESSION_DAYS=14

# 每日班次边界，默认北京时间 22 点
DUTY_SHIFT_BOUNDARY_HOUR=22

# 兼容期共享 Basic 认证，仅用于旧入口
CONSOLE_BASIC_USERNAME=admin
CONSOLE_BASIC_PASSWORD=replace-with-a-strong-password

# 受控自动化调用的 Bearer token，不可用于值班班次接口
CONSOLE_API_TOKEN=replace-with-a-long-random-token
```

共享 Basic 认证和 Bearer token 是迁移兼容路径，不代替真实用户账号。生产环境应通过
HTTPS 暴露控制台，并设置 `CONSOLE_COOKIE_SECURE=true`。

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
EXTERNAL_FILTER_POSITIVE_THRESHOLD=20
EXTERNAL_FILTER_NEGATIVE_THRESHOLD=20
INTERNAL_FILTER_POSITIVE_THRESHOLD=20
INTERNAL_FILTER_NEGATIVE_THRESHOLD=20
```

外部过滤批处理和重试：

```env
EXTERNAL_FILTER_BATCH_SIZE=50
EXTERNAL_FILTER_MAX_RETRIES=3
BEIJING_GATE_MAX_RETRIES=3
```

### 报送存档与查重

以下六项都可省略；不配置时使用代码默认值：

```env
SUBMISSION_DEDUP_LOOKBACK_DAYS=15
SUBMISSION_LINK_AUTO_THRESHOLD=0.65
SUBMISSION_LINK_REVIEW_THRESHOLD=0.55
SUBMISSION_DEDUP_RECALL_THRESHOLD=0.90
SUBMISSION_FEEDBACK_LOOKBACK_DAYS=7
SUBMISSION_FEEDBACK_MATCH_THRESHOLD=0.90
```

以上六个数值就是 `submission_archive_config.py` 中的代码默认值；修改该文件中的
默认值时必须同步更新本节。

`SUBMISSION_DEDUP_LOOKBACK_DAYS` 只控制每天自动比对的日期窗口，不会
删除历史存档。`SUBMISSION_FEEDBACK_LOOKBACK_DAYS` 控制反馈条目在取材日
（`compiled_date`）轴上向前查找综报/晚报条目的天数；候选窗口为
`[反馈 compiled_date - 天数, 反馈 compiled_date]`，上界是闭区间。
`SUBMISSION_FEEDBACK_MATCH_THRESHOLD`
是条目间向量余弦相似度阈值；二者与新闻查重配置相互独立。嵌入模型固定为
`BAAI/bge-large-zh`，不能通过环境变量更换；
更换模型必须清空已有存档向量并完整重算。

## 提示词路径

提示词路径仍由环境变量控制（通常不需要修改）：

```env
EXTERNAL_FILTER_PROMPT_PATH=config/prompts/external_positive_importance_prompt.md
EXTERNAL_NEGATIVE_FILTER_PROMPT_PATH=config/prompts/external_negative_importance_prompt.md
INTERNAL_FILTER_PROMPT_PATH=config/prompts/internal_positive_importance_prompt.md
INTERNAL_NEGATIVE_FILTER_PROMPT_PATH=config/prompts/internal_negative_importance_prompt.md
BEIJING_GATE_PROMPT_PATH=config/prompts/beijing_gate_prompt.md
```

## 已废弃：关键词配置改由控制台设置页管理

评分加分词表、抓取教育关键词、京内关键词和来源别名已迁入控制台设置页
（`app_settings` 的 `score_keyword_bonuses` / `education_keywords` /
`beijing_keywords` / `source_aliases` 分区），以下环境变量与本地文件**不再生效**，
设置后启动时会逐项告警提示移除：

```env
KEYWORDS_PATH
BEIJING_KEYWORDS_PATH
SOURCE_ALIASES_PATH
SCORE_KEYWORD_BONUSES
SCORE_KEYWORD_BONUSES_PATH
```

对应的历史文件 `config/education_keywords.txt`、`config/beijing_keywords.txt`、
`config/source_aliases.json`、`config/score_keyword_bonuses.json` 同样不再读取，
仅作为 `import-settings` 的导入来源保留；导入完成后可自行删除。

## 抓取来源

每小时抓取来源及其顺序已迁入控制台设置页。一次性流水线需要覆盖来源时，使用
`python -m scripts.run_pipeline_once --sources ...` 或 `python -m src.cli.main crawl --sources ...`。

当前支持的值：`toutiao`、`tencent`/`qq`、`chinanews`、`chinanews_xj`、`jyb`、`chinadaily`、`gmw`、`qianlong`、`xinhua`、`stdaily`、`bbtnews`、`laodongwubao`/`ldwb`、`bjrb`/`beijingdaily`、`btime`、`beijinghao`。

北京日报和劳动午报不能加入每小时来源列表。服务器每日定时抓取时，分别调用
`scripts/run_bjrb_daily.ps1` 和 `scripts/run_ldwb_daily.ps1`，脚本通过单次
`--sources` 覆盖指定来源。

部分来源可选配置：

```env
# Toutiao（账号已迁入控制台设置页）
TOUTIAO_FETCH_TIMEOUT=20
TOUTIAO_LANG=zh-CN
TOUTIAO_SHOW_BROWSER=false
TOUTIAO_EXISTING_CONSECUTIVE_STOP=5
# 头条和腾讯新账号首次抓取的单账号条数上限（最小为 1）；首次抓取同时只请求第一页
CRAWL_FIRST_RUN_LIMIT=10

# Tencent（账号已迁入控制台设置页）
TENCENT_DETAIL_DELAY=0.5
TENCENT_EXISTING_CONSECUTIVE_STOP=5

# Btime / 北京时间与 Beijinghao / 北京号账号已迁入控制台设置页

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

# China News Xinjiang
CHINANEWS_XJ_EXISTING_CONSECUTIVE_STOP=5

# GMW
GMW_BASE_URL=
GMW_TIMEOUT=15
GMW_EXISTING_CONSECUTIVE_STOP=5

# Qianlong
# 默认同时抓取 https://beijing.qianlong.com/ 与 https://edu.qianlong.com/。
# 设置 QIANLONG_BASE_URL 后改为只抓取该入口。
QIANLONG_BASE_URL=
QIANLONG_TIMEOUT=15
QIANLONG_DELAY=0.5
QIANLONG_PAGES=3
# QIANLONG_MAX_PAGES is an older alias; prefer QIANLONG_PAGES.
QIANLONG_EXISTING_CONSECUTIVE_STOP=5

# Xinhua / 新华网北京频道
# 列表项日期早于"今天减 N 天"的直接丢弃；每轮都生效，
# 防止首轮把各栏目的历史稿件一次性灌进库里。
XINHUA_LOOKBACK_DAYS=3

# Stdaily / 科技日报（滚动新闻）
# 翻页深度由 crawl 的 pages 参数控制（每页约 16 条）；此窗口按列表行
# 发布时间过滤，防止补历史稿（大 pages）时把窗口外的旧稿灌进库里。
STDAILY_LOOKBACK_DAYS=3

# Bbtnews / 北京商报（教育频道）
# 翻页深度由 crawl 的 pages 参数控制（每页约 30 条）；此窗口按列表行/
# URL 日期过滤，防止补历史稿（大 pages）时把窗口外的旧稿灌进库里。
BBTNEWS_LOOKBACK_DAYS=3

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
FEISHU_ARCHIVE_ALLOWED_OPEN_IDS=replace-with-open-id
```

`FEISHU_RECEIVE_ID_TYPE` 可选：`open_id`、`user_id`、`union_id`。

`FEISHU_ARCHIVE_ALLOWED_OPEN_IDS` 是允许通过机器人私聊自动新增报送存档的飞书 `open_id` 白名单，多个值用英文逗号分隔。未设置时，如果 `FEISHU_RECEIVE_ID_TYPE=open_id`，系统会将现有 `FEISHU_RECEIVE_ID` 作为唯一白名单用户。白名单为空时，`feishu-archive-bot` 会拒绝启动。

接收存档还需要在飞书自建应用中开启机器人能力、长连接订阅和 `im.message.receive_v1` 事件。部署步骤见 [`docs/feishu_archive_bot.md`](feishu_archive_bot.md)。

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

LLM_API_KEY=replace-with-your-llm-api-key
LLM_ALLOWED_HOSTS=openrouter.ai,api.deepseek.com,open.bigmodel.cn

CONSOLE_BASIC_USERNAME=admin
CONSOLE_BASIC_PASSWORD=replace-with-a-strong-password
```
