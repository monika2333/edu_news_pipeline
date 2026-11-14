# 进行中与待办

开始新任务前先过一遍这里，避免撞车或重复探索。事项完成或作废后从这里删除，闭环细节进 `archive/`。

## 进行中

（暂无。）

## 未排期候选

- **《北京晚报》电子报接入**（未排期，待用户决定）：委托里提到「接下来还要接《北京晚报》」。按 2026-11-14 的合并口径，只需写 adapter + 在 `SOURCE_CATALOG` 登记一行（`epaper=True`）、在设置页「电子报」打开开关，就会随 `EduNews_Epapers` 每天五轮一起跑，**不要新增脚本或计划任务**；需要先确认站点形态（是否同 bjrb 的「只出当天刊期」、未出刊时返回什么），并给 bjrb 那套「尚未上线」判定补一条对应实现。
- **北京号·客户端栏目分类页的历史补抓**（未排期，待用户决定）：2026-11-14 接入的「教育」栏目（`static.bjd.com.cn/static/category/`）检索接口 `totalElements=692`，但每轮只抓最新 1 页（10 条），历史稿不会自动入库。要补就一次性跑 `crawl --sources beijinghao --pages N`（`existing_ids` 只过滤不截停翻页，可安全重跑）；补不补、补多久是内容决策，别自动跑。
- **劳动午报 7 周空档是否补录**（未排期，待用户决定）：ldwb 抓取自 2026-09-21 21:45 后全灭，已修复（见 delivery-log 2026-11-12），但 2026-09-22 ~ 2026-11-11 的报纸没有入库。`scripts/tools/backfill_ldwb_issue.py --date YYYY-MM-DD` 现已可用（先过 WAF，`--dry-run` 实测 46 篇/期），按 46 篇/天粗算约 2000+ 篇、会被 hash-primary/score/summarize 正常流水线消费。要不要补、补多久是内容决策，别自动跑。
- 外观下拉严格居中（未排期）：原生 select 的选中值默认在箭头左侧文本区居中，用户 2026-11-11 已否决 `text-align` / 自绘箭头补丁；若要严格居中需换自定义下拉。
- article 级 4xx 死信可见性（未排期）：`beijing_gate_fail_count` 达到上限后稿件保持 `pending_beijing_gate` 但不再被 `fetch_beijing_gate_candidates` 取到，目前只有 `GATE EXHAUSTED` 日志，控制台/巡检没有入口；是否加状态或运维查询待定。
- 检索按「消息来源」筛选（未排期）：目前只能按抓取来源 `source` 搜；`news_summaries.llm_source`（LLM 识别的消息来源，5331 篇里 3277 篇与 `source` 不同）与人工来源不可检索。若要做，应作为独立维度（来源过滤下拉），不要塞进自由文本 AND 组（理由见 `decisions.md`「检索语义」）。
- 爬虫 adapter 整理后续（需先补黄金快照）：统一各站标题提取（现 9 处实现）、btime 结尾锚定后缀收编进 `strip_site_suffix`、发布时间解析共享化。
- 提示词试跑的后续候选（未排期）：批量/多文章对比、从筛选页文章直接发起重评、后台异步试跑；当前只有设置页单篇同步试跑。
- 报送存档录入的期号连续性提示（未排期）：录入时若期号与「最近归档 + 天数差」推算不一致，给一条不阻断的提示。没做的原因：`/api/submission-archive/parse` 是纯文本解析、有测试锁定不碰库，加校验得改成保存成功后提示（细节见 archive/2026-10-21-预览题头期号推算.md）。
- `raw_articles` 中 stdaily 存量 10/16 篇 content_markdown 仍含 `![](https://www.stdaily.com/...)` 图片语法（代码已翻转剥图，只影响此后抓取）——是否 UPDATE 清洗待用户决定；bbtnews 库内无存量。

## 尚未实施的配置方案

- `.gitignore` 增量合并：可显式补上 `.pytest_cache/`、`.ruff_cache/`（两者目前靠各自目录内的 `.gitignore` 自我忽略，并非漏掉）。
