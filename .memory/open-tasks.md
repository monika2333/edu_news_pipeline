# 进行中与待办

开始新任务前先过一遍这里，避免撞车或重复探索。事项完成或作废后从这里删除，闭环细节进 `archive/`。

## 进行中

### 评分提示词进设置页（原型待验收）
- worktree `.worktrees/worktree-score-prompt-settings`（分支同名，HEAD b820c0c），全量 1315 passed。
- 核心设计已实现：app_settings 新分区 `external_filter_prompts`（四键 external/internal × positive/negative，**空串=用 config/prompts 内置版本**，迁移种子全空串=零行为变化）；生效模板进 BusinessConfig 随轮冻结；`resolve_external_filter_prompt` 返回 (模板, 版本标签)，自定义版本落库为 `custom:{分区版本号}`；试算端点 POST `/api/admin/settings/external_filter_prompts/test`（复用生产 call_external_filter_model、retries=1、不写流水线）+ GET `.../sample?article_id=` 预填。
- 用户反馈迭代（b820c0c）：文本框必须预填生效中的提示词全文；保存时与内置模板 strip 后全等的文本存回空串（没改过不算自定义）；每块有「还原为内置版本」按钮。
- **待用户拍板的三个分歧点**：① 试算同步等待 LLM（reasoning 模型可能 30s+）是否可接受，要不要改后台任务+轮询；② prompt_version 记 `custom:{分区版本号}` 的形式；③ 要不要从人工筛选页文章上直接发起「用当前提示词重评这篇」（改动面大，未做）。
- 合入需用户明确授权；继续前先 `git log` 看 worktree 分支状态。

## 部署侧待办（代码已就位，生产环境要手工配合）

- **每小时来源列表不含 stdaily / bbtnews**：DB 的 app_settings `crawl_sources` 分区还是旧值，需在控制台设置页把两个源加入每小时轮换（bbtnews 环境变量 `BBTNEWS_LOOKBACK_DAYS` 默认 3）。
- **QIANLONG_PAGES**：代码默认已收紧为 1 页（72f5715）；生产 env 若还配着 `QIANLONG_PAGES=3`，需手动删除该行才会回落。
- bjrb 存量旧文标题（缺副标题）不会自愈：bjrb 只爬当天刊期、detail 只补缺正文的行；生产库要修需单独 SQL。
- btime 已入库的旧视频稿正文仍是「标题复读」垃圾（bba1c1b 只修复此后抓取），未批量重抓。
- btime 新频道 `https://record.btime.com/show?uid=2874176`：用户 2026-10-01 计划按「一栏目一账号」在控制台添加（已核实每频道每轮只抓最新 ≤10 条，不会灌档）。

## 未排期候选

- 爬虫 adapter 整理后续（需先补黄金快照）：统一各站标题提取（现 9 处实现）、btime 结尾锚定后缀收编进 `strip_site_suffix`、发布时间解析共享化。
- `raw_articles` 中 stdaily 存量 10/16 篇 content_markdown 仍含 `![](https://www.stdaily.com/...)` 图片语法（代码已翻转剥图，只影响此后抓取）——是否 UPDATE 清洗待用户决定；bbtnews 库内无存量。

## 臧宝的文件管理建议（配置文件部分未实施）

`臧宝的文件管理建议/` 目录（未跟踪）已评审完毕，2026-10-20 用户拍板：
- `.audit/`、`.discussion/`、`.incidents/` 三个过程文档目录**不需要**；`.memory/` 已按本目录的形态引入（ thematic 平铺 + archive，未照搬其模块 README 三层强制归档规则）。
- `.dockerignore` 放弃（项目无 Dockerfile）。
- 尚未实施的配置方案：① `.gitignore` 增量合并（可补上确实漏掉的 `.pytest_cache/`、`.ruff_cache/`）；② `.editorconfig` 裁剪后只留 `[*]` 段（UTF-8 + LF），删掉 C#/.NET 噪音；③ `.gitattributes`（`* text=auto eol=lf`）启用前须评估存量 CRLF 并 renormalize，否则全仓假 diff。
