from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import Mock
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Json

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
    cursor_ingested_at: datetime | None = None,
    cursor_article_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    counting_cursor = _CountingCursor(cur)
    result = search_article_attributions(
        counting_cursor,
        query=query,
        fetched_after=datetime.now(timezone.utc) - timedelta(days=7),
        limit=limit,
        cursor_ingested_at=cursor_ingested_at,
        cursor_article_id=cursor_article_id,
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
        "submitted_reports",
        "submitted_report_items",
        "score_feedbacks",
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
            external_importance_raw,
            created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        [
            (
                "attr-importance",
                "Importance Below",
                "external_filtered",
                81,
                "external_filtered",
                41,
                Json({"prompt_key": "external_positive", "prompt_version": "v1"}),
                now,
            ),
            (
                "attr-not-reviewed",
                "Not Reviewed",
                "ready_for_export",
                82,
                "ready_for_export",
                70,
                None,
                now,
            ),
            (
                "attr-discarded",
                "Discarded",
                "ready_for_export",
                83,
                "ready_for_export",
                71,
                None,
                now,
            ),
            (
                "attr-selected",
                "Selected Pending Export",
                "ready_for_export",
                83,
                "ready_for_export",
                71,
                None,
                now,
            ),
            (
                "attr-review-exported",
                "Review Exported",
                "ready_for_export",
                83,
                "ready_for_export",
                71,
                None,
                now,
            ),
            (
                "attr-dup-primary",
                "Primary Title",
                "ready_for_export",
                84,
                "ready_for_export",
                72,
                None,
                now,
            ),
        ],
    )
    # 评分反馈：attr-importance 的当前评分上下文上有一条「偏低」反馈。
    cur.execute(
        """
        INSERT INTO score_feedbacks (
            article_id,
            feedback_type,
            score_value,
            prompt_key,
            prompt_version,
            notes,
            score_context
        )
        VALUES ('attr-importance', 'too_low', 41, 'external_positive', 'v1', '分数打低了', '{}')
        """
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
    # 报送存档回链：attr-selected 有一条已确认回链（matched），
    # attr-discarded 只有待确认回链（pending），不算「有存档」。
    report_id = UUID("44444444-4444-4444-4444-444444444444")
    cur.execute(
        """
        INSERT INTO submitted_reports (id, report_type, report_date, compiled_date, pasted_text)
        VALUES (%s, 'zongbao', %s, %s, 'pasted')
        """,
        (report_id, date(2026, 8, 13), date(2026, 8, 13)),
    )
    cur.executemany(
        """
        INSERT INTO submitted_report_items (
            report_id,
            title,
            body,
            source,
            norm_title,
            norm_title_hash,
            article_id,
            link_status
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        [
            (
                report_id,
                "Selected Archive Title",
                "存档正文",
                "测试来源",
                "selected archive title",
                "hash-selected",
                "attr-selected",
                "matched",
            ),
            (
                report_id,
                "Pending Archive Title",
                "待确认正文",
                "测试来源",
                "pending archive title",
                "hash-pending",
                "attr-discarded",
                "pending",
            ),
        ],
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
    # 评分反馈按当前评分上下文（prompt_key + prompt_version）匹配返回
    assert importance["items"][0]["score_feedback_type"] == "too_low"
    assert importance["items"][0]["score_feedback_notes"] == "分数打低了"
    assert not_reviewed["items"][0]["attribution_level"] == "not_reviewed"
    assert not_reviewed["items"][0]["score_feedback_type"] is None

    discarded_item = discarded["items"][0]
    assert discarded_item["attribution_level"] == "discarded"
    assert "attribution_export_batch_dates" not in discarded_item
    assert "attribution_is_fallback" not in discarded_item
    assert {item["workspace"] for item in discarded_item["attribution_manual_decisions"]} == {
        "admin",
        "duty",
    }
    # pending 回链未人工确认，不算「有存档」
    assert discarded_item["archive_links"] == []
    assert any(
        item["workspace"] == "duty" and item["decision"] == "discarded"
        for item in discarded_item["attribution_manual_decisions"]
    )

    selected_item = selected["items"][0]
    assert selected_item["attribution_level"] == "not_reviewed"
    assert selected_item["attribution_manual_decisions"][0]["decision"] == "selected"
    selected_links = selected_item["archive_links"]
    assert len(selected_links) == 1
    assert selected_links[0]["report_type"] == "zongbao"
    assert selected_links[0]["link_status"] == "matched"
    assert selected_links[0]["title"] == "Selected Archive Title"

    review_exported_item = review_exported["items"][0]
    assert review_exported_item["attribution_level"] == "not_reviewed"
    assert review_exported_item["attribution_manual_decisions"][0]["decision"] == "exported"

    duplicate_item = duplicate["items"][0]
    assert duplicate_item["article_id"] == "attr-dup-primary"
    assert duplicate_item["attribution_matched_article_title"] == "Matched Duplicate Title"
    assert same_group["items"][0]["article_id"] == "attr-dup-primary"
    assert same_group["items"][0]["attribution_matched_article_title"] is None
    assert missing == {
        "items": [],
        "limit": 20,
        "has_more": False,
        "next_ingested_at": None,
        "next_article_id": None,
    }
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
                    "score_feedback_type": "too_high",
                    "score_feedback_notes": None,
                    "archive_links": [
                        {
                            "item_id": "55555555-5555-5555-5555-555555555555",
                            "report_type": "wanbao",
                            "report_date": "2026-08-12",
                            "link_status": "matched",
                            "title": "晚报存档",
                            "body": "存档正文",
                            "source": "测试来源",
                        }
                    ],
                }
            ],
            "has_more": False,
            "next_ingested_at": None,
            "next_article_id": None,
        }


class _FakeAdapter:
    def __init__(self) -> None:
        self.news_summaries = _FakeNewsSummaries()


def test_service_preserves_missing_fields_and_zero_scores(monkeypatch) -> None:
    adapter = _FakeAdapter()
    monkeypatch.setattr(articles_service, "_get_adapter_safe", lambda: adapter)

    result = articles_service.search_articles(
        query="raw",
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
    archive_link = response["items"][0]["archive_links"][0]
    assert archive_link["report_type"] == "wanbao"
    assert archive_link["link_status"] == "matched"
    assert archive_link["report_date"] == date(2026, 8, 12)
    assert response["items"][0]["score_feedback"] == {
        "feedback_type": "too_high",
        "notes": None,
    }
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
        )

    cursor.execute.assert_not_called()


def test_summary_search_expression_matches_trigram_index_definition() -> None:
    assert SUMMARY_SEARCH_TEXT_EXPRESSION == (
        "(coalesce(ns.title, '') || ' ' || coalesce(ns.llm_summary, '') || ' ' || "
        "coalesce(ns.content_markdown, ''))"
    )


def test_adapter_uses_limit_plus_one_without_count_or_offset() -> None:
    cursor = Mock(spec=psycopg.Cursor)
    cursor.fetchall.return_value = []

    result = search_article_attributions(
        cursor,
        query="education",
        fetched_after=datetime.now(timezone.utc),
        limit=10,
    )

    sql, params = cursor.execute.call_args.args
    assert "COUNT(*)" not in sql
    assert "OFFSET" not in sql
    assert "LIMIT %s" in sql
    assert params[-1] == 11
    assert result["has_more"] is False


def test_cursor_pagination_is_stable_and_has_no_duplicates() -> None:
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

            first, _ = _search(cur, "only", limit=3)
            second, _ = _search(
                cur,
                "only",
                limit=3,
                cursor_ingested_at=first["next_ingested_at"],
                cursor_article_id=first["next_article_id"],
            )

    first_ids = [item["article_id"] for item in first["items"]]
    second_ids = [item["article_id"] for item in second["items"]]
    assert first["has_more"] is True
    assert len(first_ids) == 3
    assert len(second_ids) == 3
    assert set(first_ids).isdisjoint(second_ids)


def test_service_cursor_round_trip_preserves_window_and_search(monkeypatch) -> None:
    adapter = _FakeAdapter()
    ingested_at = datetime(2026, 8, 12, 8, 30, tzinfo=timezone.utc)
    adapter.news_summaries.search_with_attribution = Mock(
        side_effect=[
            {
                "items": [],
                "has_more": True,
                "next_ingested_at": ingested_at,
                "next_article_id": "article-10",
            },
            {
                "items": [],
                "has_more": False,
                "next_ingested_at": None,
                "next_article_id": None,
            },
        ]
    )
    monkeypatch.setattr(articles_service, "_get_adapter_safe", lambda: adapter)

    first = articles_service.search_articles(query="raw", lookback_days=45)
    second = articles_service.search_articles(
        query="raw",
        lookback_days=45,
        cursor=first["next_cursor"],
    )

    second_kwargs = adapter.news_summaries.search_with_attribution.call_args_list[1].kwargs
    assert first["has_more"] is True
    assert second["window_start"] == first["window_start"]
    assert second_kwargs["cursor_ingested_at"] == ingested_at
    assert second_kwargs["cursor_article_id"] == "article-10"

    with pytest.raises(ValueError, match="does not match"):
        articles_service.search_articles(
            query="different",
            lookback_days=45,
            cursor=first["next_cursor"],
        )
