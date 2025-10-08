from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg

from src.config import get_settings, load_environment


def to_iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def to_text_min(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        china_tz = timezone(timedelta(hours=8))
        return value.astimezone(china_tz).strftime("%Y-%m-%d %H:%M")
    if value is None:
        return None
    return str(value)


def export_latest(limit: int = 5, output: Optional[str] = None) -> Path:
    load_environment()
    settings = get_settings()
    conn = psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        dbname=settings.db_name,
        autocommit=True,
    )
    schema = settings.db_schema or "public"
    rows: List[Dict[str, Any]] = []
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema}")
        cur.execute(
            "SELECT article_id, title, source, publish_time, publish_time_iso, url, content_markdown, fetched_at "
            "FROM raw_articles WHERE article_id LIKE %s ORDER BY fetched_at DESC LIMIT %s",
            ('%chinanews:%', max(1,int(limit))),
        )
        for rec in cur.fetchall():
            article_id, title, source, pt, pti, url, content, fetched_at = rec
            rows.append(
                {
                    "article_id": article_id,
                    "title": title,
                    "source": source,
                    "publish_time": pt,
                    "publish_time_iso": to_iso(pti),
                    "publish_time_text": to_text_min(pti),
                    "url": url,
                    "content_markdown": content,
                    "fetched_at": to_iso(fetched_at),
                }
            )

    out_path = Path(output) if output else (Path("outputs") / "chinanews_latest.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Export latest ChinaNews rows to JSON")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()
    out = export_latest(limit=args.limit, output=args.output)
    print(str(out))


if __name__ == "__main__":
    main()



