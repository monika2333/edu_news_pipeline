"""多词检索（空格分隔、逐词 AND）的行为测试。

覆盖三层：
- ``src.adapters.sql_search`` 的子句构造与 LIKE 转义；
- manual_reviews 检索路径的子句生成（每词一条 ILIKE、参数按字面转义）；
- 真实 Postgres 上的 AND 语义与转义语义（通配符不得当通配符用）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator, List, Tuple

import psycopg
import pytest
from psycopg.rows import dict_row

from src.adapters.db_postgres_core import PostgresAdapter
from src.adapters.db_postgres_manual_reviews import SEARCH_TEXT_EXPRESSION
from src.adapters.db_postgres_manual_reviews._filters import search_manual_candidates
from src.adapters.sql_search import (
    escape_like,
    ilike_all_clauses,
    ilike_any_clause,
)
from src.config import get_settings


class TestSqlSearchHelpers:
    def test_escape_like_escapes_backslash_and_wildcards(self) -> None:
        assert escape_like("a%b_c\\d") == "a\\%b\\_c\\\\d"

    def test_ilike_all_clauses_one_clause_and_pattern_per_term(self) -> None:
        clauses, params = ilike_all_clauses(SEARCH_TEXT_EXPRESSION, ["教育", "政策"])
        assert clauses == [f"{SEARCH_TEXT_EXPRESSION} ILIKE %s"] * 2
        assert params == ["%教育%", "%政策%"]

    def test_ilike_all_clauses_empty_terms_yield_no_clauses(self) -> None:
        assert ilike_all_clauses(SEARCH_TEXT_EXPRESSION, []) == ([], [])

    def test_ilike_any_clause_is_parenthesized_or_group(self) -> None:
        clause, params = ilike_any_clause("COALESCE(ns.llm_summary, '')", ["a", "b"])
        assert clause == "(COALESCE(ns.llm_summary, '') ILIKE %s OR COALESCE(ns.llm_summary, '') ILIKE %s)"
        assert params == ["%a%", "%b%"]


class _FakeSearchCursor:
    def __init__(self) -> None:
        self.queries: List[str] = []
        self.params: List[Tuple[Any, ...]] = []

    def execute(self, query: str, params: Tuple[Any, ...]) -> None:
        self.queries.append(query)
        self.params.append(params)

    def fetchone(self) -> dict[str, int]:
        return {"total": 0}

    def fetchall(self) -> List[dict[str, Any]]:
        return []


class TestSearchManualCandidatesClauseGeneration:
    def _run(self, terms: Any) -> _FakeSearchCursor:
        cur = _FakeSearchCursor()
        search_manual_candidates(
            cur,
            owner_user_id="owner-1",
            terms=terms,
            limit=10,
            offset=0,
        )
        return cur

    def test_multi_terms_produce_one_ilike_per_term_with_escaped_params(self) -> None:
        cur = self._run(["教育", "a%b_c"])
        assert len(cur.queries) == 2
        for query in cur.queries:
            assert query.count("ILIKE %s") == 2
        # 参数按字面转义：通配符不再生效
        assert cur.params[0][-2:] == ("%教育%", "%a\\%b\\_c%")

    def test_no_terms_produces_no_search_clause(self) -> None:
        cur = self._run(None)
        assert len(cur.queries) == 2
        for query in cur.queries:
            assert "ILIKE %s" not in query


OWNER_USER_ID = "00000000-0000-0000-0000-000000000201"


@pytest.fixture
def search_adapter() -> Iterator[PostgresAdapter]:
    settings = get_settings()
    conn = psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        dbname=settings.db_name,
        autocommit=True,
        row_factory=dict_row,
    )
    try:
        with conn.cursor() as cur:
            for table in ("console_users", "manual_reviews", "news_summaries", "score_feedbacks"):
                cur.execute(
                    f"CREATE TEMP TABLE {table} "
                    f"(LIKE public.{table} INCLUDING ALL) ON COMMIT PRESERVE ROWS"
                )
            cur.execute(
                "ALTER TABLE manual_reviews "
                "DROP CONSTRAINT IF EXISTS manual_reviews_article_id_key"
            )
            cur.execute(
                "ALTER TABLE manual_reviews ADD COLUMN IF NOT EXISTS owner_user_id uuid"
            )
        _seed_multi_term_rows(conn)
        yield PostgresAdapter(connection=conn)
    finally:
        conn.close()


def _seed_multi_term_rows(conn: psycopg.connection.Connection) -> None:
    rows = [
        # (article_id, title, llm_summary, source)
        ("mt-1", "alpha 教育政策", "beta 通知", None),  # 两词分落标题与摘要：AND 语义必须命中
        ("mt-2", "alpha 单独出现", None, None),  # 只含首词
        ("mt-3", "gamma", "beta", None),  # 只含次词
        ("mt-4", "a%b", None, None),  # LIKE 通配符字面样本
        ("mt-5", "aXb", None, None),  # 若 % 未转义会被 a%b 误命中
        ("mt-6", "c_d", None, None),  # LIKE 单字符通配符字面样本
        ("mt-7", "cXd", None, None),  # 若 _ 未转义会被 c_d 误命中
        ("mt-src", "来源命中标题", None, "srcqianlong"),  # 只有抓取来源含检索词
        ("mt-src-both", "bothword 标题词", None, "srcword 来源"),  # 标题词与来源词同行的样本
        ("mt-src-titleonly", "bothword 另一篇标题", None, None),  # 只有标题词
    ]
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO news_summaries (
                article_id, title, llm_summary, content_markdown, source, status,
                score, external_importance_score, sentiment_label,
                is_beijing_related, created_at, score_details
            )
            VALUES (%s, %s, %s, '', %s, 'ready_for_export', 90, 90, 'positive', true, %s, '{}'::jsonb)
            """,
            [
                (
                    article_id,
                    title,
                    summary,
                    source,
                    datetime(2025, 1, 1, tzinfo=timezone.utc),
                )
                for article_id, title, summary, source in rows
            ],
        )
        cur.executemany(
            """
            INSERT INTO manual_reviews (owner_user_id, article_id, status, version)
            VALUES (%s, %s, 'pending', 1)
            """,
            [(OWNER_USER_ID, article_id) for article_id, _, _, _ in rows],
        )


def _search_article_ids(adapter: PostgresAdapter, terms: List[str]) -> set[str]:
    items, _ = adapter.manual_reviews.search_candidates(
        owner_user_id=OWNER_USER_ID,
        terms=terms,
        limit=50,
        offset=0,
    )
    return {str(item["article_id"]) for item in items}


def test_multi_term_search_requires_every_term_to_hit(search_adapter: PostgresAdapter) -> None:
    assert _search_article_ids(search_adapter, ["alpha", "beta"]) == {"mt-1"}


def test_single_term_search_keeps_original_scope(search_adapter: PostgresAdapter) -> None:
    assert _search_article_ids(search_adapter, ["alpha"]) == {"mt-1", "mt-2"}


def test_percent_is_matched_literally_not_as_wildcard(search_adapter: PostgresAdapter) -> None:
    assert _search_article_ids(search_adapter, ["a%b"]) == {"mt-4"}


def test_underscore_is_matched_literally_not_as_wildcard(search_adapter: PostgresAdapter) -> None:
    assert _search_article_ids(search_adapter, ["c_d"]) == {"mt-6"}


def test_candidate_search_matches_fetch_source_term(search_adapter: PostgresAdapter) -> None:
    # 筛选页 / 放弃页 / 值班检索共用 SEARCH_TEXT_EXPRESSION：抓取来源也参与匹配
    assert _search_article_ids(search_adapter, ["srcqianlong"]) == {"mt-src"}


def test_source_and_title_terms_must_hit_the_same_row(search_adapter: PostgresAdapter) -> None:
    assert _search_article_ids(search_adapter, ["bothword"]) == {
        "mt-src-both",
        "mt-src-titleonly",
    }
    # 来源词在 mt-src-both 的 source 里，标题词在两篇的标题里，
    # 逐词 AND 要求同一行同时命中，否则只返回 mt-src-both
    assert _search_article_ids(search_adapter, ["bothword", "srcword"]) == {"mt-src-both"}
