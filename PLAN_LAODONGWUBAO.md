# 劳动午报接入与调度规划

本文档汇总了“劳动午报”数据源接入 edu news pipeline 的整体规划、调度策略以及验证步骤，便于在 `feature/laodongwubao-source` 分支上落地实现。

## 1. 适配层与数据模型（已完成）

- **新建 `src/adapters/http_laodongwubao.py`**
  - 把 `laodongwubao_crawl/crawl_ldwb.py` 的能力收编为 adapter：获取最新期刊、枚举版面、抓取文章、解析 founder-content。
  - 输出统一结构：`article_id`, `title`, `content_markdown`, `publish_time_iso`, `source='劳动午报'`（`source` 字段固定为“劳动午报”，不再使用作者信息；版面仅用于去重与日志，可在解析阶段丢弃，不写入数据库）。
  - `article_id` 生成规则：基于 issue 日期 + 版面 + 文章 slug，如 `laodongwubao:2024-11-12:A01:content_123456`，确保多次抓取不会重复入库。
  - 时间戳：若页面只提供日期，补齐 `publish_time_iso = "YYYY-MM-DDT00:00:00+08:00"`（以当天零点为默认），保持和库中其它记录的 ISO8601 一致。
  - 配置化：暴露 `verify_tls`, `request_timeout`, `user_agent` 等参数，默认关闭 TLS 校验但允许通过环境变量开启。
- **单元测试**
- 使用 `laodongwubao_crawl/latest_issue_sample.json` 中的样例 HTML，新增 `tests/adapters/test_http_laodongwubao.py`。
- 覆盖页面列表解析、文章解析、`article_id` 生成，确保未来网站结构轻微变化时有报警。
- 现有 `laodongwubao_crawl/` 文件夹包含已跑通的参考脚本与样例数据，用于本次开发；在 adapter 正式完成并迁移逻辑后，需要彻底删除该目录以免混淆（在合并前清理）。

## 2. Crawl Worker 集成（已完成）

- 在 `src/workers/crawl_sources.py` 中注册 `laodongwubao` 分支：
  - 复用现有 `collect -> upsert feed -> fetch missing content` 模板，按 adapter 输出写入 `raw_articles`。
  - 因为报纸一次性抓完最新一期，可忽略 `pages`，默认跑满；若 CLI 指定 `--limit`，则在 enqueue 阶段截断，便于测试。
  - 入口：`python -m src.cli.main crawl --sources laodongwubao` 或 `CRAWL_SOURCES=laodongwubao python -m scripts.run_pipeline_once --steps crawl ...`。
- 文档更新：README “Pipeline Overview / Crawl” 中补充该源描述、CLI 示例。

## 3. 调度方案

- 新增 `scripts/run_ldwb_daily.ps1`（已完成）：
  - 功能：每日一次执行 `crawl -> hash-primary -> score -> summarize -> external-filter`，触发源为劳动午报（无需在该任务内执行 `export`，待统一导出时与其他来源一并处理）。
  - 关键实现：
    1. 设置 `CRAWL_SOURCES=laodongwubao` 环境变量。
    2. 通过 `python -m scripts.run_pipeline_once --steps crawl hash-primary score summarize external-filter --trigger-source scheduler-ldwb --continue-on-error` 调用主流水线。
    3. 使用独立 lock 文件（如 `locks/pipeline_ldwb.lock`），日志写入 `logs/pipeline_ldwb_YYYYMMDD.log`。
  - 是否加 `export`：视业务需要决定当天中午是否即刻入日报；默认交由晚间导出统一处理。
- 计划任务（已完成）：
  - 在 `scripts/tasks/` 下新增 `register-ldwb-midday.ps1`，注册 Windows 计划任务，触发时间改为每日 18:50。
  - 任务动作：运行 `run_ldwb_daily.ps1`，并设置失败重试（例如 15 分钟间隔，最多 2 次）。
- 备选方案：若不想新增脚本，可在现有 `run_pipeline_hourly.ps1` 中检测当小时 `== 12` 时临时追加 `laodongwubao` 源，但独立任务更易排障、日志也更清晰。

## 4. 验证与上线步骤

1. **本地验证**
   - `CRAWL_SOURCES=laodongwubao python -m scripts.run_pipeline_once --steps crawl hash-primary summarize score --no-metadata`
   - 确认控制台输出无错误，`database/raw_articles` 中 `source='laodongwubao'` 记录被写入。
2. **数据库核查**
   - 检查 `raw_articles`/`filtered_articles`/`primary_articles` 中劳动午报记录数量、字段完整性（特别是 `publish_time_iso`）。
3. **调度演练**
   - 手动执行 `scripts/run_ldwb_daily.ps1 -Python python`（或指定虚拟环境）验证锁、日志路径是否正确。
   - 执行 `scripts/tasks/register-ldwb-midday.ps1` 并在任务计划程序中确认触发器、运行身份。
4. **观测期**
   - 上线后前几天重点关注 `logs/pipeline_ldwb_*`，若发现重复/漏抓，调优 `article_id` 或页面解析策略。
   - 需要补历史期刊时，可扩展 adapter 接受 `issue_date` 参数，或者写独立 backfill 脚本。

## 5. 后续可能的增强

- 为劳动午报构建版面/栏目白名单，避免不相关内容进入数据库。
- 结合报纸特性（无实时更新），在数据库中记录 issue date，便于导出时做“当天报纸特辑”。
- 如果未来要做差分抓取，可在 adapter 内维护 issue cache，仅对新增版面执行抓取以减少带宽。

---
若需对计划内容调整或扩展，请在此文件更新并提 PR，保持信息同步。
