import argparse
import os
import time
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import requests


# ===== Path & runtime configuration =====
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "articles.sqlite3"
DEFAULT_KEYWORDS_FILE = REPO_ROOT / "education_keywords.txt"
ARTICLES_TABLE = "articles"
ID_COL = "article_id"  # Prefer long external ID for de-dup
CONTENT_COL = "content"


def _env_int(name: str, fallback: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback


DEFAULT_CONCURRENCY = _env_int("CONCURRENCY", 5)
ENV_PROCESS_LIMIT = os.getenv("PROCESS_LIMIT")


@dataclass(slots=True)
class SummarizeConfig:
    db_path: Path
    keywords_path: Path
    concurrency: int
    process_limit: Optional[int]


def load_dotenv_simple(path: str | Path = ".env", override: bool = False) -> None:
    """Minimal .env loader: KEY=VALUE pairs, ignores comments/blank lines."""
    p = Path(path)
    if not p.exists():
        return
    try:
        with open(p, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                if override or key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass


# Load .env early so env-based config resolves correctly
load_dotenv_simple()
load_dotenv_simple(REPO_ROOT / ".env")
load_dotenv_simple(REPO_ROOT / "config" / "abstract.env")

# SiliconFlow OpenAI-compatible endpoint
BASE_URL = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
API_KEY = os.getenv("SILICONFLOW_API_KEY")
MODEL = os.getenv("MODEL_NAME", "Qwen/Qwen2.5-14B-Instruct")
ENABLE_THINKING = os.getenv("ENABLE_THINKING", "false").lower() in ("1", "true", "yes", "y")

# Models that support enable_thinking flag
THINKING_SUPPORTED_PREFIXES = (
    "Qwen/Qwen3-8B",
    "Qwen/Qwen3-14B",
    "Qwen/Qwen3-32B",
    "Qwen/Qwen3-30B-A3B",
    "Qwen/Qwen3-235B-A22B",
    "tencent/Hunyuan-A13B-Instruct",
    "zai-org/GLM-4.5V",
    "deepseek-ai/DeepSeek-V3.1",
    "Pro/deepseek-ai/DeepSeek-V3.1",
)


def supports_thinking(model: str) -> bool:
    return any(model.startswith(p) for p in THINKING_SUPPORTED_PREFIXES)


def load_keywords(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"关键词文件不存在: {path}")
    kws: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            kws.append(s)
    if not kws:
        raise ValueError("关键词列表为空，请在 education_keywords.txt 中每行写一个关键词")
    return kws


def contains_keywords(text: str, keywords: Iterable[str]) -> bool:
    if not text:
        return False
    tl = text.lower()
    for kw in keywords:
        if kw and kw.lower() in tl:
            return True
    return False


def call_api(content: str, retries: int = 4) -> str:
    if not API_KEY:
        raise RuntimeError("缺少环境变量 SILICONFLOW_API_KEY")
    url = f"{BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    messages = [{"role": "user", "content": f"请总结下面的新闻，直接输出总结后的内容即可：{content}"}]
    payload: Dict[str, Any] = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 256,
    }
    if supports_thinking(MODEL):
        payload["enable_thinking"] = bool(ENABLE_THINKING) and False  # keep off by default
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=60)
            if r.status_code == 200:
                data = r.json()
                return data["choices"][0]["message"]["content"].strip()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            last_err = RuntimeError(f"API {r.status_code}: {r.text[:300]}")
        except Exception as e:
            last_err = e
        time.sleep(0.5 * (attempt + 1))
    raise last_err or RuntimeError("API 重试失败")


def call_source_api(content: str, retries: int = 4) -> Optional[str]:
    if not API_KEY:
        raise RuntimeError("缺少环境变量 SILICONFLOW_API_KEY")
    url = f"{BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    messages = [{"role": "user", "content": f"请识别本文的来源，仅仅输出答案即可：{content}"}]
    payload: Dict[str, Any] = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 64,
    }
    if supports_thinking(MODEL):
        payload["enable_thinking"] = bool(ENABLE_THINKING) and False
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=60)
            if r.status_code == 200:
                data = r.json()
                return data["choices"][0]["message"]["content"].strip()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            last_err = RuntimeError(f"API {r.status_code}: {r.text[:300]}")
        except Exception as e:
            last_err = e
        time.sleep(0.5 * (attempt + 1))
    if last_err:
        raise last_err
    return None


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS news_summaries (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          article_id INTEGER NOT NULL UNIQUE,
          title TEXT,
          content TEXT,
          summary TEXT NOT NULL,
          source TEXT,
          publish_time_iso TEXT,
          publish_time TEXT,
          original_url TEXT,
          source_LLM TEXT,
          correlation INTEGER
        );
        """
    )
    conn.commit()
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(news_summaries)")
    existing = {r[1] for r in cur.fetchall()}
    if "content" not in existing:
        cur.execute("ALTER TABLE news_summaries ADD COLUMN content TEXT")
    if "source_LLM" not in existing:
        cur.execute("ALTER TABLE news_summaries ADD COLUMN source_LLM TEXT")
    if "correlation" not in existing:
        cur.execute("ALTER TABLE news_summaries ADD COLUMN correlation INTEGER")

    conn.commit()



def row_get(row: sqlite3.Row, column: str) -> Any:
    lower = column.lower()
    for name in row.keys():
        if name.lower() == lower:
            return row[name]
    return None



@dataclass(slots=True)
class ArticleCandidate:
    target_id: Any
    content: str
    existing_summary: Optional[str] = None


def detect_id_column(cur: sqlite3.Cursor, table: str, preferred: str) -> str:
    try:
        cur.execute(f"PRAGMA table_info({table})")
        cols = [r[1].lower() for r in cur.fetchall()]
    except Exception:
        cols = []
    pref = preferred.lower()
    if pref in cols:
        return preferred
    if "article_id" in cols:
        return "article_id"
    if "id" in cols:
        return "id"
    return preferred


def build_update_sql(cur: sqlite3.Cursor, detected_id_col: str) -> Tuple[Optional[str], Optional[str], List[str]]:
    try:
        cur.execute("PRAGMA table_info(news_summaries)")
        sum_cols: Set[str] = {r[1].lower() for r in cur.fetchall()}
    except Exception:
        sum_cols = set()
    try:
        cur.execute(f"PRAGMA table_info({ARTICLES_TABLE})")
        art_cols: Set[str] = {r[1].lower() for r in cur.fetchall()}
    except Exception:
        art_cols = set()

    update_fields = [
        name
        for name in ("title", "content", "source", "publish_time", "original_url")
        if name in sum_cols and name in art_cols
    ]
    if update_fields:
        set_clause = ", ".join(
            [
                f"{f} = COALESCE({f}, (SELECT a.{f} FROM {ARTICLES_TABLE} a WHERE a.{detected_id_col} = ?))"
                for f in update_fields
            ]
        )
        update_sql = f"UPDATE news_summaries SET {set_clause} WHERE article_id = ?"
    else:
        update_sql = None

    if ("publish_time_iso" in sum_cols) and ("publish_time" in art_cols):
        update_publish_iso_sql = (
            "UPDATE news_summaries SET publish_time_iso = COALESCE(publish_time_iso, "
            "(SELECT CASE "
            " WHEN CAST(a.publish_time AS TEXT) GLOB '[0-9]*' AND length(CAST(a.publish_time AS TEXT))>=13 THEN datetime(CAST(a.publish_time AS REAL)/1000, 'unixepoch') "
            " WHEN CAST(a.publish_time AS TEXT) GLOB '[0-9]*' AND length(CAST(a.publish_time AS TEXT))=10 THEN datetime(a.publish_time, 'unixepoch') "
            " ELSE a.publish_time END FROM {table} a WHERE a.{idcol} = ?)) "
            "WHERE article_id = ?"
        ).format(table=ARTICLES_TABLE, idcol=detected_id_col)
    else:
        update_publish_iso_sql = None

    return update_sql, update_publish_iso_sql, update_fields


def summarize_articles(config: SummarizeConfig) -> Tuple[int, int, int, int]:
    keywords = load_keywords(config.keywords_path)

    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    ensure_table(conn)

    detected_id_col = detect_id_column(cur, ARTICLES_TABLE, ID_COL)
    update_sql, update_publish_iso_sql, update_fields = build_update_sql(cur, detected_id_col)

    cur.execute("SELECT article_id FROM news_summaries")
    done_ids: Set[str] = {str(row[0]) for row in cur.fetchall() if row[0] is not None}

    cur.execute(
        f"SELECT a.*, a.{detected_id_col} AS target_id FROM {ARTICLES_TABLE} a "
        f"LEFT JOIN news_summaries s ON s.article_id = a.{detected_id_col} "
        f"WHERE s.article_id IS NULL"
    )
    rows = cur.fetchall()

    candidates: List[ArticleCandidate] = []
    filtered_out = 0
    total = 0
    seen_done = 0

    for row in rows:
        total += 1
        target_value = row_get(row, "target_id")
        if target_value is None:
            target_value = row_get(row, detected_id_col)
        key = str(target_value) if target_value is not None else ""
        if key in done_ids:
            seen_done += 1
            continue
        content = (row_get(row, CONTENT_COL) or "")
        raw_summary = row_get(row, "summary")
        normalized_summary: Optional[str]
        if isinstance(raw_summary, str):
            normalized_summary = raw_summary.strip()
            if not normalized_summary or normalized_summary.lower() in {"null", "none"}:
                normalized_summary = None
        else:
            normalized_summary = None
        if contains_keywords(content, keywords):
            candidates.append(
                ArticleCandidate(
                    target_id=target_value,
                    content=content,
                    existing_summary=normalized_summary,
                )
            )
        else:
            filtered_out += 1
        if config.process_limit and len(candidates) >= config.process_limit:
            break
    filtered = filtered_out

    if not candidates:
        conn.close()
        skipped = max(0, total - seen_done - filtered)
        print(f"done. ok=0 skipped={skipped} filtered={filtered} failed=0")
        return 0, 0, filtered, skipped

    ok = 0
    failed = 0
    concurrency = max(1, config.concurrency)

    reused_summaries = 0

    if concurrency == 1:
        for cand in candidates:
            try:
                if cand.existing_summary:
                    summary = cand.existing_summary
                    src_llm: Optional[str] = None
                    reused_summaries += 1
                else:
                    summary = call_api(cand.content)
                    try:
                        src_llm = call_source_api(cand.content)
                    except Exception:
                        src_llm = None
                cur.execute(
                    "INSERT INTO news_summaries(article_id, content, summary) VALUES(?,?,?)",
                    (cand.target_id, cand.content, summary),
                )
                if update_sql:
                    params = [cand.target_id] * (len(update_fields) + 1)
                    cur.execute(update_sql, params)
                if update_publish_iso_sql:
                    cur.execute(update_publish_iso_sql, (cand.target_id, cand.target_id))
                if src_llm:
                    cur.execute("UPDATE news_summaries SET source_LLM=? WHERE article_id=?", (src_llm, cand.target_id))
                conn.commit()
                ok += 1
                msg = "OK article {aid}".format(aid=cand.target_id)
                if cand.existing_summary:
                    msg += " (existing summary reused)"
                print(msg)
            except Exception as e:
                failed += 1
                print(f"FAIL article {cand.target_id}: {e}")
    else:
        need_api = sum(1 for cand in candidates if not cand.existing_summary)
        reuse_count = len(candidates) - need_api
        if need_api:
            print(
                f"Processing {len(candidates)} items with concurrency={concurrency}... "
                f"(reusing {reuse_count} existing summaries)"
            )
        else:
            print(
                f"Processing {len(candidates)} items without API calls (all summaries reused)."
            )
        content_map: Dict[Any, str] = {cand.target_id: cand.content for cand in candidates}

        def worker(cand: ArticleCandidate) -> Tuple[Any, str, Optional[str], bool]:
            if cand.existing_summary:
                return cand.target_id, cand.existing_summary, None, True
            summary = call_api(cand.content)
            src_llm: Optional[str]
            try:
                src_llm = call_source_api(cand.content)
            except Exception:
                src_llm = None
            return cand.target_id, summary, src_llm, False

        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            future_map = {ex.submit(worker, cand): cand.target_id for cand in candidates}
            for fut in as_completed(future_map):
                aid = future_map[fut]
                try:
                    aid_ret, summary, src_llm, reused = fut.result()
                    article_content = content_map.get(aid_ret, "")
                    cur.execute(
                        "INSERT INTO news_summaries(article_id, content, summary) VALUES(?,?,?)",
                        (aid_ret, article_content, summary),
                    )
                    if update_sql:
                        params = [aid_ret] * (len(update_fields) + 1)
                        cur.execute(update_sql, params)
                    if update_publish_iso_sql:
                        cur.execute(update_publish_iso_sql, (aid_ret, aid_ret))
                    if src_llm:
                        cur.execute("UPDATE news_summaries SET source_LLM=? WHERE article_id=?", (src_llm, aid_ret))
                    conn.commit()
                    ok += 1
                    if reused:
                        reused_summaries += 1
                        print(f"OK article {aid_ret} (existing summary reused)")
                    else:
                        print(f"OK article {aid_ret}")
                except Exception as e:
                    failed += 1
                    print(f"FAIL article {aid}: {e}")

    conn.close()
    skipped = max(0, total - ok - failed - filtered - seen_done)
    print(
        f"done. ok={ok} skipped={skipped} filtered={filtered} failed={failed} reused={reused_summaries}"
    )
    return ok, failed, filtered, skipped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize news articles with SiliconFlow API")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Path to the target articles SQLite DB")
    parser.add_argument("--keywords", type=Path, default=DEFAULT_KEYWORDS_FILE, help="Path to keyword list file")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of articles to summarize")
    parser.add_argument("--concurrency", type=int, default=None, help="Number of threads for API calls")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    limit = args.limit
    if limit is None and ENV_PROCESS_LIMIT and ENV_PROCESS_LIMIT.isdigit():
        limit = int(ENV_PROCESS_LIMIT)
    concurrency = args.concurrency if args.concurrency else DEFAULT_CONCURRENCY

    config = SummarizeConfig(
        db_path=args.db.resolve(),
        keywords_path=args.keywords.resolve(),
        concurrency=max(1, concurrency),
        process_limit=limit if limit and limit > 0 else None,
    )
    summarize_articles(config)


if __name__ == "__main__":
    main()
