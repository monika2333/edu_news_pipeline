# 进行中与待办

开始新任务前先过一遍这里，避免撞车或重复探索。事项完成或作废后从这里删除，闭环细节进 `archive/`。

## 进行中

（暂无。）

## 未排期候选

- article 级 4xx 死信可见性（未排期）：`beijing_gate_fail_count` 达到上限后稿件保持 `pending_beijing_gate` 但不再被 `fetch_beijing_gate_candidates` 取到，目前只有 `GATE EXHAUSTED` 日志，控制台/巡检没有入口；是否加状态或运维查询待定。
- 检索按「消息来源」筛选（未排期）：目前只能按抓取来源 `source` 搜；`news_summaries.llm_source`（LLM 识别的消息来源，5331 篇里 3277 篇与 `source` 不同）与人工来源不可检索。若要做，应作为独立维度（来源过滤下拉），不要塞进自由文本 AND 组（理由见 `decisions.md`「检索语义」）。
- 爬虫 adapter 整理后续（需先补黄金快照）：统一各站标题提取（现 9 处实现）、btime 结尾锚定后缀收编进 `strip_site_suffix`、发布时间解析共享化。
- 提示词试跑的后续候选（未排期）：批量/多文章对比、从筛选页文章直接发起重评、后台异步试跑；当前只有设置页单篇同步试跑。
- 报送存档录入的期号连续性提示（未排期）：录入时若期号与「最近归档 + 天数差」推算不一致，给一条不阻断的提示。没做的原因：`/api/submission-archive/parse` 是纯文本解析、有测试锁定不碰库，加校验得改成保存成功后提示（细节见 archive/2026-10-21-预览题头期号推算.md）。
- `raw_articles` 中 stdaily 存量 10/16 篇 content_markdown 仍含 `![](https://www.stdaily.com/...)` 图片语法（代码已翻转剥图，只影响此后抓取）——是否 UPDATE 清洗待用户决定；bbtnews 库内无存量。

## 尚未实施的配置方案

- `.gitignore` 增量合并：可显式补上 `.pytest_cache/`、`.ruff_cache/`（两者目前靠各自目录内的 `.gitignore` 自我忽略，并非漏掉）。
