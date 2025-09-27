import sqlite3
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
TOOLS_DIR = CURRENT_DIR.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from summarize_news import DEFAULT_DB_PATH, call_api

DB_PATH = DEFAULT_DB_PATH


def fetch_article_content(conn: sqlite3.Connection, article_id: int) -> str | None:
    cur = conn.execute(
        "SELECT content FROM news_summaries WHERE article_id=?",
        (article_id,),
    )
    row = cur.fetchone()
    if row and row[0]:
        return row[0]
    cur = conn.execute(
        "SELECT content FROM articles WHERE article_id=?",
        (article_id,),
    )
    row = cur.fetchone()
    if row and row[0]:
        return row[0]
    return None


def main() -> None:
    db_path = DB_PATH
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT article_id FROM news_summaries WHERE summary IS NULL OR LENGTH(TRIM(summary))=0"
    )
    article_ids = [row[0] for row in cur.fetchall()]
    print(f"Found {len(article_ids)} news_summaries rows lacking summary")
    updated = 0
    skipped = 0
    errors = 0
    for idx, article_id in enumerate(article_ids, start=1):
        content = fetch_article_content(conn, article_id)
        if not content:
            print(f"[{idx}/{len(article_ids)}] Skip article {article_id}: no content available")
            skipped += 1
            continue
        try:
            summary = call_api(content)
        except Exception as exc:
            print(f"[{idx}/{len(article_ids)}] Fail article {article_id}: {exc}")
            errors += 1
            continue
        summary = summary.strip()
        if summary:
            cur.execute(
                "UPDATE news_summaries SET summary=? WHERE article_id=?",
                (summary, article_id),
            )
            conn.commit()
            updated += 1
            print(f"[{idx}/{len(article_ids)}] Updated article {article_id}")
        else:
            print(f"[{idx}/{len(article_ids)}] No summary generated for article {article_id}")
            skipped += 1
    conn.close()
    print(
        f"Done. updated={updated} skipped={skipped} errors={errors} remaining={len(article_ids) - updated - skipped - errors}"
    )


if __name__ == "__main__":
    main()
