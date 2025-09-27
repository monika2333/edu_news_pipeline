# 每日新闻数据处理流水线

本仓库整理了从 AuthorFetch 原始采集数据的导入、关键词筛选、LLM 摘要生成、相关度打分及导出的完整流程。最终数据存储在仓库根目录的 `articles.sqlite3` 中，关键词筛选文件位于 `education_keywords.txt`。


## Supabase 后端支持

若设置了 `SUPABASE_URL` 与对应的 API Key，流水线会自动切换到 Supabase 模式，也可显式指定：

```
python run_pipeline.py --backend supabase --import-src AuthorFetch --keywords education_keywords.txt
```

Supabase 模式使用以下脚本：
- `tools/import_authorfetch_supabase.py`
- `tools/fill_missing_content_supabase.py`
- `tools/summarize_supabase.py`
- `tools/score_correlation_supabase.py`
- `tools/export_high_correlation_supabase.py`
- `tools/cleanup_authorfetch_supabase.py`

请在 `.env.local` 中配置 `SUPABASE_URL`、`SUPABASE_ANON_KEY` 或服务密钥以及数据库密码。
## 一键执行

按默认配置完成导入、摘要与导出：

    python run_pipeline.py --db articles.sqlite3 --keywords education_keywords.txt

常用选项：
- `--import-src`：AuthorFetch 源目录（默认 `AuthorFetch/`）
- `--fill-limit` / `--summarize-limit`：限制处理的文章数量
- `--summarize-concurrency` / `--score-concurrency`：调整并发
- `--min-score`：导出时的相关度阈值
- `--cleanup-apply`：清理阶段执行真实删除（默认开启，可通过 `--no-cleanup-apply` 只做 Dry Run）
- `--export-report-tag`：导出批次标签，用于标记同日的早/晚报等
- `--export-skip-exported` / `--no-export-skip-exported`：是否跳过历史上已导出的文章
- `--export-record-history` / `--no-export-record-history`：导出后是否写入历史记录表

> **增量导出说明：**
> - `tools/export_high_correlation.py` 默认会将导出的 `article_id` 写入数据库中的 `export_history` 表，同时在下一次导出时自动跳过这些历史记录。
> - 建议在早晚两次运行流水线时分别指定不同的标签，例如：
>
>       python run_pipeline.py --export-report-tag 2025-09-20-ZB
>       python run_pipeline.py --export-report-tag 2025-09-20-ZM
>
>   这样即可确保晚报只输出上午之后新增的新闻。
> - 如需重新导出全部内容，可添加 `--no-export-skip-exported`；若仅做试运行且不记录，请再加 `--no-export-record-history`。

## 浏览器控制台

也可以使用 Streamlit 提供的 Web 控制台在浏览器中操作：

    streamlit run tools/web/app.py

功能包括：
- 关键指标面板（文章数量、摘要缺失情况等）
- 导出历史与现有输出文件的预览/下载
- 一键运行完整流水线，以及逐个阶段的按钮（导入、回填、摘要、打分、导出）
- 在界面中切换标签、是否跳过历史导出、是否执行清理删除等开关

首次运行需要在当前 Python 环境中安装 `streamlit`（例如 `pip install streamlit`）。

## 手动分步

1. 导入 AuthorFetch 数据

        python tools/import_authorfetch_to_sqlite.py --src AuthorFetch --db articles.sqlite3

   Excel 表第一行自动识别列名，TXT 正文会取同目录下最长的文件作为正文写入 `articles` 表。

2. 回填缺失正文

        python tools/fill_missing_content.py --db articles.sqlite3 --limit 200 --delay 1.5

   使用 `original_url` 或 `article_id` 请求源站，失败会自动跳过。

3. 关键词过滤后的摘要生成

        python tools/summarize_news.py --db articles.sqlite3 --keywords education_keywords.txt

   仅对包含关键词的文章调用 SiliconFlow/OpenAI 接口生成摘要，并写入 `news_summaries` 表。

4. 相关度打分

        python tools/score_correlation_fulltext.py --db articles.sqlite3 --concurrency 5

   生成 0-100 的相关度分数，写入 `correlation` 列。

5. 导出高相关度摘要（含去重机制）

        python tools/export_high_correlation.py --db articles.sqlite3 --output outputs/high_correlation_summaries.txt --min-score 60 --report-tag 2025-09-20-ZM

   - 默认跳过已在 `export_history` 表中出现的文章。
   - 输出文件会包含标题、摘要以及 LLM 来源（若有），以空行分隔，并根据标签生成如 `high_correlation_summaries_20250920_ZB.txt` 的文件名。
   - 再次导出同批次时可用 `--no-skip-exported` 重新生成全部内容。

### å¯¼åºåç»æåºç­ç¥

- `tools/export_high_correlation.py` ä¼åæâå¸å§æå§ â ä¸­å°å­¦ â é«æ ¡ â å¶ä»ç¤¾ä¼æ°é»âçé¡ºåºåç»ï¼åå¨æ¯ä¸ªåç»åä¿æåæçç¸å³åº¦éåºã
- åç±»åºäº `source`ã`source_LLM`ãæ é¢ãæè¦ä»¥åæ­£æå³é®è¯çå¯åå¼å¹éï¼å¤å½ä¸­æ¶ä»¥ä¼åçº§é«çåç»ä¸ºåã
- å³é®è¯è¡¨å¯å¨ `tools/export_high_correlation.py` ä¸­è°æ´ï¼Dry Run æ¨¡å¼ä¼æå°ååç»ç»è®¡ï¼ä¾¿äºå¿«éæ ¡ååç±»ææã



6. 清理 AuthorFetch 目录

        python tools/cleanup_authorfetch_outputs.py --src AuthorFetch --db articles.sqlite3
        python tools/cleanup_authorfetch_outputs.py --src AuthorFetch --db articles.sqlite3 --apply

   默认 Dry Run，带 `--apply` 后执行删除，可配合 `--allow-empty-content` 清理空正文记录。

## 目录结构参考

- `AuthorFetch/`：原始 Excel 与正文目录
- `articles.sqlite3`：主 SQLite 数据库（`articles`、`news_summaries`、`export_history` 等表）
- `education_keywords.txt`：关键词列表
- `outputs/high_correlation_summaries.txt`：导出的文本

## 常见问题

- 初次运行若数据库不存在会自动创建。
- 需要在 `.env` 或 `config/abstract.env` 中提供 `SILICONFLOW_API_KEY`，并可设置 `MODEL_NAME`、`CONCURRENCY`。
- PowerShell 下如出现编码问题，可先执行 `chcp 65001`。
- 建议在正式导出前执行 Dry Run，并检查原始抓取目录内容是否已备份。

## 快速处理单条新闻

当需要快速处理单个新闻链接时，可以使用 `tools/process_single_news.py`：

### 交互式模式（推荐）

    python tools/process_single_news.py

脚本会提示你粘贴新闻分享内容，支持直接粘贴今日头条/北京日报网分享的内容：

```
【AI赋能､人人参与!北京市2025年北京市中小学科学节(通... - 今日头条】
点击链接打开👉 https://m.toutiao.com/is/YEexSWXbGwQ/ YEexSWXbGwQ` dvX:/ m@q.EH :0am
复制此条消息,打开｢今日头条APP｣或｢今日头条极速版APP｣后直接查看~
```

粘贴完成后按回车（空行）结束输入，脚本会：
1. 自动提取有效链接
2. 调用 `toutiao_fetch.py` 获取内容并保存到数据库
3. 调用 `summarize_news.py` 生成摘要和来源识别
4. 跳过相关度打分步骤
5. 直接输出标题、摘要和来源信息到控制台

### 命令行模式

    python tools/process_single_news.py "https://m.toutiao.com/is/YEexSWXbGwQ/"

支持的链接格式：
- `https://m.toutiao.com/is/xxxxx/`（分享短链）
- `https://www.toutiao.com/article/xxxxxx/`（标准链接）
- `https://m.toutiao.com/ixxxxxx/`（移动端链接）
- 北京日报网链接

## 相关脚本

- 数据抓取：`tools/toutiao_fetch.py`
- 导入：`tools/import_authorfetch_to_sqlite.py`
- 正文补全：`tools/fill_missing_content.py`
- 摘要与打分：`tools/summarize_news.py`、`tools/score_correlation_fulltext.py`
- 导出：`tools/export_high_correlation.py`
- 清理：`tools/cleanup_authorfetch_outputs.py`
- 一键流水线：`run_pipeline.py`
- **单条新闻快速处理：`tools/process_single_news.py`**
