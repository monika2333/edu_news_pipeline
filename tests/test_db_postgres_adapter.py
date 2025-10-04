
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import psycopg

from src.adapters import db as db_factory
from src.config import get_settings
from src.domain import ArticleInput


def _reset_adapter_cache() -> None:
    db_factory._ADAPTER = None  # type: ignore[attr-defined]
    get_settings.cache_clear()


def test_postgres_adapter_upsert_and_counts() -> None:
    os.environ['DB_BACKEND'] = 'postgres'
    _reset_adapter_cache()

    settings = get_settings()
    adapter = db_factory.get_adapter()

    unique_id = f"test-{uuid.uuid4()}"
    now_ts = int(datetime.now(timezone.utc).timestamp())
    article = ArticleInput(
        article_id=unique_id,
        title="Test Article",
        source=f"Test Source {unique_id}",
        publish_time=now_ts,
        original_url=f"https://example.com/{unique_id}",
        content="Sample content for validation",
        raw_payload={"origin": "test"},
        metadata={"language": "zh"},
    )

    article_hash = None
    counts_before = adapter.get_article_counts()
    try:
        created = adapter.upsert_article(article)
        article_hash = str(created['hash'])

        counts_after = adapter.get_article_counts()
        assert counts_after[0] == counts_before[0] + 1
        assert counts_after[1] == counts_before[1] + 1

        exists_map = adapter.articles_exist([article_hash])
        assert exists_map[article_hash] is True

        missing_targets = adapter.iter_missing_content(limit=10)
        assert all(target.article_hash != article_hash for target in missing_targets)
    finally:
        with psycopg.connect(
            host=settings.db_host,
            port=settings.db_port,
            user=settings.db_user,
            password=settings.db_password,
            dbname=settings.db_name,
            autocommit=True,
        ) as conn:
            with conn.cursor() as cur:
                if article_hash:
                    cur.execute("DELETE FROM raw_articles WHERE hash = %s", (article_hash,))
                cur.execute("DELETE FROM sources WHERE name = %s", (article.source,))

    _reset_adapter_cache()
