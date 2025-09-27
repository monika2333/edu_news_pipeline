import argparse
import os
import re
import time
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Tuple

import requests
REPO_ROOT = Path(__file__).resolve().parents[1]

try:
    from summarize_news import load_dotenv_simple
except Exception:  # pragma: no cover - fallback for CLI execution only
    def load_dotenv_simple(path: str | Path = ".env", override: bool = False) -> None:
        p = Path(path)
        if not p.exists():
            return
        with open(p, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if override or k.strip() not in os.environ:
                    os.environ[k.strip()] = v


load_dotenv_simple()
load_dotenv_simple(REPO_ROOT / ".env")
load_dotenv_simple(REPO_ROOT / "config" / "abstract.env")

DEFAULT_DB_PATH = REPO_ROOT / "articles.sqlite3"
TABLE = "news_summaries"

BASE_URL = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
API_KEY = os.getenv("SILICONFLOW_API_KEY")
MODEL = os.getenv("MODEL_NAME", "Qwen/Qwen3-8B")
ENABLE_THINKING = os.getenv("ENABLE_THINKING", "true").lower() in ("1", "true", "yes", "y")

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


DEFAULT_CONCURRENCY = int(os.getenv("CONCURRENCY", "5") or "5")


def ensure_column(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(news_summaries)")
    existing = {r[1] for r in cur.fetchall()}
    if "correlation" not in existing:
        cur.execute("ALTER TABLE news_summaries ADD COLUMN correlation INTEGER")
        conn.commit()


def call_score_api(text: str, retries: int = 4) -> str:
    if not API_KEY:
        raise RuntimeError("缺少环境变量 SILICONFLOW_API_KEY")
    url = f"{BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    prompt = (
        "请判断下面的内容和教育的相关程度，输出数字0-100，"
        "其中0为完全不相关，100为完全相关："
        f"{text}"
    )
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 16,
    }
    if ENABLE_THINKING and supports_thinking(MODEL):
        payload["enable_thinking"] = True
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=60)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            last_err = RuntimeError(f"API {r.status_code}: {r.text[:200]}")
        except Exception as e:
            last_err = e
        time.sleep(0.5 * (attempt + 1))
    raise last_err or RuntimeError("API 重试失败")


def parse_score(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"(\d{1,3})", text)
    if not m:
        return None
    n = int(m.group(1))
    if n < 0:
        n = 0
    if n > 100:
        n = 100
    return n


def score_correlation(db_path: Path, concurrency: int) -> Tuple[int, int]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    ensure_column(conn)

    cur.execute(
        f"SELECT id AS rid, article_id, content FROM {TABLE} WHERE correlation IS NULL"
    )
    rows = cur.fetchall()
    total = len(rows)
    if not rows:
        conn.close()
        print("No rows to score (correlation already filled).")
        return 0, 0

    print(
        f"Scoring correlation for {total} rows from content with concurrency={concurrency} using model={MODEL}..."
    )

    def worker(item: Tuple[int, str]) -> Tuple[int, Optional[int]]:
        rid, text = item
        if not text.strip():
            return rid, None
        resp = call_score_api(text)
        score = parse_score(resp)
        return rid, score

    ok = 0
    failed = 0
    items = [(int(r["rid"]), r["content"] or "") for r in rows]
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futmap = {ex.submit(worker, it): it[0] for it in items}
        for fut in as_completed(futmap):
            rid = futmap[fut]
            try:
                rowid, score = fut.result()
                cur.execute(f"UPDATE {TABLE} SET correlation=? WHERE rowid=?", (score, rowid))
                conn.commit()
                ok += 1
            except Exception as e:
                failed += 1
                print(f"FAIL rowid {rid}: {e}")

    conn.close()
    print(f"done. ok={ok} failed={failed}")
    return ok, failed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score education relevance for summaries")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="news_summaries 所在的 SQLite 路径")
    parser.add_argument("--concurrency", type=int, default=None, help="请求并发数")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    concurrency = args.concurrency if args.concurrency else DEFAULT_CONCURRENCY
    score_correlation(args.db.resolve(), max(1, concurrency))


if __name__ == "__main__":
    main()






