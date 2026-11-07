from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from src.adapters.db_postgres_news_summaries import (
    fetch_latest_news_summary_created_at,
    fetch_news_summary_content,
    fetch_prompt_test_article,
    insert_pending_summary,
    mark_summary_attempt,
    refund_summary_attempt,
    update_dedup_embeddings,
    upsert_news_summaries_from_primary,
    upsert_news_summary,
)
from src.config import get_settings


class FakeCursor:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.executemany_calls: list[tuple[str, Any]] = []
        self.row = row
        self.rowcount = 0

    def execute(self, query: str, params: Any) -> None:
        self.calls.append((query, params))

    def fetchone(self) -> dict[str, Any] | None:
        return self.row

    def executemany(self, query: str, params: Any) -> None:
        values = list(params)
        self.executemany_calls.append((query, values))
        self.rowcount = len(values)


def test_insert_pending_summary_only_executes_summary_upsert() -> None:
    cursor = FakeCursor()

    insert_pending_summary(
        cursor,
        {
            "article_id": "article-1",
            "title": "Test title",
            "content_markdown": "Test content",
        },
        keywords=["education", "education"],
    )

    assert len(cursor.calls) == 1
    query, params = cursor.calls[0]
    assert "INSERT INTO news_summaries" in query
    assert params[0] == "article-1"
    assert params[-1] == ["education"]


def test_fetch_news_summary_content_selects_drawer_metadata() -> None:
    expected = {
        "article_id": "article-1",
        "title": "Test title",
        "source": "Test source",
        "url": "https://example.com/article-1",
        "created_at": "2026-08-06T10:00:00+00:00",
        "content_markdown": "First paragraph.\n\nSecond paragraph.",
        "is_beijing_related": True,
        "sentiment_label": "positive",
        "external_importance_score": 58.0,
    }
    cursor = FakeCursor(expected)

    result = fetch_news_summary_content(cursor, "article-1")

    assert result == expected
    query, params = cursor.calls[0]
    for column in (
        "article_id",
        "title",
        "source",
        "url",
        "created_at",
        "content_markdown",
        # 分类/评分字段供抽屉头部渲染分类徽章；未入库文章三列均为 NULL
        "is_beijing_related",
        "sentiment_label",
        "external_importance_score",
    ):
        assert column in query
    assert "publish_time_iso" not in query
    # 以 raw_articles 为基表、news_summaries 字段优先：
    # 全库检索会命中未进摘要环节的文章，内容接口也要能给出它们的正文。
    assert "FROM raw_articles" in query
    assert "LEFT JOIN news_summaries" in query
    assert "ra.fetched_at" in query
    assert params == ("article-1",)


def test_fetch_prompt_test_article_selects_pipeline_context() -> None:
    expected = {
        "article_id": "article-1",
        "title": "Test title",
        "source": "Test source",
        "publish_time_iso": "2026-10-20T08:00:00+00:00",
        "llm_summary": "摘要",
        "content_markdown": "正文",
        "sentiment_label": "positive",
        "is_beijing_related": True,
        "is_beijing_related_llm": True,
        "external_importance_status": "ready_for_export",
        "external_importance_score": 72,
        "external_importance_raw": {
            "prompt_key": "internal_positive",
            "prompt_version": "v2",
        },
        "external_importance_checked_at": "2026-10-20T09:00:00+00:00",
        "beijing_gate_checked_at": "2026-10-20T08:30:00+00:00",
        "beijing_gate_raw": {"is_beijing_related": True},
        "score_details": {
            "matched_rules": [{"label": "高考"}, {"rule_id": "校园安全"}]
        },
        "status": "ready_for_export",
    }
    cursor = FakeCursor(expected)

    result = fetch_prompt_test_article(cursor, "article-1")

    assert result is not None
    assert result["article_id"] == "article-1"
    assert result["keyword_matches"] == ["高考", "校园安全"]
    query, params = cursor.calls[0]
    for column in (
        "llm_summary",
        "content_markdown",
        "sentiment_label",
        "is_beijing_related",
        "external_importance_raw",
        "beijing_gate_raw",
        "score_details",
    ):
        assert column in query
    assert "FROM news_summaries" in query
    assert "WHERE article_id = %s" in query
    assert params == ("article-1",)


def test_fetch_latest_created_at_uses_shift_half_open_range() -> None:
    latest = datetime(2026, 8, 21, 13, 50, tzinfo=timezone.utc)
    starts_at = datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc)
    ends_at = datetime(2026, 8, 21, 14, 0, tzinfo=timezone.utc)
    cursor = FakeCursor({"latest_created_at": latest})

    result = fetch_latest_news_summary_created_at(
        cursor,
        created_at_gte=starts_at,
        created_at_lt=ends_at,
    )

    assert result == latest
    query, params = cursor.calls[0]
    assert "created_at >= %s" in query
    assert "created_at < %s" in query
    assert params == (starts_at, ends_at)


def test_update_dedup_embeddings_only_updates_cache_columns() -> None:
    cursor = FakeCursor()

    updated = update_dedup_embeddings(
        cursor,
        [
            {
                "article_id": "article-1",
                "embedding": b"vector",
                "embedding_model": "model",
                "source_hash": "hash",
            }
        ],
    )

    assert updated == 1
    query, params = cursor.executemany_calls[0]
    assert "dedup_embedding = %s" in query
    assert "dedup_embedding_model = %s" in query
    assert "dedup_source_hash = %s" in query
    assert "dedup_embedded_at = NOW()" in query
    assert params == [(b"vector", "model", "hash", "article-1")]


def test_summary_upserts_do_not_overwrite_dedup_cache_columns() -> None:
    cursor = FakeCursor()
    article = {
        "article_id": "article-1",
        "title": "Test title",
        "content_markdown": "Test content",
    }

    upsert_news_summary(cursor, article, "Generated summary")
    upsert_news_summaries_from_primary(
        cursor,
        [
            {
                **article,
                "score": 80,
                "raw_relevance_score": 80,
                "keyword_bonus_score": 0,
                "score_details": {},
                "status": "pending",
                "keywords": [],
            }
        ],
    )

    queries = [cursor.calls[0][0], cursor.executemany_calls[0][0]]
    for query in queries:
        assert "dedup_embedding" not in query
        assert "dedup_embedding_model" not in query
        assert "dedup_source_hash" not in query
        assert "dedup_embedded_at" not in query


def test_refund_summary_attempt_undoes_the_claim_without_touching_other_rows() -> None:
    settings = get_settings()
    with psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        dbname=settings.db_name,
        autocommit=False,
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TEMP TABLE news_summaries "
                "(LIKE public.news_summaries INCLUDING DEFAULTS) ON COMMIT DROP"
            )
            cur.executemany(
                """
                INSERT INTO news_summaries (
                    article_id, summary_status, status, summary_fail_count
                )
                VALUES (%s, %s, %s, %s)
                """,
                [
                    ("pending-claimed", "pending", "pending", 2),
                    ("pending-zero", "pending", "pending", 0),
                    ("pending-untouched", "pending", "pending", 0),
                    ("summary-failed", "failed", "pending", 2),
                    ("status-failed", "pending", "failed", 2),
                    ("completed", "completed", "pending_enrichment", 2),
                ],
            )

            # 认领：只有 pending 行才会 +1。
            assert mark_summary_attempt(cur, "pending-claimed") is True
            assert mark_summary_attempt(cur, "pending-zero") is True
            assert mark_summary_attempt(cur, "summary-failed") is False
            assert mark_summary_attempt(cur, "status-failed") is False
            assert mark_summary_attempt(cur, "completed") is False

            # 退回：只对仍 pending 且计数 > 0 的行生效。
            refund_summary_attempt(cur, "pending-claimed")
            refund_summary_attempt(cur, "pending-zero")
            # 没有认领过的 0 计数行不得被退成负数。
            refund_summary_attempt(cur, "pending-untouched")
            refund_summary_attempt(cur, "summary-failed")
            refund_summary_attempt(cur, "status-failed")
            refund_summary_attempt(cur, "completed")

            cur.execute(
                """
                SELECT article_id, summary_fail_count, summary_status, status
                FROM news_summaries
                ORDER BY article_id
                """
            )
            rows = {row["article_id"]: dict(row) for row in cur.fetchall()}

    assert rows["pending-claimed"]["summary_fail_count"] == 2
    assert rows["pending-zero"]["summary_fail_count"] == 0
    assert rows["pending-untouched"]["summary_fail_count"] == 0
    assert rows["summary-failed"]["summary_fail_count"] == 2
    assert rows["status-failed"]["summary_fail_count"] == 2
    assert rows["completed"]["summary_fail_count"] == 2
