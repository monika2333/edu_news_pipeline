喜欢你这种“把程序想成一群小工人”的感觉～这正是把项目做大做稳的关键思路 👷‍♀️👷‍♂️

你现在的结构**已经能用**，但为了让“多 worker 并行 + 后续好扩展”，我建议做一版**最小代价的整理**：不推倒重来，只是挪挪位置、定几个规矩。下面给你一个“**一步步落地的梳理方案**”。

# 先给结论（最小改动版目录）

把核心 Python 放进一个包里（`src/`），把“工人（workers）”和“适配器（adapters）”分开；`tools/` 里零散脚本并入 workers；保留你已有的 `supabase/` 与 `outputs/`。

```
edu_news_pipeline/
│  .env.local
│  .gitignore
│  education_keywords.txt
│  README.md
│  requirements.txt
│  run_pipeline.py                  # 兼容旧脚本，内部调用新入口
│
├─outputs/                          # 导出成品（继续保留）
├─data/                             # 临时/缓存/下载
├─replace-news-JGW/                 # 这是独立小工具，先保持原样
├─supabase/
│   ├─schema.sql
│   └─migrations/
│
├─src/
│  ├─__init__.py
│  ├─config.py                      # 读取 .env / 常量
│  ├─domain/                        # 领域层：状态、枚举、打分规则等
│  │    __init__.py
│  │    states.py                   # process_state 常量/枚举
│  │    scoring.py                  # 打分规则（可纯函数）
│  │    templates.py                # 简报文本模板
│  │
│  ├─adapters/                      # 和外界交互的一切（DB、HTTP、LLM）
│  │    __init__.py
│  │    db_supabase.py              # = 你现在的 supabase_adapter.py
│  │    http_toutiao.py             # = toutiao_scraper.py 的 HTTP 封装
│  │    llm_summary.py              # = summarize_supabase.py 的 LLM 调用
│  │
│  ├─workers/                       # 小工人：每个工人一个清晰入口
│  │    __init__.py
│  │    crawl_toutiao.py            # 抓取工人（写入 process_state=NEW）
│  │    summarize.py                # 摘要工人（NEW -> SUMMARIZED）
│  │    score.py                    # 打分工人（SUMMARIZED -> SCORED）
│  │    export_brief.py             # 导出工人（SCORED -> brief_items）
│  │
│  └─cli/                           # 统一命令行入口（可选）
│       __init__.py
│       main.py                     # edu-news <cmd> … （见下）
│
└─tests/                            # 以后再加也行，先留坑
```

> 你现有的 `tools/` 里的 `summarize_supabase.py / score_correlation_supabase.py / export_high_correlation_supabase.py / toutiao_scraper.py / supabase_adapter.py`，分别移动到 `src/workers/` 与 `src/adapters/`。
> `__pycache__/` 全部不用管，`.gitignore` 忽略即可。

---

## 为什么这样放？

* **按职责分层**：

  * *adapters* 只管“怎么拿数据/怎么写库/怎么调 API”；
  * *workers* 只做一件事：领取任务 → 处理 → 改状态；
  * *domain* 放纯规则、常量，便于测试和复用。
* **并行更安全**：每个 worker 独立跑，不会互相 import 一堆侧效应。
* **后续扩源不乱**：你要加一个新源（比如“教育部官网”），只需 `adapters/http_moe.py` + `workers/crawl_moe.py`，其余不用动。

---

# 你可以直接粘贴用的“统一命令行入口”

有了它，你本地、服务器、计划任务都统一用一种调用方式。

`src/cli/main.py`

```python
import argparse
from src.workers.crawl_toutiao import run as crawl_tt
from src.workers.summarize import run as summarize_run
from src.workers.score import run as score_run
from src.workers.export_brief import run as export_run

def main():
    p = argparse.ArgumentParser("edu-news")
    sub = p.add_subparsers(dest="cmd", required=True)

    s1 = sub.add_parser("crawl");       s1.add_argument("--limit", type=int, default=50)
    s2 = sub.add_parser("summarize");   s2.add_argument("--limit", type=int, default=50)
    s3 = sub.add_parser("score");       s3.add_argument("--limit", type=int, default=100)
    s4 = sub.add_parser("export");      s4.add_argument("--date")  # YYYY-MM-DD，可为空=今天

    args = p.parse_args()
    if args.cmd == "crawl":      crawl_tt(limit=args.limit)
    elif args.cmd == "summarize": summarize_run(limit=args.limit)
    elif args.cmd == "score":     score_run(limit=args.limit)
    elif args.cmd == "export":    export_run(date=args.date)

if __name__ == "__main__":
    main()
```

`run_pipeline.py`（兼容旧入口，内部转发）：

```python
from src.cli.main import main
if __name__ == "__main__":
    main()
```

> 以后你只要 `python run_pipeline.py crawl --limit 100` 就可以拉起“抓取工人”，`summarize/score/export` 同理。服务器上也可以把这四个命令设成**不同的计划任务**，实现“同步增补”。

---

## worker 的最小模板（并发安全领取）

`src/workers/summarize.py`（示例）

```python
from src.adapters.db_supabase import pg, fetch_rows, exec_many
from src.domain.states import State

def run(limit=50):
    # 1) 领取任务（并发安全）
    rows = fetch_rows(f"""
        WITH cte AS (
          SELECT id
          FROM articles
          WHERE process_state = '{State.NEW}'
          ORDER BY publish_time NULLS LAST
          LIMIT {limit}
          FOR UPDATE SKIP LOCKED
        )
        UPDATE articles a
        SET process_state = '{State.SUMMARIZING}'
        FROM cte
        WHERE a.id = cte.id
        RETURNING a.id, a.title, a.content;
    """)

    if not rows: 
        print("no tasks"); return

    # 2) 处理
    updates = []
    for (aid, title, content) in rows:
        summary = your_llm_or_rules(title, content)   # 调 LLM 或规则
        updates.append((aid, summary))

    # 3) 写回（UPSERT 摘要表 + 改状态）
    exec_many("""
        INSERT INTO summaries(article_id, summary)
        VALUES (%s, %s)
        ON CONFLICT (article_id) DO UPDATE SET summary = EXCLUDED.summary;
    """, updates)

    exec_many("""
        UPDATE articles SET process_state = %s WHERE id = %s
    """, [(State.SUMMARIZED, aid) for (aid, _) in updates])

    print(f"done: {len(updates)}")
```

`src/domain/states.py`

```python
class State:
    NEW = "NEW"
    SUMMARIZING = "SUMMARIZING"
    SUMMARIZED = "SUMMARIZED"
    SCORED = "SCORED"
    EXPORTED = "EXPORTED"
```

---

# 命名与规矩（踩实几个小点就不乱）

* **文件命名**：`crawl_xxx.py` 只抓某一来源；`summarize.py`/`score.py`/`export_brief.py` 做通用处理。
* **函数命名**：每个 worker 有一个 `run(limit=...)`，其它都是内联私有函数。
* **日志**：先用 `print` 简单打点（起码打出“领取N条/成功M条/失败K条”），后面再换 `logging`。
* **配置**：所有连接串/API Key 只放 `config.py`（从 `.env.local` 读取），其它文件 `from src.config import settings`。
* **状态流**：只能单向推进（NEW→SUMMARIZING→SUMMARIZED→SCORED→EXPORTED），失败不前进、留在原状态并记录 `error_log`。
* **outputs 文件名**：继续你的风格，但统一成 `{YYYYMMDD}_{phase}_{tag}.txt`，便于机器查找。

---

# 你目录里几个具体点的建议

* `tools/`：现在这几个脚本已经对接 Supabase，**建议并入 `src/`**（见上），避免“同功能两份代码”。
* `replace-news-JGW/`：它是人手改稿的小工具，保持独立即可；在 README 里标注“用于人工修订，不参与流水线”。
* `__pycache__/`：加到 `.gitignore`（如果没加的话）。
* `文件夹结构.txt`：可以把这次整理后的结构覆写进去，作为索引卡。

---

# 以后加一个新“工人”很简单

比如你要新加“教育部官网抓取”：

1. `src/adapters/http_moe.py`：封装列表页/详情页抓取与清洗；
2. `src/workers/crawl_moe.py`：调用上面的 adapter，插入 `articles`，`source_tag='moe'`，`process_state=NEW`；
3. 计划任务里增加一条：`python run_pipeline.py crawl --limit 80`（改成 `crawl-moe` 也行，取决于你怎么拆入口）。

---

## 最后：你的问题“workers 要不要每个一个文件夹？”

**不用**。上面这样“`workers/` 里每个工人一个 `.py` 文件”就已经足够清晰；等到某个工人变得很复杂（比如 `crawl_toutiao.py` 需要 5–6 个辅助模块），再把它升级成一个子包目录也不迟（例如 `workers/crawl_toutiao/__init__.py` 以及若干模块）。

---

如果你愿意，我可以把：

* `src/` 这套目录的**空文件骨架**直接发你（可复制粘贴），
* 再给你一个 **`.gitignore` 模板** 和 **`config.py` 小样**，让你 10 分钟就能完成重命名与迁移。
