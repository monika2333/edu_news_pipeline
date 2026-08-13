from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import Mock
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row

from src.adapters.db_postgres_article_attribution import (
    SUMMARY_SEARCH_TEXT_EXPRESSION,
    search_article_attributions,
)
from src.config import get_settings
from src.console import articles_service
from src.console.articles_schemas import NewsArticleSearchResponse


class _CountingCursor:
    def __init__(self, cursor: psycopg.Cursor) -> None:
        self._cursor = cursor
        self.execute_count = 0

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.execute_count += 1
        self._cursor.execute(query, params)

    def fetchall(self) -> list[dict[str, Any]]:
        return self._cursor.fetchall()


def _search(
    cur: psycopg.Cursor,
    query: str,
    *,
    limit: int = 20,
    offset: int = 0,
) -> tuple[dict[str, Any], int]:
    counting_cursor = _CountingCursor(cur)
    result = search_article_attributions(
        counting_cursor,
        query=query,
        fetched_after=datetime.now(timezone.utc) - timedelta(days=7),
        limit=limit,
        offset=offset,
    )
    return result, counting_cursor.execute_count


def _create_temp_search_tables(cur: psycopg.Cursor) -> None:
    for table in (
        "raw_articles",
        "filtered_articles",
        "primary_articles",
        "news_summaries",
        "manual_reviews",
        "shift_reviews",
        "brief_items",
        "brief_batches",
        "console_users",
    ):
        cur.execute(
            f"CREATE TEMP TABLE {table} "
            f"(LIKE public.{table} INCLUDING DEFAULTS) ON COMMIT DROP"
        )


def _seed_attribution_scenarios(cur: psycopg.Cursor) -> None:
    now = datetime.now(timezone.utc)
    raw_rows = [
        ("attr-keyword", "Keyword Missed", "keywordonly body", now),
        ("attr-relevance", "Relevance Below", "relevanceonly body", now),
        ("attr-importance", "Importance Below", "importanceonly body", now),
        ("attr-not-reviewed", "Not Reviewed", "notreviewedonly body", now),
        ("attr-discarded", "Discarded", "discardedonly body", now),
        ("attr-selected", "Selected Pending Export", "selectedonly body", now),
        ("attr-review-exported", "Review Exported", "reviewexportedonly body", now),
        (
            "attr-dup-primary",
            "Primary Title",
            "sharedterm primary body",
            now - timedelta(minutes=1),
        ),
        ("attr-duplicate", "Matched Duplicate Title", "sharedterm duplicateonly body", now),
    ]
    cur.executemany(
        """
        INSERT INTO raw_articles (article_id, title, content_markdown, fetched_at)
        VALUES (%s, %s, %s, %s)
        """,
        raw_rows,
    )
    cur.executemany(
        """
        INSERT INTO filtered_articles (article_id, status, primary_article_id)
        VALUES (%s, %s, %s)
        """,
        [
            ("attr-relevance", "pending", None),
            ("attr-importance", "pending", None),
            ("attr-not-reviewed", "pending", None),
            ("attr-discarded", "pending", None),
            ("attr-selected", "pending", None),
            ("attr-review-exported", "pending", None),
            ("attr-dup-primary", "pending", None),
            ("attr-duplicate", "duplicate", "attr-dup-primary"),
        ],
    )
    cur.executemany(
        """
        INSERT INTO primary_articles (
            article_id,
            primary_article_id,
            status,
            score
        )
        VALUES (%s, %s, %s, %s)
        """,
        [
            ("attr-relevance", "attr-relevance", "filtered_out", 12),
            ("attr-importance", "attr-importance", "promoted", 81),
            ("attr-not-reviewed", "attr-not-reviewed", "promoted", 82),
            ("attr-discarded", "attr-discarded", "promoted", 83),
            ("attr-selected", "attr-selected", "promoted", 83),
            ("attr-review-exported", "attr-review-exported", "promoted", 83),
            ("attr-dup-primary", "attr-dup-primary", "promoted", 84),
        ],
    )
    cur.executemany(
        """
        INSERT INTO news_summaries (
            article_id,
            title,
            status,
            score,
            external_importance_status,
            external_importance_score,
            created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        [
            (
                "attr-importance",
                "Importance Below",
                "external_filtered",
                81,
                "external_filtered",
                41,
                now,
            ),
            (
                "attr-not-reviewed",
                "Not Reviewed",
                "ready_for_export",
                82,
                "ready_for_export",
                70,
                now,
            ),
            (
                "attr-discarded",
                "Discarded",
                "ready_for_export",
                83,
                "ready_for_export",
                71,
                now,
            ),
            (
                "attr-selected",
                "Selected Pending Export",
                "ready_for_export",
                83,
                "ready_for_export",
                71,
                now,
            ),
            (
                "attr-review-exported",
                "Review Exported",
                "ready_for_export",
                83,
                "ready_for_export",
                71,
                now,
            ),
            (
                "attr-dup-primary",
                "Primary Title",
                "ready_for_export",
                84,
                "ready_for_export",
                72,
                now,
            ),
        ],
    )
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    shift_id = UUID("22222222-2222-2222-2222-222222222222")
    batch_id = UUID("33333333-3333-3333-3333-333333333333")
    cur.execute(
        """
        INSERT INTO console_users (
            id,
            username,
            display_name,
            password_hash,
            role
        )
        VALUES (%s, 'editor', 'Test Editor', 'not-a-real-hash', 'admin')
        """,
        (user_id,),
    )
    cur.execute(
        """
        INSERT INTO manual_reviews (
            article_id,
            status,
            decided_by_user_id,
            decided_at
        )
        VALUES ('attr-discarded', 'selected', %s, %s)
        """,
        (user_id, now - timedelta(minutes=2)),
    )
    cur.execute(
        """
        INSERT INTO manual_reviews (
            article_id,
            status,
            decided_by_user_id,
            decided_at
        )
        VALUES ('attr-selected', 'selected', %s, %s)
        """,
        (user_id, now - timedelta(minutes=1)),
    )
    cur.execute(
        """
        INSERT INTO manual_reviews (
            article_id,
            status,
            decided_by_user_id,
            decided_at
        )
        VALUES ('attr-review-exported', 'exported', %s, %s)
        """,
        (user_id, now - timedelta(seconds=30)),
    )
    cur.execute(
        """
        INSERT INTO shift_reviews (
            shift_id,
            article_id,
            created_by_user_id,
            updated_by_user_id,
            decision,
            decided_at
        )
        VALUES (%s, 'attr-discarded', %s, %s, 'discarded', %s)
        """,
        (shift_id, user_id, user_id, now - timedelta(minutes=3)),
    )
    cur.execute(
        "INSERT INTO brief_batches (id, report_date) VALUES (%s, %s)",
        (batch_id, date(2026, 8, 12)),
    )
    cur.execute(
        """
        INSERT INTO brief_items (brief_batch_id, article_id)
        VALUES (%s, 'attr-discarded')
        """,
        (batch_id,),
    )


def test_full_pipeline_attribution_and_primary_deduplication() -> None:
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
            _create_temp_search_tables(cur)
            _seed_attribution_scenarios(cur)

            keyword, keyword_queries = _search(cur, "keywordonly")
            relevance, relevance_queries = _search(cur, "relevanceonly")
            importance, importance_queries = _search(cur, "importanceonly")
            not_reviewed, not_reviewed_queries = _search(cur, "notreviewedonly")
            discarded, discarded_queries = _search(cur, "discardedonly")
            selected, selected_queries = _search(cur, "selectedonly")
            review_exported, review_exported_queries = _search(cur, "reviewexportedonly")
            duplicate, duplicate_queries = _search(cur, "duplicateonly")
            same_group, same_group_queries = _search(cur, "sharedterm")
            missing, missing_queries = _search(cur, "absent-everywhere")

    assert keyword["items"][0]["attribution_level"] == "keyword_missed"
    assert keyword["items"][0]["attribution_ingested_at_source"] == "raw_articles.fetched_at"
    assert relevance["items"][0]["attribution_level"] == "relevance_below"
    assert float(relevance["items"][0]["attribution_relevance_score"]) == 12
    assert importance["items"][0]["attribution_level"] == "importance_below"
    assert float(importance["items"][0]["attribution_importance_score"]) == 41
    assert not_reviewed["items"][0]["attribution_level"] == "not_reviewed"

    discarded_item = discarded["items"][0]
    assert discarded_item["attribution_level"] == "discarded"
    assert "attribution_export_batch_dates" not in discarded_item
    assert "attribution_is_fallback" not in discarded_item
    assert {item["workspace"] for item in discarded_item["attribution_manual_decisions"]} == {
        "admin",
        "duty",
    }
    assert any(
        item["workspace"] == "duty" and item["decision"] == "discarded"
        for item in discarded_item["attribution_manual_decisions"]
    )

    selected_item = selected["items"][0]
    assert selected_item["attribution_level"] == "not_reviewed"
    assert selected_item["attribution_manual_decisions"][0]["decision"] == "selected"

    review_exported_item = review_exported["items"][0]
    assert review_exported_item["attribution_level"] == "not_reviewed"
    assert review_exported_item["attribution_manual_decisions"][0]["decision"] == "exported"

    duplicate_item = duplicate["items"][0]
    assert duplicate["total"] == 1
    assert duplicate_item["article_id"] == "attr-dup-primary"
    assert duplicate_item["attribution_matched_article_title"] == "Matched Duplicate Title"
    assert same_group["total"] == 1
    assert same_group["items"][0]["article_id"] == "attr-dup-primary"
    assert same_group["items"][0]["attribution_matched_article_title"] is None
    assert missing == {"items": [], "total": 0, "limit": 20, "offset": 0}
    assert {
        keyword_queries,
        relevance_queries,
        importance_queries,
        not_reviewed_queries,
        discarded_queries,
        selected_queries,
        review_exported_queries,
        duplicate_queries,
        same_group_queries,
        missing_queries,
    } == {1}


class _FakeNewsSummaries:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def search_with_attribution(self, **kwargs: Any) -> dict[str, Any]:
        self.kwargs = kwargs
        return {
            "items": [
                {
                    "article_id": "raw-only",
                    "title": "Raw only",
                    "score": None,
                    "attribution_level": "keyword_missed",
                    "attribution_ingested_at": datetime(2026, 8, 12, tzinfo=timezone.utc),
                    "attribution_ingested_at_source": "raw_articles.fetched_at",
                    "attribution_relevance_score": 0,
                    "attribution_importance_score": None,
                    "attribution_manual_decisions": [],
                    "attribution_matched_article_title": None,
                }
            ],
            "total": 1,
        }


class _FakeAdapter:
    def __init__(self) -> None:
        self.news_summaries = _FakeNewsSummaries()


def test_service_preserves_missing_fields_and_zero_scores(monkeypatch) -> None:
    adapter = _FakeAdapter()
    monkeypatch.setattr(articles_service, "_get_adapter_safe", lambda: adapter)

    result = articles_service.search_articles(
        query="raw",
        page=1,
        limit=20,
        lookback_days=45,
    )
    response = NewsArticleSearchResponse.model_validate(result).model_dump()

    assert response["items"][0]["llm_summary"] is None
    assert response["items"][0]["score"] is None
    assert response["items"][0]["attribution"]["relevance_score"] == 0
    assert response["items"][0]["attribution"]["importance_score"] is None
    assert "is_fallback" not in response["items"][0]["attribution"]
    assert "export_batch_dates" not in response["items"][0]["attribution"]
    assert response["lookback_days"] == 45
    assert response["window_start"] == adapter.news_summaries.kwargs["fetched_after"]

    default_result = articles_service.search_articles(query="raw")

    assert default_result["lookback_days"] == 30
    assert default_result["window_start"] == adapter.news_summaries.kwargs["fetched_after"]


def test_service_rejects_blank_query_before_getting_adapter(monkeypatch) -> None:
    def fail_get_adapter():
        raise AssertionError("blank searches must not reach the database adapter")

    monkeypatch.setattr(articles_service, "_get_adapter_safe", fail_get_adapter)

    with pytest.raises(ValueError, match="must not be blank"):
        articles_service.search_articles(query="   ")


def test_adapter_rejects_blank_query_before_executing_sql() -> None:
    cursor = Mock(spec=psycopg.Cursor)

    with pytest.raises(ValueError, match="must not be blank"):
        search_article_attributions(
            cursor,
            query="   ",
            fetched_after=datetime.now(timezone.utc),
            limit=20,
            offset=0,
        )

    cursor.execute.assert_not_called()


def test_summary_search_expression_matches_trigram_index_definition() -> None:
    assert SUMMARY_SEARCH_TEXT_EXPRESSION == (
        "(coalesce(ns.title, '') || ' ' || coalesce(ns.llm_summary, '') || ' ' || "
        "coalesce(ns.content_markdown, ''))"
    )
