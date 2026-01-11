from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import psycopg
from psycopg.types.json import Json

from src.adapters.db_postgres_shared import MISSING

SEARCH_TEXT_EXPRESSION = (
    "(coalesce(title, '') || ' ' || coalesce(llm_summary, '') || ' ' || coalesce(content_markdown, ''))"
)
SEARCH_TRGM_INDEX_SQL = f"""
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE INDEX CONCURRENTLY IF NOT EXISTS news_summaries_search_expr_trgm
    ON news_summaries
    USING gin ({SEARCH_TEXT_EXPRESSION} gin_trgm_ops);
"""


def insert_pending_summary(
    cur: psycopg.Cursor,
    article: Mapping[str, Any],
    *,
    keywords: Optional[Sequence[str]] = None,
    fetched_at: Optional[str] = None,
) -> None:
    article_id = str(article.get("article_id") or "").strip()
    if not article_id:
        raise ValueError("Pending summary insert requires article_id")
    payload: Dict[str, Any] = {
        "article_id": article_id,
        "title": article.get("title"),
        "source": article.get("source"),
        "publish_time": article.get("publish_time"),
        "publish_time_iso": article.get("publish_time_iso"),
        "url": article.get("url"),
        "content_markdown": article.get("content_markdown") or "",
        "fetched_at": fetched_at or article.get("fetched_at"),
        "summary_status": "pending",
        "summary_attempted_at": None,
        "summary_fail_count": 0,
    }
    if keywords:
        deduped: List[str] = []
        for kw in keywords:
            if kw and kw not in deduped:
                deduped.append(kw)
        if deduped:
            payload["llm_keywords"] = deduped
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
    cur.execute(query, values)
    if final_failure:
        self.update_manual_review_statuses(
            [
                {
                    "article_id": article_id,
                    "status": "discarded",
                    "decided_at": timestamp,
                }
            ]
        )


def fetch_pending_summaries(
    cur: psycopg.Cursor,
    limit: Optional[int] = None,
    *,
    max_attempts: Optional[int] = None,
) -> List[Dict[str, Any]]:
    clauses = ["summary_status = 'pending'", "status = 'pending'"]
    params: List[Any] = []
    if max_attempts is not None:
        clauses.append("summary_fail_count < %s")
        params.append(max_attempts)
    where_sql = " AND ".join(clauses)
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
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    result: List[Dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        for field in ("fetched_at", "summary_attempted_at", "publish_time_iso"):
            value = record.get(field)
            if isinstance(value, datetime):
                record[field] = value.isoformat()
        result.append(record)
    return result


def mark_summary_attempt(cur: psycopg.Cursor, article_id: str) -> bool:
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
    cur.execute(query, (article_id,))
    return cur.rowcount == 1


def complete_summary(
    cur: psycopg.Cursor,
    article_id: str,
    summary_text: str,
    *,
    llm_source: Optional[str] = None,
    keywords: Optional[Sequence[str]] = None,
    beijing_related: Optional[bool] = None,
    sentiment_label: Optional[str] = None,
    sentiment_confidence: Optional[float] = None,
    status: str = "ready_for_export",
    external_importance_status: Any = MISSING,
    external_importance_score: Any = MISSING,
    external_importance_checked_at: Any = MISSING,
    external_importance_raw: Any = MISSING,
    external_filter_attempted_at: Any = MISSING,
    external_filter_fail_count: Any = MISSING,
    is_beijing_related_llm: Any = MISSING,
    beijing_gate_checked_at: Any = MISSING,
    beijing_gate_raw: Any = MISSING,
    beijing_gate_attempted_at: Any = MISSING,
    beijing_gate_fail_count: Any = MISSING,
) -> None:
    if not article_id:
        raise ValueError("complete_summary requires article_id")
    payload: Dict[str, Any] = {
        "llm_summary": summary_text,
        "summary_status": "completed",
        "summary_generated_at": datetime.now(timezone.utc).isoformat(),
        "summary_attempted_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
    }
    if llm_source is not None:
        payload["llm_source"] = llm_source
    if keywords:
        deduped: List[str] = []
        for kw in keywords:
            if kw and kw not in deduped:
                deduped.append(kw)
        if deduped:
            payload["llm_keywords"] = deduped
    if beijing_related is not None:
        payload["is_beijing_related"] = beijing_related
    if sentiment_label is not None:
        payload["sentiment_label"] = sentiment_label
    if sentiment_confidence is not None:
        payload["sentiment_confidence"] = float(sentiment_confidence)

    def _maybe_set(field: str, value: Any) -> None:
        if value is not MISSING:
            payload[field] = value

    _maybe_set("external_importance_status", external_importance_status)
    _maybe_set("external_importance_score", external_importance_score)
    _maybe_set("external_importance_checked_at", external_importance_checked_at)
    _maybe_set(
        "external_importance_raw",
        Json(external_importance_raw)
        if (external_importance_raw is not MISSING and external_importance_raw is not None)
        else external_importance_raw,
    )
    _maybe_set("external_filter_attempted_at", external_filter_attempted_at)
    _maybe_set("external_filter_fail_count", external_filter_fail_count)
    _maybe_set("is_beijing_related_llm", is_beijing_related_llm)
    _maybe_set("beijing_gate_checked_at", beijing_gate_checked_at)
    if beijing_gate_raw is not MISSING:
        payload["beijing_gate_raw"] = Json(beijing_gate_raw) if beijing_gate_raw is not None else None
    _maybe_set("beijing_gate_attempted_at", beijing_gate_attempted_at)
    _maybe_set("beijing_gate_fail_count", beijing_gate_fail_count)
    sets = ", ".join(f"{field} = %s" for field in payload)
    values = list(payload.values()) + [article_id]
    query = f"""
        UPDATE news_summaries
        SET {sets}
        WHERE article_id = %s
    """
    cur.execute(query, values)
    if cur.rowcount != 1:
        raise ValueError(f"Unable to complete summary for {article_id}")


def mark_summary_failed(cur: psycopg.Cursor, article_id: str, *, message: Optional[str] = None) -> None:
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
    cur.execute(query, (article_id,))
    if message:
        print(f"[warn] summary failed {article_id}: {message}", file=sys.stderr)


def search_news_summaries(
    cur: psycopg.Cursor,
    *,
    query: Optional[str] = None,
    sources: Optional[Sequence[str]] = None,
    sentiments: Optional[Sequence[str]] = None,
    statuses: Optional[Sequence[str]] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    normalized_query = (query or "").strip()
    clauses: List[str] = []
    params: List[Any] = []
    if normalized_query:
        like_pattern = f"%{normalized_query}%"
        clauses.append(f"{SEARCH_TEXT_EXPRESSION} ILIKE %s")
        params.append(like_pattern)

    normalized_sources = [item.strip() for item in (sources or []) if item and item.strip()]
    if normalized_sources:
        clauses.append("source = ANY(%s)")
        params.append(normalized_sources)

    normalized_sentiments = [item.strip().lower() for item in (sentiments or []) if item and item.strip()]
    if normalized_sentiments:
        clauses.append("lower(coalesce(sentiment_label, '')) = ANY(%s)")
        params.append(normalized_sentiments)

    normalized_statuses = [item.strip().lower() for item in (statuses or []) if item and item.strip()]
    if normalized_statuses:
        clauses.append("lower(coalesce(status, '')) = ANY(%s)")
        params.append(normalized_statuses)

    if start_date:
        start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
        clauses.append("publish_time_iso >= %s")
        params.append(start_dt)

    if end_date:
        exclusive_end = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        clauses.append("publish_time_iso < %s")
        params.append(exclusive_end)

    where_clause = " AND ".join(clauses) if clauses else "TRUE"
    count_sql = f"SELECT COUNT(*) FROM news_summaries WHERE {where_clause}"
    select_sql = f"""
        SELECT
            article_id,
            title,
            source,
            publish_time,
            publish_time_iso,
            url,
            llm_summary,
            COALESCE(llm_keywords, '{{}}'::text[]) AS llm_keywords,
            score,
            raw_relevance_score,
            keyword_bonus_score,
            sentiment_label,
            sentiment_confidence,
            status,
            summary_status,
            external_importance_status,
            external_importance_score,
            is_beijing_related,
            is_beijing_related_llm,
            external_importance_checked_at,
            external_importance_raw,
            summary_generated_at,
            created_at,
            updated_at
        FROM news_summaries
        WHERE {where_clause}
        ORDER BY publish_time_iso DESC NULLS LAST, created_at DESC
        LIMIT %s OFFSET %s
    """
    cur.execute(count_sql, tuple(params))
    total_row = cur.fetchone() or {}
    total = int(total_row.get("count") or 0)
    fetch_params = list(params)
    fetch_params.extend([limit, offset])
    cur.execute(select_sql, tuple(fetch_params))
    rows = cur.fetchall()
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def fetch_news_summary_content(cur: psycopg.Cursor, article_id: str) -> Optional[Dict[str, Any]]:
    if not article_id:
        return None
    query = """
        SELECT article_id, content_markdown
        FROM news_summaries
        WHERE article_id = %s
    """
    cur.execute(query, (article_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def fetch_raw_articles_for_summary(
    cur: psycopg.Cursor,
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
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    result: List[Dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        fetched = record.get("fetched_at")
        if isinstance(fetched, datetime):
            record["fetched_at"] = fetched.isoformat()
        publish_iso = record.get("publish_time_iso")
        if isinstance(publish_iso, datetime):
            record["publish_time_iso"] = publish_iso.isoformat()
        detail_fetched = record.get("detail_fetched_at")
        if isinstance(detail_fetched, datetime):
            record["detail_fetched_at"] = detail_fetched.isoformat()
        result.append(record)
    return result


def get_existing_news_summary_ids(cur: psycopg.Cursor, article_ids: Sequence[str]) -> Set[str]:
    unique_ids = list({str(item) for item in article_ids if item})
    if not unique_ids:
        return set()
    query = "SELECT article_id FROM news_summaries WHERE article_id = ANY(%s)"
    cur.execute(query, (unique_ids,))
    rows = cur.fetchall()
    return {str(row["article_id"]) for row in rows if row.get("article_id")}


def upsert_news_summary(
    cur: psycopg.Cursor,
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


def update_summary_score(cur: psycopg.Cursor, article_id: str, score: Optional[float]) -> None:
    cur.execute(
        "UPDATE news_summaries SET score = %s, updated_at = NOW() WHERE article_id = %s",
        (score, article_id),
    )


def upsert_news_summaries_from_primary(cur: psycopg.Cursor, rows: Sequence[Mapping[str, Any]]) -> int:
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
    cur.executemany(query, prepared)
    return len(prepared)


__all__ = [
    "complete_summary",
    "fetch_pending_summaries",
    "fetch_news_summary_content",
    "fetch_raw_articles_for_summary",
    "get_existing_news_summary_ids",
    "insert_pending_summary",
    "mark_summary_attempt",
    "mark_summary_failed",
    "search_news_summaries",
    "update_summary_score",
    "upsert_news_summary",
    "upsert_news_summaries_from_primary",
]
