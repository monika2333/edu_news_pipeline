# 教育新闻智能流水线

## 项目概览
- 处理今日头条 AuthorFetch 抓取的教育类新闻，从导入、补全、摘要、打分到导出成批次文本。
- 默认使用本地 SQLite (`articles.sqlite3`) 存储，也可切换至 Supabase 托管数据库。
- 关键词筛选列表存放于 `education_keywords.txt`，用于挑选需要摘要的文章。

## 数据流程总览
1. 导入素材：`tools/import_authorfetch_to_sqlite.py` 从 Excel/TXT 整理为结构化数据。
2. 回填正文：`tools/fill_missing_content.py` 根据原始链接补全缺失的正文。
3. 关键词筛选与摘要：`tools/summarize_news.py` 触发 LLM 生成摘要与来源标注。
4. 相关度打分：`tools/score_correlation_fulltext.py` 生成 0-100 的相关度分值。
5. 导出高相关内容：`tools/export_high_correlation.py` 组合摘要并记录导出批次。
6. 清理：`tools/cleanup_authorfetch_outputs.py` 归档/删除已处理的原始文件。

`run_pipeline.py` 将上述步骤串联成可配置的流水线，可根据命令行参数跳过或限制各阶段。

## 后端模式
- **SQLite (默认)**：简单部署，单机存储，适合开发或离线处理。
- **Supabase**：在 `.env.local` 配置 `SUPABASE_URL`、`SUPABASE_ANON_KEY`（或服务密钥）、`SUPABASE_DB_PASSWORD` 后启用。也可以通过 `--backend supabase` 显式指定。
- Supabase 入口脚本位于 `tools/*_supabase.py`，目标 schema 定义在 `supabase/schema.sql`。

## 环境配置
- Python 3.10+，建议创建虚拟环境并安装 `requirements.txt`。
- 必需环境变量
  - `SILICONFLOW_API_KEY`（或兼容的 OpenAI API Key）。
  - `MODEL_NAME`、`CONCURRENCY` 等可选调优参数。
- `load_dotenv_simple` 会自动读取以下文件（若存在）：`.env`, `.env.local`, `config/abstract.env`。
- Git 忽略建议在 `.gitignore` 中包含 `.env.local` 等敏感文件。

## 快速开始
```
python run_pipeline.py --db articles.sqlite3 --keywords education_keywords.txt
```
常用选项：
- `--import-src`：AuthorFetch 原始目录（默认 `AuthorFetch/`）。
- `--fill-limit` / `--summarize-limit`：限制处理条数。
- `--summarize-concurrency` / `--score-concurrency`：控制并发。
- `--min-score`：导出阶段的相关度阈值。
- `--export-report-tag`：输出批次标签，建议早晚报区分。
- `--export-skip-exported`、`--export-record-history`：控制增量导出与历史记录。

## 分阶段执行
```
python tools/import_authorfetch_to_sqlite.py --src AuthorFetch --db articles.sqlite3
python tools/fill_missing_content.py --db articles.sqlite3 --limit 200 --delay 1.5
python tools/summarize_news.py --db articles.sqlite3 --keywords education_keywords.txt
python tools/score_correlation_fulltext.py --db articles.sqlite3 --concurrency 5
python tools/export_high_correlation.py --db articles.sqlite3 --output outputs/high_correlation_summaries.txt --min-score 60 --report-tag 2025-09-20-ZM
python tools/cleanup_authorfetch_outputs.py --src AuthorFetch --db articles.sqlite3 [--apply]
```
- 导出脚本会根据 `export_history` 去重，`--no-export-skip-exported` 可重新导出全部。
- 分类与排序策略在 `tools/export_high_correlation.py` 中可调，Dry Run 会打印分组统计。

## 浏览器控制台
- `streamlit run tools/web/app.py` 启动自助面板，提供指标监控、批次导出、流水线快捷按钮等能力。
- 首次运行需要安装 `streamlit`：`pip install streamlit`。

## Supabase 结构与映射
- `raw_articles`：对应原本的 `articles` 表，建议使用 `article_id`+URL hash 作为 `hash`。
- `filtered_articles`：承载摘要、相关度、关键词及处理元数据。
- `brief_batches` / `brief_items`：记录导出批次与摘要明细。
- 推荐通过数据库适配层（如 `services/db_adapter.py`）封装 CRUD，便于在 SQLite 与 Supabase 之间切换。
- 注意 Supabase RLS 与速率限制：服务调用应使用服务密钥并实现限流、重试。

## 常见问题
- 首次运行若不存在数据库会自动创建。
- PowerShell 若出现编码问题，可先执行 `chcp 65001`。
- 导出前建议 Dry Run 并备份 `AuthorFetch/` 原始文件。

## 迁移路线图
1. 抽象数据库访问：创建统一的 `DbAdapter` 接口，现有脚本改为通过适配层访问。
2. 实现 Supabase 适配器：封装 sources/raw_articles/filtered_articles/brief_* 的增删查改与幂等写入。
3. 逐步替换脚本：依次改造导入、摘要、打分、导出脚本以调用 Supabase 适配器。
4. 配置与凭据管理：新增 `.env.example`，完善 `.gitignore`，统一加载配置。
5. 数据迁移：编写一次性脚本，将 `articles.sqlite3` 中的历史数据同步至 Supabase。
6. 验证与运维：编写关键接口集成测试，在测试环境完整跑通流水线后再切换生产。

## 单条新闻快速处理
- `python tools/process_single_news.py` 进入交互模式，粘贴今日头条或北京日报分享内容。
- 也可直接传入链接：`python tools/process_single_news.py "https://m.toutiao.com/is/XXXX/"`。
