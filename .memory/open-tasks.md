# 进行中与待办

开始新任务前先过一遍这里，避免撞车或重复探索。事项完成或作废后从这里删除，闭环细节进 `archive/`。

## 进行中

- **评分排除名单（黑名单）**：2026-10-20 用户拍板要做，委托书已移到桌面 `委托书-评分排除名单.md`（未入库），交由另一窗口/会话按委托书执行。契约：新增 `app_settings.score_exclusion_keywords`；排除优先于加分词典，命中不调用模型、`status=filtered_out`、`score=NULL`、`score_details.skip_reason=keyword_excluded`；UI 放在「加分词典」页签表格下方的独立卡片（不新增页签，即时保存，两个分区版本独立）。完成并合并后把本项移入交付流水。

## 部署侧待办（代码已就位，生产环境要手工配合）

- **每小时来源列表不含 stdaily / bbtnews**：DB 的 app_settings `crawl_sources` 分区还是旧值，需在控制台设置页把两个源加入每小时轮换（bbtnews 环境变量 `BBTNEWS_LOOKBACK_DAYS` 默认 3）。
- **QIANLONG_PAGES**：代码默认已收紧为 1 页（72f5715）；生产 env 若还配着 `QIANLONG_PAGES=3`，需手动删除该行才会回落。
- bjrb 存量旧文标题（缺副标题）不会自愈：bjrb 只爬当天刊期、detail 只补缺正文的行；生产库要修需单独 SQL。
- btime 已入库的旧视频稿正文仍是「标题复读」垃圾（bba1c1b 只修复此后抓取），未批量重抓。
- btime 新频道 `https://record.btime.com/show?uid=2874176`：用户 2026-10-01 计划按「一栏目一账号」在控制台添加；已核实每频道每轮只抓最新 ≤10 条，不会灌档（结论见 [archive/2026-10-01-北京时间抓取量与新频道.md](archive/2026-10-01-北京时间抓取量与新频道.md)）。

## 未排期候选

- 爬虫 adapter 整理后续（需先补黄金快照）：统一各站标题提取（现 9 处实现）、btime 结尾锚定后缀收编进 `strip_site_suffix`、发布时间解析共享化。
- `raw_articles` 中 stdaily 存量 10/16 篇 content_markdown 仍含 `![](https://www.stdaily.com/...)` 图片语法（代码已翻转剥图，只影响此后抓取）——是否 UPDATE 清洗待用户决定；bbtnews 库内无存量。

## 臧宝的文件管理建议（配置文件部分未实施）

`臧宝的文件管理建议/` 目录（未跟踪）已评审完毕，2026-10-20 用户拍板：
- `.audit/`、`.discussion/`、`.incidents/` 三个过程文档目录**不需要**；`.memory/` 已按本目录的形态引入（thematic 平铺 + archive，未照搬其模块 README 三层强制归档规则）。
- `.dockerignore` 放弃（项目无 Dockerfile）。
- 尚未实施的配置方案：① `.gitignore` 增量合并（可显式补上 `.pytest_cache/`、`.ruff_cache/`；两者目前靠各自目录内的 `.gitignore` 自我忽略，并非漏掉）；② `.editorconfig` 裁剪后只留 `[*]` 段（UTF-8 + LF），删掉 C#/.NET 噪音；③ `.gitattributes`（`* text=auto eol=lf`）启用前须评估存量 CRLF 并 renormalize，否则全仓假 diff。
