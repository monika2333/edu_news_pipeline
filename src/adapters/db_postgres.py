from __future__ import annotations

import contextlib
import sys
from datetime import datetime, timezone, date
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Json

from src.config import get_settings
from src.domain import (
    ExportCandidate,
    PrimaryArticleForScoring,
    SummaryForScoring,
    ExternalFilterCandidate,
)

_CONNECTION: Optional[psycopg.Connection] = None
_ADAPTER: Optional["PostgresAdapter"] = None
_MISSING = object()


def _get_connection() -> psycopg.Connection:
    global _CONNECTION
    settings = get_settings()
    if _CONNECTION is None or _CONNECTION.closed:
        _CONNECTION = psycopg.connect(
            host=settings.db_host,
            port=settings.db_port,
            user=settings.db_user,
            password=settings.db_password,
            dbname=settings.db_name,
            autocommit=True,
        )
        schema = settings.db_schema or "public"
        with _CONNECTION.cursor() as cur:
            cur.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
    return _CONNECTION


class PostgresAdapter:
    """High-level helpers for interacting with the local PostgreSQL database."""

    def __init__(self, connection: Optional[psycopg.Connection] = None) -> None:
        self._settings = get_settings()
        self._schema = self._settings.db_schema or "public"
        self._conn = connection or _get_connection()

    def _conn_cursor(self):
        if self._conn.closed:
            self._conn = _get_connection()
        return self._conn.cursor(row_factory=dict_row)

    @contextlib.contextmanager
    def _cursor(self):
        cur = self._conn_cursor()
        try:
            yield cur
        finally:
            cur.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    # Toutiao articles (crawler storage)
    # ------------------------------------------------------------------
    # Legacy: kept for backward-compat in tests; now raw_articles is canonical
    def upsert_toutiao_articles(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        columns = [
            'token',
            'profile_url',
            'article_id',
            'title',
            'source',
            'publish_time',
            'publish_time_iso',
            'url',
            'summary',
            'comment_count',
            'digg_count',
            'content_markdown',
            'fetched_at',
        ]
        insert_sql = '''
            INSERT INTO raw_articles (token, profile_url, article_id, title, source,
                publish_time, publish_time_iso, url, summary, comment_count, digg_count,
                content_markdown, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (article_id) DO UPDATE
            SET token = EXCLUDED.token,
                profile_url = EXCLUDED.profile_url,
                title = EXCLUDED.title,
                source = EXCLUDED.source,
                publish_time = EXCLUDED.publish_time,
                publish_time_iso = EXCLUDED.publish_time_iso,
                url = EXCLUDED.url,
                summary = EXCLUDED.summary,
                comment_count = EXCLUDED.comment_count,
                digg_count = EXCLUDED.digg_count,
                content_markdown = EXCLUDED.content_markdown,
                fetched_at = EXCLUDED.fetched_at,
                updated_at = now()
        '''
        data = [tuple(row.get(col) for col in columns) for row in rows]
        with self._cursor() as cur:
            cur.executemany(insert_sql, data)
        return len(rows)


    # New canonical: raw feed upsert
    def upsert_raw_feed_rows(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        columns = [
            'token',
            'profile_url',
            'article_id',
            'title',
            'source',
            'publish_time',
            'publish_time_iso',
            'url',
            'summary',
            'comment_count',
            'digg_count',
            'fetched_at',
        ]
        insert_sql = '''
            INSERT INTO raw_articles (token, profile_url, article_id, title, source,
                publish_time, publish_time_iso, url, summary, comment_count, digg_count,
                fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (article_id) DO UPDATE
            SET token = EXCLUDED.token,
                profile_url = EXCLUDED.profile_url,
                title = EXCLUDED.title,
                source = EXCLUDED.source,
                publish_time = EXCLUDED.publish_time,
                publish_time_iso = EXCLUDED.publish_time_iso,
                url = EXCLUDED.url,
                summary = EXCLUDED.summary,
                comment_count = EXCLUDED.comment_count,
                digg_count = EXCLUDED.digg_count,
                fetched_at = EXCLUDED.fetched_at,
                updated_at = now()
        '''
        data = [tuple(row.get(col) for col in columns) for row in rows]
        with self._cursor() as cur:
            cur.executemany(insert_sql, data)
        return len(rows)

    # New canonical: raw details update
    def update_raw_article_details(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        columns = [
            'token',
            'profile_url',
            'title',
            'source',
            'publish_time',
            'publish_time_iso',
            'url',
            'summary',
            'comment_count',
            'digg_count',
            'content_markdown',
            'detail_fetched_at',
        ]
        update_sql = '''
            UPDATE raw_articles
            SET token = %s,
                profile_url = %s,
                title = %s,
                source = %s,
                publish_time = %s,
                publish_time_iso = %s,
                url = %s,
                summary = %s,
                comment_count = %s,
                digg_count = %s,
                content_markdown = %s,
                detail_fetched_at = %s,
                updated_at = now()
            WHERE article_id = %s
        '''
        missing: List[str] = []
        with self._cursor() as cur:
            for row in rows:
                article_id = str(row.get('article_id') or '')
                if not article_id:
                    raise ValueError('Detail update requires article_id')
                values = [row.get(col) for col in columns]
                cur.execute(update_sql, values + [article_id])
                if cur.rowcount == 0:
                    missing.append(article_id)
        if missing:
            missing_values = ', '.join(sorted(missing))
            raise ValueError(f'Missing feed rows for detail update: {missing_values}')
        return len(rows)



    def get_raw_articles_missing_content(self, article_ids: Sequence[str]) -> Set[str]:
        unique_ids = list({str(item) for item in article_ids if item})
        if not unique_ids:
            return set()
        query = (
            "SELECT article_id FROM raw_articles"
            " WHERE article_id = ANY(%s)"
            "   AND (content_markdown IS NULL OR LENGTH(TRIM(content_markdown)) = 0)"
        )
        with self._cursor() as cur:
            cur.execute(query, (unique_ids,))
            rows = cur.fetchall()
        return {str(row['article_id']) for row in rows if row.get('article_id')}

    def fetch_raw_articles_missing_content(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        query = [
            "SELECT token, profile_url, article_id, title, source, publish_time, publish_time_iso, url, summary,",
            "       comment_count, digg_count, fetched_at, detail_fetched_at",
            "FROM raw_articles",
            "WHERE content_markdown IS NULL OR LENGTH(TRIM(content_markdown)) = 0",
            "ORDER BY fetched_at ASC NULLS LAST",
        ]
        params: List[Any] = []
        if limit and limit > 0:
            query.append("LIMIT %s")
            params.append(limit)
        sql_query = " ".join(query)
        with self._cursor() as cur:
            cur.execute(sql_query, tuple(params))
            rows = cur.fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            fetched = record.get('fetched_at')
            if isinstance(fetched, datetime):
                record['fetched_at'] = fetched.isoformat()
            publish_iso = record.get('publish_time_iso')
            if isinstance(publish_iso, datetime):
                record['publish_time_iso'] = publish_iso.isoformat()
            detail_fetched = record.get('detail_fetched_at')
            if isinstance(detail_fetched, datetime):
                record['detail_fetched_at'] = detail_fetched.isoformat()
            result.append(record)
        return result

    # ------------------------------------------------------------------
    # Filtered articles (keyword hits)
    # ------------------------------------------------------------------
    def upsert_filtered_articles(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        columns = [
            "article_id",
            "keywords",
            "status",
            "title",
            "source",
            "publish_time",
            "publish_time_iso",
            "url",
            "content_markdown",
        ]
        prepared: List[Tuple[Any, ...]] = []
        for row in rows:
            article_id = str(row.get("article_id") or "").strip()
            if not article_id:
                continue
            keywords = row.get("keywords") or []
            normalized_keywords: List[str] = []
            seen: Set[str] = set()
            for kw in keywords:
                if not kw:
                    continue
                cleaned = str(kw).strip()
                if not cleaned or cleaned in seen:
                    continue
                seen.add(cleaned)
                normalized_keywords.append(cleaned)
            status_value = str(row.get("status") or "pending").strip() or "pending"
            prepared.append(
                (
                    article_id,
                    normalized_keywords,
                    status_value,
                    row.get("title"),
                    row.get("source"),
                    row.get("publish_time"),
                    row.get("publish_time_iso"),
                    row.get("url"),
                    str(row.get("content_markdown") or ""),
                )
            )
        if not prepared:
            return 0
        updates = [
            "keywords = EXCLUDED.keywords",
            "title = EXCLUDED.title",
            "source = EXCLUDED.source",
            "publish_time = EXCLUDED.publish_time",
            "publish_time_iso = EXCLUDED.publish_time_iso",
            "url = EXCLUDED.url",
            "content_markdown = EXCLUDED.content_markdown",
            "status = CASE WHEN filtered_articles.status IN ('pending', 'failed') OR filtered_articles.status IS NULL"
            " THEN EXCLUDED.status ELSE filtered_articles.status END",
            "updated_at = NOW()",
        ]
        query = f"""
            INSERT INTO filtered_articles ({', '.join(columns)})
            VALUES ({', '.join(['%s'] * len(columns))})
            ON CONFLICT (article_id) DO UPDATE SET {', '.join(updates)}
        """
        with self._cursor() as cur:
            cur.executemany(query, prepared)
        return len(prepared)

    def fetch_filtered_articles_for_hashing(self, limit: int) -> List[Dict[str, Any]]:
        query = """
            SELECT
                article_id,
                title,
                source,
                publish_time,
                publish_time_iso,
                url,
                content_markdown,
                keywords,
                status,
                primary_article_id,
                content_hash,
                simhash,
                simhash_bigint,
                simhash_band1,
                simhash_band2,
                simhash_band3,
                simhash_band4,
                inserted_at,
                updated_at
            FROM filtered_articles
            WHERE
                status IN ('pending', 'failed')
                OR content_hash IS NULL
                OR simhash IS NULL
                OR primary_article_id IS NULL
                OR simhash_bigint IS NULL
                OR simhash_band1 IS NULL
                OR simhash_band2 IS NULL
                OR simhash_band3 IS NULL
                OR simhash_band4 IS NULL
            ORDER BY inserted_at ASC
            LIMIT %s
        """
        with self._cursor() as cur:
            cur.execute(query, (max(1, limit),))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def fetch_filtered_articles_by_hashes(self, hashes: Sequence[str]) -> List[Dict[str, Any]]:
        ordered_hashes = [value for value in dict.fromkeys(hashes) if value]
        if not ordered_hashes:
            return []
        query = """
            SELECT
                article_id,
                title,
                source,
                publish_time,
                publish_time_iso,
                url,
                content_markdown,
                keywords,
                content_hash,
                simhash,
                primary_article_id,
                status,
                inserted_at,
                updated_at
            FROM filtered_articles
            WHERE content_hash = ANY(%s)
        """
        with self._cursor() as cur:
            cur.execute(query, (ordered_hashes,))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def update_filtered_article_features(self, updates: Sequence[Mapping[str, Any]]) -> int:
        if not updates:
            return 0
        prepared: List[Tuple[Any, ...]] = []
        for row in updates:
            article_id = str(row.get("article_id") or "").strip()
            if not article_id:
                continue
            prepared.append(
                (
                    row.get("content_hash"),
                    row.get("simhash"),
                    row.get("simhash_bigint"),
                    row.get("simhash_band1"),
                    row.get("simhash_band2"),
                    row.get("simhash_band3"),
                    row.get("simhash_band4"),
                    article_id,
                )
            )
        if not prepared:
            return 0
        query = """
            UPDATE filtered_articles
            SET
                content_hash = %s,
                simhash = %s,
                simhash_bigint = %s,
                simhash_band1 = %s,
                simhash_band2 = %s,
                simhash_band3 = %s,
                simhash_band4 = %s,
                status = CASE
                    WHEN status IN ('pending', 'failed') THEN 'hashed'
                    ELSE status
                END,
                updated_at = NOW()
            WHERE article_id = %s
        """
        with self._cursor() as cur:
            cur.executemany(query, prepared)
        return len(prepared)

    def fetch_filtered_articles_by_band(self, band_index: int, band_value: int, limit: int) -> List[Dict[str, Any]]:
        if band_index not in (1, 2, 3, 4):
            raise ValueError("band_index must be between 1 and 4")
        column_name = f"simhash_band{band_index}"
        query = f"""
            SELECT
                article_id,
                title,
                source,
                publish_time,
                publish_time_iso,
                url,
                content_markdown,
                keywords,
                status,
                primary_article_id,
                content_hash,
                simhash,
                simhash_bigint,
                inserted_at,
                updated_at
            FROM filtered_articles
            WHERE {column_name} = %s
            LIMIT %s
        """
        with self._cursor() as cur:
            cur.execute(query, (band_value, max(1, limit)))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def update_filtered_primary_ids(self, updates: Sequence[Mapping[str, Any]]) -> int:
        if not updates:
            return 0
        prepared: List[Tuple[Any, ...]] = []
        for row in updates:
            article_id = str(row.get("article_id") or "").strip()
            primary_id = str(row.get("primary_article_id") or "").strip()
            status_value = str(row.get("status") or "").strip()
            if not article_id or not primary_id or not status_value:
                continue
            prepared.append((primary_id, status_value, article_id))
        if not prepared:
            return 0
        query = """
            UPDATE filtered_articles
            SET
                primary_article_id = %s,
                status = %s,
                updated_at = NOW()
            WHERE article_id = %s
        """
        with self._cursor() as cur:
            cur.executemany(query, prepared)
        return len(prepared)

    def upsert_primary_articles(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        columns = [
            "article_id",
            "primary_article_id",
            "status",
            "score",
            "score_updated_at",
            "title",
            "source",
            "publish_time",
            "publish_time_iso",
            "url",
            "content_markdown",
            "keywords",
            "content_hash",
            "simhash",
        ]
        prepared: List[Tuple[Any, ...]] = []
        for row in rows:
            article_id = str(row.get("article_id") or "").strip()
            primary_article_id = str(row.get("primary_article_id") or "").strip()
            if not article_id or not primary_article_id:
                continue
            keywords = row.get("keywords") or []
            normalized_keywords: List[str] = []
            seen: Set[str] = set()
            for kw in keywords:
                if not kw:
                    continue
                cleaned = str(kw).strip()
                if not cleaned or cleaned in seen:
                    continue
                seen.add(cleaned)
                normalized_keywords.append(cleaned)
            prepared.append(
                (
                    article_id,
                    primary_article_id,
                    row.get("status", "pending"),
                    row.get("score"),
                    row.get("score_updated_at"),
                    row.get("title"),
                    row.get("source"),
                    row.get("publish_time"),
                    row.get("publish_time_iso"),
                    row.get("url"),
                    row.get("content_markdown"),
                    normalized_keywords,
                    row.get("content_hash"),
                    row.get("simhash"),
                )
            )
        if not prepared:
            return 0
        update_clauses = [
            "primary_article_id = EXCLUDED.primary_article_id",
            "title = EXCLUDED.title",
            "source = EXCLUDED.source",
            "publish_time = EXCLUDED.publish_time",
            "publish_time_iso = EXCLUDED.publish_time_iso",
            "url = EXCLUDED.url",
            "content_markdown = EXCLUDED.content_markdown",
            "keywords = EXCLUDED.keywords",
            "content_hash = EXCLUDED.content_hash",
            "simhash = EXCLUDED.simhash",
            "score = COALESCE(EXCLUDED.score, primary_articles.score)",
            "score_updated_at = COALESCE(EXCLUDED.score_updated_at, primary_articles.score_updated_at)",
            "status = CASE WHEN primary_articles.status IN ('pending', 'failed') THEN EXCLUDED.status ELSE primary_articles.status END",
            "updated_at = NOW()",
        ]
        query = f"""
            INSERT INTO primary_articles ({', '.join(columns)})
            VALUES ({', '.join(['%s'] * len(columns))})
            ON CONFLICT (article_id) DO UPDATE SET {', '.join(update_clauses)}
        """
        with self._cursor() as cur:
            cur.executemany(query, prepared)
        return len(prepared)


    def get_existing_raw_article_ids(self) -> Set[str]:
        ids: Set[str] = set()
        with self._cursor() as cur:
            cur.execute("SELECT article_id FROM raw_articles")
            for row in cur.fetchall():
                article_id = row.get('article_id')
                if article_id:
                    ids.add(str(article_id))
        return ids

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    @staticmethod
    def _article_hash(article_id: Optional[str], original_url: Optional[str], title: Optional[str]) -> str:
        import hashlib

        basis = "-".join(filter(None, (article_id, original_url, title)))
        if not basis:
            basis = datetime.now(timezone.utc).isoformat()
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()

    @staticmethod
    def _to_iso(publish_time: Optional[int]) -> Optional[str]:
        if publish_time is None:
            return None
        try:
            return datetime.fromtimestamp(int(publish_time), tz=timezone.utc).isoformat()
        except Exception:
            return None

    @staticmethod
    def _iso_datetime(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------
    def insert_pending_summary(
        self,
        article: Mapping[str, Any],
        *,
        keywords: Optional[Sequence[str]] = None,
        fetched_at: Optional[str] = None,
    ) -> None:
        article_id = str(article.get('article_id') or '').strip()
        if not article_id:
            raise ValueError('Pending summary insert requires article_id')
        payload: Dict[str, Any] = {
            'article_id': article_id,
            'title': article.get('title'),
            'source': article.get('source'),
            'publish_time': article.get('publish_time'),
            'publish_time_iso': article.get('publish_time_iso'),
            'url': article.get('url'),
            'content_markdown': article.get('content_markdown') or '',
            'fetched_at': fetched_at or article.get('fetched_at'),
            'summary_status': 'pending',
            'summary_attempted_at': None,
            'summary_fail_count': 0,
        }
        if keywords:
            deduped: List[str] = []
            for kw in keywords:
                if kw and kw not in deduped:
                    deduped.append(kw)
            if deduped:
                payload['llm_keywords'] = deduped
        columns = list(payload.keys())
        values = [payload[col] for col in columns]
        updates = [
            "title = EXCLUDED.title",
            "source = EXCLUDED.source",
            "publish_time = EXCLUDED.publish_time",
            "publish_time_iso = EXCLUDED.publish_time_iso",
            "url = EXCLUDED.url",
            "content_markdown = EXCLUDED.content_markdown",
            "fetched_at = COALESCE(EXCLUDED.fetched_at, news_summaries.fetched_at)",
            "llm_keywords = CASE WHEN EXCLUDED.llm_keywords IS NULL OR array_length(EXCLUDED.llm_keywords, 1) = 0 THEN news_summaries.llm_keywords ELSE EXCLUDED.llm_keywords END",
            "summary_status = CASE WHEN news_summaries.summary_status = 'completed' THEN news_summaries.summary_status ELSE EXCLUDED.summary_status END",
            "summary_attempted_at = CASE WHEN news_summaries.summary_status = 'completed' THEN news_summaries.summary_attempted_at ELSE EXCLUDED.summary_attempted_at END",
            "summary_fail_count = CASE WHEN news_summaries.summary_status = 'completed' THEN news_summaries.summary_fail_count ELSE EXCLUDED.summary_fail_count END",
        ]
        query = f"""
            INSERT INTO news_summaries ({', '.join(columns)})
            VALUES ({', '.join(['%s'] * len(columns))})
            ON CONFLICT (article_id) DO UPDATE
            SET {', '.join(updates)}
            WHERE news_summaries.summary_status <> 'completed'
        """
        with self._cursor() as cur:
            cur.execute(query, values)

    def fetch_pending_summaries(
        self,
        limit: Optional[int] = None,
        *,
        max_attempts: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        clauses = ["summary_status = 'pending'", "status = 'pending'"]
        params: List[Any] = []
        if max_attempts is not None:
            clauses.append("summary_fail_count < %s")
            params.append(max_attempts)
        where_sql = ' AND '.join(clauses)
        query_parts = [
            "SELECT article_id, title, source, publish_time, publish_time_iso, url, content_markdown,",
            "       fetched_at, summary_attempted_at, summary_fail_count, llm_keywords",
            "FROM news_summaries",
            f"WHERE {where_sql}",
            "ORDER BY summary_attempted_at ASC NULLS FIRST, fetched_at ASC NULLS LAST, article_id ASC",
        ]
        if limit and limit > 0:
            query_parts.append("LIMIT %s")
            params.append(limit)
        query = " ".join(query_parts)
        with self._cursor() as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            for field in ('fetched_at', 'summary_attempted_at', 'publish_time_iso'):
                value = record.get(field)
                if isinstance(value, datetime):
                    record[field] = value.isoformat()
            result.append(record)
        return result

    def mark_summary_attempt(self, article_id: str) -> bool:
        if not article_id:
            return False
        query = """
            UPDATE news_summaries
            SET summary_attempted_at = NOW(),
                summary_fail_count = summary_fail_count + 1
            WHERE article_id = %s
              AND summary_status = 'pending'
              AND status = 'pending'
        """
        with self._cursor() as cur:
            cur.execute(query, (article_id,))
            return cur.rowcount == 1

    def complete_summary(
        self,
        article_id: str,
        summary_text: str,
        *,
        llm_source: Optional[str] = None,
        keywords: Optional[Sequence[str]] = None,
        beijing_related: Optional[bool] = None,
        sentiment_label: Optional[str] = None,
        sentiment_confidence: Optional[float] = None,
        status: str = "ready_for_export",
        external_importance_status: Any = _MISSING,
        external_importance_score: Any = _MISSING,
        external_importance_checked_at: Any = _MISSING,
        external_importance_raw: Any = _MISSING,
        external_filter_attempted_at: Any = _MISSING,
        external_filter_fail_count: Any = _MISSING,
    ) -> None:
        if not article_id:
            raise ValueError('complete_summary requires article_id')
        payload: Dict[str, Any] = {
            'llm_summary': summary_text,
            'summary_status': 'completed',
            'summary_generated_at': datetime.now(timezone.utc).isoformat(),
            'summary_attempted_at': datetime.now(timezone.utc).isoformat(),
            'status': status,
        }
        if llm_source is not None:
            payload['llm_source'] = llm_source
        if keywords:
            deduped: List[str] = []
            for kw in keywords:
                if kw and kw not in deduped:
                    deduped.append(kw)
            if deduped:
                payload['llm_keywords'] = deduped
        if beijing_related is not None:
            payload['is_beijing_related'] = beijing_related
        if sentiment_label is not None:
            payload['sentiment_label'] = sentiment_label
        if sentiment_confidence is not None:
            payload['sentiment_confidence'] = float(sentiment_confidence)
        def _maybe_set(field: str, value: Any) -> None:
            if value is not _MISSING:
                payload[field] = value

        _maybe_set('external_importance_status', external_importance_status)
        _maybe_set('external_importance_score', external_importance_score)
        _maybe_set('external_importance_checked_at', external_importance_checked_at)
        _maybe_set('external_importance_raw', Json(external_importance_raw) if (external_importance_raw is not _MISSING and external_importance_raw is not None) else external_importance_raw)
        _maybe_set('external_filter_attempted_at', external_filter_attempted_at)
        _maybe_set('external_filter_fail_count', external_filter_fail_count)
        sets = ', '.join(f"{field} = %s" for field in payload)
        values = list(payload.values()) + [article_id]
        query = f"""
            UPDATE news_summaries
            SET {sets}
            WHERE article_id = %s
        """
        with self._cursor() as cur:
            cur.execute(query, values)
            if cur.rowcount != 1:
                raise ValueError(f'Unable to complete summary for {article_id}')

    def mark_summary_failed(self, article_id: str, *, message: Optional[str] = None) -> None:
        if not article_id:
            return
        query = """
            UPDATE news_summaries
            SET summary_status = 'failed',
                status = 'failed'
            WHERE article_id = %s
              AND summary_status = 'pending'
              AND status = 'pending'
        """
        with self._cursor() as cur:
            cur.execute(query, (article_id,))
        if message:
            print(f"[warn] summary failed {article_id}: {message}", file=sys.stderr)

    def fetch_external_filter_candidates(
        self,
        limit: int,
        *,
        max_failures: Optional[int] = None,
    ) -> List[ExternalFilterCandidate]:
        if limit <= 0:
            return []
        clauses = [
            "status = 'pending_external_filter'",
            "external_importance_status = 'pending_external_filter'",
            "summary_status = 'completed'",
        ]
        params: List[Any] = []
        if max_failures is not None:
            clauses.append("external_filter_fail_count < %s")
            params.append(max_failures)
        where_sql = " AND ".join(clauses)
        query = f"""
            SELECT
                article_id,
                title,
                source,
                publish_time_iso,
                llm_summary,
                content_markdown,
                sentiment_label,
                is_beijing_related,
                external_importance_status,
                external_filter_fail_count
            FROM news_summaries
            WHERE {where_sql}
            ORDER BY external_filter_attempted_at ASC NULLS FIRST,
                     summary_generated_at ASC NULLS LAST,
                     article_id ASC
            LIMIT %s
        """
        params.append(limit)
        with self._cursor() as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()
        results: List[ExternalFilterCandidate] = []
        for row in rows:
            article_id = row.get("article_id")
            if not article_id:
                continue
            results.append(
                ExternalFilterCandidate(
                    article_id=str(article_id),
                    title=row.get("title"),
                    source=row.get("source"),
                    publish_time_iso=self._iso_datetime(row.get("publish_time_iso")),
                    summary=row.get("llm_summary") or "",
                    content=row.get("content_markdown") or "",
                    sentiment_label=row.get("sentiment_label"),
                    is_beijing_related=row.get("is_beijing_related"),
                    external_importance_status=row.get("external_importance_status") or "pending_external_filter",
                    external_filter_fail_count=int(row.get("external_filter_fail_count") or 0),
                )
            )
        return results

    def complete_external_filter(
        self,
        article_id: str,
        *,
        passed: bool,
        score: int,
        raw_output: str,
    ) -> None:
        if not article_id:
            raise ValueError("complete_external_filter requires article_id")
        target_status = "ready_for_export" if passed else "external_filtered"
        timestamp = datetime.now(timezone.utc)
        payload = {
            "status": target_status,
            "external_importance_status": target_status,
            "external_importance_score": score,
            "external_importance_checked_at": timestamp,
            "external_importance_raw": Json(
                {
                    "model_output": raw_output,
                    "decided_at": timestamp.isoformat(),
                }
            ),
            "external_filter_attempted_at": timestamp,
            "external_filter_fail_count": 0,
        }
        sets = ", ".join(f"{field} = %s" for field in payload)
        values = list(payload.values()) + [article_id]
        query = f"""
            UPDATE news_summaries
            SET {sets}
            WHERE article_id = %s
        """
        with self._cursor() as cur:
            cur.execute(query, values)
            if cur.rowcount != 1:
                raise ValueError(f"Unable to update external filter status for {article_id}")

    def mark_external_filter_failure(
        self,
        article_id: str,
        *,
        fail_count: int,
        final_failure: bool,
        error: str,
    ) -> None:
        if not article_id:
            return
        timestamp = datetime.now(timezone.utc)
        payload: Dict[str, Any] = {
            "external_filter_fail_count": fail_count,
            "external_filter_attempted_at": timestamp,
            "external_importance_raw": Json(
                {
                    "error": str(error)[:500],
                    "recorded_at": timestamp.isoformat(),
                }
            ),
        }
        if final_failure:
            payload.update(
                {
                    "status": "external_filtered",
                    "external_importance_status": "external_filtered",
                    "external_importance_checked_at": timestamp,
                    "external_importance_score": None,
                }
            )
        sets = ", ".join(f"{field} = %s" for field in payload)
        values = list(payload.values()) + [article_id]
        query = f"""
            UPDATE news_summaries
            SET {sets}
            WHERE article_id = %s
        """
        with self._cursor() as cur:
            cur.execute(query, values)
    def fetch_external_backfill_candidates(self, limit: int, since_date: Optional[date] = None) -> List[Dict[str, Any]]:
        if limit <= 0:
            return []
        # Build query with optional date filter on publish_time_iso (date-level)
        parts: List[str] = [
            "SELECT",
            "    article_id,",
            "    title,",
            "    publish_time_iso,",
            "    summary_generated_at,",
            "    sentiment_label",
            "FROM news_summaries",
            "WHERE status = 'ready_for_export'",
            "  AND summary_status = 'completed'",
            "  AND (is_beijing_related IS DISTINCT FROM TRUE)",
            "  AND lower(coalesce(sentiment_label, '')) = 'positive'",
            "  AND (external_importance_status IS NULL OR external_importance_status NOT IN ('pending_external_filter'))",
        ]
        params: List[Any] = []
        if since_date is not None:
            parts.append("  AND publish_time_iso::date >= %s")
            params.append(since_date)
        parts.extend([
            "ORDER BY summary_generated_at ASC NULLS LAST, article_id ASC",
            "LIMIT %s",
        ])
        params.append(limit)
        query = "\n".join(parts)
        with self._cursor() as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def reset_external_filter_pending(self, article_ids: Sequence[str]) -> int:
        if not article_ids:
            return 0
        query = """
            UPDATE news_summaries
            SET status = 'pending_external_filter',
                external_importance_status = 'pending_external_filter',
                external_importance_score = NULL,
                external_importance_checked_at = NULL,
                external_importance_raw = NULL,
                external_filter_attempted_at = NULL,
                external_filter_fail_count = 0,
                updated_at = NOW()
            WHERE article_id = ANY(%s)
        """
        with self._cursor() as cur:
            cur.execute(query, (list(article_ids),))
            return cur.rowcount

    def fetch_raw_articles_for_summary(
        self,
        *,
        after_fetched_at: Optional[str],
        limit: Optional[int],
    ) -> List[Dict[str, Any]]:
        fetch_target = max(1, (limit or 50))
        base_query = [
            "SELECT article_id, title, source, publish_time, publish_time_iso, url, content_markdown, fetched_at, detail_fetched_at",
            "FROM raw_articles",
            "WHERE content_markdown IS NOT NULL AND LENGTH(TRIM(content_markdown)) > 0",
            "  AND detail_fetched_at IS NOT NULL",
        ]
        params: List[Any] = []
        if after_fetched_at:
            base_query.append("AND fetched_at >= %s")
            params.append(after_fetched_at)
        base_query.append("ORDER BY fetched_at ASC")
        base_query.append("LIMIT %s")
        params.append(fetch_target)
        query = " ".join(base_query)
        with self._cursor() as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            fetched = record.get('fetched_at')
            if isinstance(fetched, datetime):
                record['fetched_at'] = fetched.isoformat()
            publish_iso = record.get('publish_time_iso')
            if isinstance(publish_iso, datetime):
                record['publish_time_iso'] = publish_iso.isoformat()
            detail_fetched = record.get('detail_fetched_at')
            if isinstance(detail_fetched, datetime):
                record['detail_fetched_at'] = detail_fetched.isoformat()
            result.append(record)
        return result

    # ------------------------------------------------------------------
    # Backward-compat wrappers (to be removed after refactor)
    # ------------------------------------------------------------------
    def upsert_toutiao_feed_rows(self, rows: Sequence[Mapping[str, Any]]) -> int:
        return self.upsert_raw_feed_rows(rows)

    def update_toutiao_article_details(self, rows: Sequence[Mapping[str, Any]]) -> int:
        return self.update_raw_article_details(rows)

    def get_toutiao_articles_missing_content(self, article_ids: Sequence[str]) -> Set[str]:
        return self.get_raw_articles_missing_content(article_ids)

    def fetch_toutiao_articles_missing_content(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        return self.fetch_raw_articles_missing_content(limit)

    def get_existing_toutiao_article_ids(self) -> Set[str]:
        return self.get_existing_raw_article_ids()

    def fetch_toutiao_articles_for_summary(
        self,
        *,
        after_fetched_at: Optional[str],
        limit: Optional[int],
    ) -> List[Dict[str, Any]]:
        return self.fetch_raw_articles_for_summary(after_fetched_at=after_fetched_at, limit=limit)

    def get_existing_news_summary_ids(self, article_ids: Sequence[str]) -> Set[str]:
        unique_ids = list({str(item) for item in article_ids if item})
        if not unique_ids:
            return set()
        query = "SELECT article_id FROM news_summaries WHERE article_id = ANY(%s)"
        with self._cursor() as cur:
            cur.execute(query, (unique_ids,))
            rows = cur.fetchall()
        return {str(row["article_id"]) for row in rows if row.get("article_id")}

    def upsert_news_summary(
        self,
        article: Dict[str, Any],
        summary: str,
        *,
        keywords: Optional[Sequence[str]] = None,
    ) -> None:
        article_id = str(article.get("article_id") or "")
        if not article_id:
            raise ValueError("Postgres upsert requires article_id")
        content_value = article.get("content_markdown")
        if content_value is None:
            content_value = ""
        payload: Dict[str, Any] = {
            "article_id": article_id,
            "title": article.get("title"),
            "source": article.get("source"),
            "publish_time": article.get("publish_time"),
            "publish_time_iso": article.get("publish_time_iso"),
            "url": article.get("url"),
            "content_markdown": str(content_value),
            "llm_summary": summary,
            "summary_generated_at": datetime.now(timezone.utc).isoformat(),
        }
        llm_source_value = article.get("llm_source")
        if llm_source_value is not None:
            payload["llm_source"] = str(llm_source_value).strip()
        fetched_at = article.get("fetched_at")
        if fetched_at:
            payload["fetched_at"] = fetched_at
        if keywords:
            deduped = []
            for kw in keywords:
                if kw and kw not in deduped:
                    deduped.append(kw)
            if deduped:
                payload["llm_keywords"] = deduped
        columns = list(payload.keys())
        values = [payload[col] for col in columns]
        updates = [f"{col} = EXCLUDED.{col}" for col in columns if col != "article_id"]
        query = f"""
            INSERT INTO news_summaries ({', '.join(columns)})
            VALUES ({', '.join(['%s'] * len(columns))})
            ON CONFLICT (article_id) DO UPDATE SET {', '.join(updates)}
        """
        with self._cursor() as cur:
            try:
                cur.execute(query, values)
            except psycopg.DatabaseError as exc:
                message = str(exc)
                if "fetched_at" in message and "news_summaries" in message:
                    # Retry without fetched_at to mimic previous remote behaviour
                    filtered_columns = [c for c in columns if c != "fetched_at"]
                    filtered_values = [payload[c] for c in filtered_columns]
                    filtered_updates = [f"{col} = EXCLUDED.{col}" for col in filtered_columns if col != "article_id"]
                    retry_query = f"""
                        INSERT INTO news_summaries ({', '.join(filtered_columns)})
                        VALUES ({', '.join(['%s'] * len(filtered_columns))})
                        ON CONFLICT (article_id) DO UPDATE SET {', '.join(filtered_updates)}
                    """
                    cur.execute(retry_query, filtered_values)
                else:
                    raise

    # ------------------------------------------------------------------
    # Scoring helpers (news_summaries)
    # ------------------------------------------------------------------
    def update_summary_score(self, article_id: str, score: Optional[float]) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE news_summaries SET score = %s, updated_at = NOW() WHERE article_id = %s",
                (score, article_id),
            )

    def fetch_primary_articles_for_scoring(self, limit: int) -> List[PrimaryArticleForScoring]:
        query = """
            SELECT
                article_id,
                primary_article_id,
                status,
                score,
                raw_relevance_score,
                keyword_bonus_score,
                score_details,
                title,
                source,
                publish_time,
                publish_time_iso,
                url,
                content_markdown,
                keywords,
                content_hash,
                simhash,
                created_at
            FROM primary_articles
            WHERE status IN ('pending', 'failed')
               OR score IS NULL
            ORDER BY created_at ASC
            LIMIT %s
        """
        with self._cursor() as cur:
            cur.execute(query, (max(1, limit),))
            rows = cur.fetchall()
        results: List[PrimaryArticleForScoring] = []
        for row in rows:
            article_id = row.get("article_id")
            content = row.get("content_markdown")
            if not article_id or content is None:
                continue
            keywords = row.get("keywords") or []
            if keywords is None:
                keywords = []
            score_details = row.get("score_details") or {}
            if isinstance(score_details, list):
                score_details = {}
            results.append(
                PrimaryArticleForScoring(
                    article_id=str(article_id),
                    content=str(content),
                    title=row.get("title"),
                    source=row.get("source"),
                    publish_time=row.get("publish_time"),
                    publish_time_iso=row.get("publish_time_iso"),
                    url=row.get("url"),
                    keywords=list(keywords),
                    content_hash=row.get("content_hash"),
                    simhash=row.get("simhash"),
                    raw_relevance_score=row.get("raw_relevance_score"),
                    keyword_bonus_score=row.get("keyword_bonus_score"),
                    score_details=score_details,
                )
            )
        return results

    def update_primary_article_scores(self, updates: Sequence[Mapping[str, Any]]) -> int:
        if not updates:
            return 0
        prepared: List[Tuple[Any, ...]] = []
        for row in updates:
            article_id = str(row.get("article_id") or "").strip()
            if not article_id:
                continue
            score_details = row.get("score_details")
            if score_details is None:
                score_details = {}
            prepared.append(
                (
                    row.get("score"),
                    row.get("raw_relevance_score"),
                    row.get("keyword_bonus_score"),
                    Json(score_details),
                    row.get("status") or "pending",
                    article_id,
                )
            )
        if not prepared:
            return 0
        query = """
            UPDATE primary_articles
            SET
                score = %s,
                raw_relevance_score = %s,
                keyword_bonus_score = %s,
                score_details = %s,
                status = %s,
                score_updated_at = NOW(),
                updated_at = NOW()
            WHERE article_id = %s
        """
        with self._cursor() as cur:
            cur.executemany(query, prepared)
        return len(prepared)

    def upsert_news_summaries_from_primary(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        columns = [
            "article_id",
            "title",
            "source",
            "publish_time",
            "publish_time_iso",
            "url",
            "content_markdown",
            "score",
            "raw_relevance_score",
            "keyword_bonus_score",
            "score_details",
            "status",
            "llm_keywords",
        ]
        prepared: List[Tuple[Any, ...]] = []
        for row in rows:
            article_id = str(row.get("article_id") or "").strip()
            if not article_id:
                continue
            keywords = row.get("keywords") or []
            deduped: List[str] = []
            seen: Set[str] = set()
            for kw in keywords:
                if not kw:
                    continue
                cleaned = str(kw).strip()
                if not cleaned or cleaned in seen:
                    continue
                seen.add(cleaned)
                deduped.append(cleaned)
            score_details = row.get("score_details")
            if score_details is None:
                score_details = {}
            prepared.append(
                (
                    article_id,
                    row.get("title"),
                    row.get("source"),
                    row.get("publish_time"),
                    row.get("publish_time_iso"),
                    row.get("url"),
                    row.get("content_markdown"),
                    row.get("score"),
                    row.get("raw_relevance_score"),
                    row.get("keyword_bonus_score"),
                    Json(score_details),
                    row.get("status") or "pending",
                    deduped,
                )
            )
        if not prepared:
            return 0
        update_parts = [
            "title = EXCLUDED.title",
            "source = EXCLUDED.source",
            "publish_time = EXCLUDED.publish_time",
            "publish_time_iso = EXCLUDED.publish_time_iso",
            "url = EXCLUDED.url",
            "content_markdown = EXCLUDED.content_markdown",
            "score = EXCLUDED.score",
            "raw_relevance_score = EXCLUDED.raw_relevance_score",
            "keyword_bonus_score = EXCLUDED.keyword_bonus_score",
            "score_details = EXCLUDED.score_details",
            "llm_keywords = EXCLUDED.llm_keywords",
            "status = CASE WHEN news_summaries.status IN ('pending', 'failed') THEN EXCLUDED.status ELSE news_summaries.status END",
            "updated_at = NOW()",
        ]
        query = f"""
            INSERT INTO news_summaries ({', '.join(columns)})
            VALUES ({', '.join(['%s'] * len(columns))})
            ON CONFLICT (article_id) DO UPDATE SET {', '.join(update_parts)}
        """
        with self._cursor() as cur:
            cur.executemany(query, prepared)
        return len(prepared)

    def fetch_beijing_tag_candidates(self, limit: int) -> List[Dict[str, Any]]:
        query = """
            SELECT
                article_id,
                content_markdown,
                llm_summary,
                llm_keywords
            FROM news_summaries
            WHERE is_beijing_related IS NULL
            ORDER BY summary_generated_at ASC NULLS LAST
            LIMIT %s
        """
        with self._cursor() as cur:
            cur.execute(query, (max(1, limit),))
            rows = cur.fetchall()
        results: List[Dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            article_id = record.get("article_id")
            if not article_id:
                continue
            results.append(record)
        return results

    def update_beijing_related_bulk(self, updates: Sequence[Tuple[str, bool]]) -> int:
        if not updates:
            return 0
        payload = []
        for article_id, value in updates:
            if not article_id:
                continue
            payload.append((value, str(article_id)))
        if not payload:
            return 0
        with self._cursor() as cur:
            cur.executemany(
                "UPDATE news_summaries SET is_beijing_related = %s, updated_at = NOW() WHERE article_id = %s",
                payload,
            )
        return len(payload)

    # ------------------------------------------------------------------
    # Export helpers
    # ------------------------------------------------------------------
    def fetch_export_candidates(self, min_score: float) -> List[ExportCandidate]:
        query = """
            SELECT
                article_id,
                title,
                llm_summary,
                content_markdown,
                score,
                raw_relevance_score,
                keyword_bonus_score,
                score_details,
                url,
                source,
                publish_time_iso,
                publish_time,
                llm_source,
                sentiment_label,
                sentiment_confidence,
                is_beijing_related,
                status,
                summary_status,
                external_importance_score,
                external_importance_checked_at
            FROM news_summaries
            WHERE status = 'ready_for_export'
              AND summary_status = 'completed'
              AND score IS NOT NULL
              AND score >= %s
            ORDER BY score DESC NULLS LAST, publish_time_iso DESC NULLS LAST, article_id ASC
        """
        with self._cursor() as cur:
            cur.execute(query, (min_score,))
            rows = cur.fetchall()
        out: List[ExportCandidate] = []
        for row in rows:
            article_id = str(row.get("article_id") or "")
            if not article_id:
                continue
            title = row.get("title")
            summary_text = row.get("llm_summary") or ""
            content = row.get("content_markdown") or ""
            score_value = float(row.get("score") or 0.0)
            url = row.get("url")
            published_at = row.get("publish_time_iso") or row.get("publish_time")
            if isinstance(published_at, datetime):
                published_at = published_at.isoformat()
            source_name = row.get("source")
            article_hash = self._article_hash(article_id, url, title)
            score_details = row.get("score_details") or {}
            if isinstance(score_details, list):
                score_details = {}
            out.append(
                ExportCandidate(
                    filtered_article_id=article_id,
                    raw_article_id=article_id,
                    article_hash=article_hash,
                    title=title,
                    summary=str(summary_text),
                    content=str(content),
                    source=source_name,
                    llm_source=row.get("llm_source"),
                    score=score_value,
                    original_url=url,
                    published_at=published_at,
                    sentiment_label=row.get("sentiment_label"),
                    sentiment_confidence=row.get("sentiment_confidence"),
                    is_beijing_related=row.get("is_beijing_related"),
                    raw_relevance_score=row.get("raw_relevance_score"),
                    keyword_bonus_score=row.get("keyword_bonus_score"),
                    score_details=score_details,
                    external_importance_score=row.get("external_importance_score"),
                    external_importance_checked_at=self._iso_datetime(row.get("external_importance_checked_at")),
                )
            )
        return out

    def _get_batch_by_tag(self, report_tag: str) -> Optional[Dict[str, Any]]:
        query = """
            SELECT id, report_date, sequence_no, export_payload
            FROM brief_batches
            WHERE generated_by = %s
            LIMIT 1
        """
        with self._cursor() as cur:
            cur.execute(query, (report_tag,))
            row = cur.fetchone()
        return dict(row) if row else None

    def _parse_report_tag(self, report_tag: str) -> Tuple[date, str]:
        try:
            parts = report_tag.split("-")
            if len(parts) >= 3:
                y, m, d = parts[0:3]
                report_date = date(int(y), int(m), int(d))
                suffix = "-".join(parts[3:]) if len(parts) > 3 else ""
                return report_date, suffix
        except Exception:
            pass
        return datetime.now(timezone.utc).date(), report_tag

    def _create_batch(self, report_tag: str) -> Dict[str, Any]:
        report_date, suffix = self._parse_report_tag(report_tag)
        fetch_query = """
            SELECT sequence_no
            FROM brief_batches
            WHERE report_date = %s
            ORDER BY sequence_no DESC
            LIMIT 1
        """
        with self._cursor() as cur:
            cur.execute(fetch_query, (report_date.isoformat(),))
            row = cur.fetchone()
            next_seq = 1
            if row:
                try:
                    next_seq = int(row["sequence_no"]) + 1
                except Exception:
                    next_seq = 1
            payload = {
                "report_date": report_date.isoformat(),
                "sequence_no": next_seq,
                "generated_by": report_tag,
                "export_payload": Json({"report_tag": report_tag, "suffix": suffix}),
            }
            cur.execute(
                """
                INSERT INTO brief_batches (report_date, sequence_no, generated_by, export_payload)
                VALUES (%s, %s, %s, %s)
                RETURNING id, report_date, sequence_no, export_payload
                """,
                (
                    payload["report_date"],
                    payload["sequence_no"],
                    payload["generated_by"],
                    payload["export_payload"],
                ),
            )
            created = cur.fetchone()
            if not created:
                raise RuntimeError("Failed to create brief batch")
            return dict(created)

    def get_export_history(self, report_tag: str) -> Tuple[Set[str], Optional[str]]:
        batch = self._get_batch_by_tag(report_tag)
        if not batch:
            return set(), None
        batch_id = str(batch["id"])
        query = "SELECT article_id FROM brief_items WHERE brief_batch_id = %s"
        with self._cursor() as cur:
            cur.execute(query, (batch_id,))
            rows = cur.fetchall()
        ids = {str(row.get("article_id")) for row in rows if row.get("article_id")}
        return ids, batch_id

    def get_all_exported_article_ids(self) -> Set[str]:
        batch_size = 1000
        start = 0
        seen: Set[str] = set()
        while True:
            query = """
                SELECT article_id
                FROM brief_items
                ORDER BY id
                OFFSET %s LIMIT %s
            """
            with self._cursor() as cur:
                cur.execute(query, (start, batch_size))
                rows = cur.fetchall()
            if not rows:
                break
            for row in rows:
                article_id = row.get("article_id")
                if article_id:
                    seen.add(str(article_id))
            if len(rows) < batch_size:
                break
            start += batch_size
        return seen

    def record_export(
        self,
        report_tag: str,
        exported: Sequence[Tuple[ExportCandidate, str]],
        *,
        output_path: str,
    ) -> None:
        existing_ids, batch_id = self.get_export_history(report_tag)
        if batch_id is None:
            batch = self._create_batch(report_tag)
            batch_id = str(batch["id"])
        with self._cursor() as cur:
            cur.execute(
                "UPDATE brief_batches SET export_payload = %s, updated_at = NOW() WHERE id = %s",
                (Json({"report_tag": report_tag, "output_path": output_path}), batch_id),
            )
            order_index_start = 0
            if existing_ids:
                cur.execute(
                    """
                    SELECT order_index
                    FROM brief_items
                    WHERE brief_batch_id = %s
                    ORDER BY order_index DESC
                    LIMIT 1
                    """,
                    (batch_id,),
                )
                row = cur.fetchone()
                if row:
                    try:
                        order_index_start = int(row["order_index"]) + 1
                    except Exception:
                        order_index_start = 0
            insert_payload: List[Tuple[Any, ...]] = []
            for offset, (candidate, section) in enumerate(exported):
                article_id = candidate.filtered_article_id
                if article_id in existing_ids:
                    continue
                metadata = {
                    "title": self._json_safe(candidate.title),
                    "score": self._json_safe(candidate.score),
                    "original_url": self._json_safe(candidate.original_url),
                    "published_at": self._json_safe(candidate.published_at),
                    "source": self._json_safe(candidate.source),
                    "is_beijing_related": self._json_safe(candidate.is_beijing_related),
                    "sentiment_label": self._json_safe(candidate.sentiment_label),
                    "sentiment_confidence": self._json_safe(candidate.sentiment_confidence),
                    "external_importance_score": self._json_safe(candidate.external_importance_score),
                    "external_importance_checked_at": self._json_safe(candidate.external_importance_checked_at),
                }
                insert_payload.append(
                    (
                        batch_id,
                        article_id,
                        section,
                        order_index_start + offset,
                        candidate.summary,
                        Json(metadata),
                    )
                )
            if insert_payload:
                cur.executemany(
                    """
                    INSERT INTO brief_items (
                        brief_batch_id,
                        article_id,
                        section,
                        order_index,
                        final_summary,
                        metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    insert_payload,
                )

    def fetch_latest_brief_batch(self) -> Optional[Dict[str, Any]]:
        query = """
            SELECT *
            FROM brief_batches
            ORDER BY report_date DESC, sequence_no DESC
            LIMIT 1
        """
        with self._cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()
        return dict(row) if row else None

    def fetch_brief_items_by_batch(self, batch_id: str) -> List[Dict[str, Any]]:
        query = """
            SELECT id, article_id, section, order_index, final_summary, metadata
            FROM brief_items
            WHERE brief_batch_id = %s
            ORDER BY order_index ASC
        """
        with self._cursor() as cur:
            cur.execute(query, (batch_id,))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def fetch_brief_item_count(self, batch_id: str) -> int:
        query = "SELECT COUNT(*) AS total FROM brief_items WHERE brief_batch_id = %s"
        with self._cursor() as cur:
            cur.execute(query, (batch_id,))
            row = cur.fetchone()
        return int(row["total"]) if row else 0

    # ------------------------------------------------------------------
    # Pipeline run metadata
    # ------------------------------------------------------------------
    def record_pipeline_run_start(
        self,
        *,
        run_id: str,
        started_at: datetime,
        plan: Sequence[str],
        trigger_source: Optional[str] = None,
    ) -> None:
        payload = {
            "run_id": run_id,
            "status": "running",
            "trigger_source": trigger_source,
            "plan": plan,
            "started_at": started_at.isoformat(),
            "finished_at": None,
            "steps_completed": 0,
            "artifacts": None,
            "error_summary": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        columns = list(payload.keys())
        values = [Json(v) if isinstance(v, (list, dict)) else v for v in payload.values()]
        updates = [
            "status = EXCLUDED.status",
            "trigger_source = EXCLUDED.trigger_source",
            "plan = EXCLUDED.plan",
            "started_at = EXCLUDED.started_at",
            "finished_at = EXCLUDED.finished_at",
            "steps_completed = EXCLUDED.steps_completed",
            "artifacts = EXCLUDED.artifacts",
            "error_summary = EXCLUDED.error_summary",
            "updated_at = EXCLUDED.updated_at",
        ]
        query = f"""
            INSERT INTO pipeline_runs ({', '.join(columns)})
            VALUES ({', '.join(['%s'] * len(columns))})
            ON CONFLICT (run_id) DO UPDATE SET {', '.join(updates)}
        """
        with self._cursor() as cur:
            cur.execute(query, values)

    def record_pipeline_run_step(
        self,
        *,
        run_id: str,
        order_index: int,
        step_name: str,
        status: str,
        started_at: datetime,
        finished_at: datetime,
        duration_seconds: Optional[float],
        error: Optional[str],
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO pipeline_run_steps (
                    run_id,
                    order_index,
                    step_name,
                    status,
                    started_at,
                    finished_at,
                    duration_seconds,
                    error
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    order_index,
                    step_name,
                    status,
                    started_at.isoformat(),
                    finished_at.isoformat(),
                    duration_seconds,
                    error,
                ),
            )
            cur.execute(
                """
                UPDATE pipeline_runs
                SET steps_completed = %s,
                    updated_at = NOW()
                WHERE run_id = %s
                """,
                (order_index, run_id),
            )

    def finalize_pipeline_run(
        self,
        *,
        run_id: str,
        status: str,
        finished_at: datetime,
        steps_completed: int,
        artifacts: Optional[Mapping[str, str]] = None,
        error_summary: Optional[str] = None,
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                UPDATE pipeline_runs
                SET status = %s,
                    finished_at = %s,
                    steps_completed = %s,
                    artifacts = %s,
                    error_summary = %s,
                    updated_at = NOW()
                WHERE run_id = %s
                """,
                (
                    status,
                    finished_at.isoformat(),
                    steps_completed,
                    (Json(dict(artifacts)) if artifacts else None),
                    error_summary,
                    run_id,
                ),
            )

    def fetch_pipeline_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        query = """
            SELECT *
            FROM pipeline_runs
            ORDER BY started_at DESC
            LIMIT %s
        """
        with self._cursor() as cur:
            cur.execute(query, (limit,))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def fetch_pipeline_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM pipeline_runs WHERE run_id = %s LIMIT 1"
        with self._cursor() as cur:
            cur.execute(query, (run_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def fetch_pipeline_run_steps(self, run_id: str) -> List[Dict[str, Any]]:
        query = """
            SELECT *
            FROM pipeline_run_steps
            WHERE run_id = %s
            ORDER BY order_index ASC
        """
        with self._cursor() as cur:
            cur.execute(query, (run_id,))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Misc utilities
    # ------------------------------------------------------------------
def get_adapter() -> PostgresAdapter:
    global _ADAPTER
    if _ADAPTER is None:
        _ADAPTER = PostgresAdapter()
    return _ADAPTER


__all__ = ["PostgresAdapter", "get_adapter"]

