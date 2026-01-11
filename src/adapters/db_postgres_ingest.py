from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import psycopg


def upsert_toutiao_articles(cur: psycopg.Cursor, rows: Sequence[Mapping[str, Any]]) -> int:
    if not rows:
        return 0
    columns = [
        "token",
        "profile_url",
        "article_id",
        "title",
        "source",
        "publish_time",
        "publish_time_iso",
        "url",
        "summary",
        "comment_count",
        "digg_count",
        "content_markdown",
        "fetched_at",
    ]
    insert_sql = """
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
    """
    data = [tuple(row.get(col) for col in columns) for row in rows]
    cur.executemany(insert_sql, data)
    return len(rows)


def upsert_raw_feed_rows(cur: psycopg.Cursor, rows: Sequence[Mapping[str, Any]]) -> int:
    if not rows:
        return 0
    columns = [
        "token",
        "profile_url",
        "article_id",
        "title",
        "source",
        "publish_time",
        "publish_time_iso",
        "url",
        "summary",
        "comment_count",
        "digg_count",
        "fetched_at",
    ]
    insert_sql = """
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
    """
    data = [tuple(row.get(col) for col in columns) for row in rows]
    cur.executemany(insert_sql, data)
    return len(rows)


def update_raw_article_details(cur: psycopg.Cursor, rows: Sequence[Mapping[str, Any]]) -> int:
    if not rows:
        return 0
    columns = [
        "token",
        "profile_url",
        "title",
        "source",
        "publish_time",
        "publish_time_iso",
        "url",
        "summary",
        "comment_count",
        "digg_count",
        "content_markdown",
        "detail_fetched_at",
    ]
    update_sql = """
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
    """
    missing: List[str] = []
    for row in rows:
        article_id = str(row.get("article_id") or "")
        if not article_id:
            raise ValueError("Detail update requires article_id")
        values = [row.get(col) for col in columns]
        cur.execute(update_sql, values + [article_id])
        if cur.rowcount == 0:
            missing.append(article_id)
    if missing:
        missing_values = ", ".join(sorted(missing))
        raise ValueError(f"Missing feed rows for detail update: {missing_values}")
    return len(rows)


def get_raw_articles_missing_content(cur: psycopg.Cursor, article_ids: Sequence[str]) -> Set[str]:
    unique_ids = list({str(item) for item in article_ids if item})
    if not unique_ids:
        return set()
    query = (
        "SELECT article_id FROM raw_articles"
        " WHERE article_id = ANY(%s)"
        "   AND (content_markdown IS NULL OR LENGTH(TRIM(content_markdown)) = 0)"
    )
    cur.execute(query, (unique_ids,))
    rows = cur.fetchall()
    return {str(row["article_id"]) for row in rows if row.get("article_id")}


def fetch_raw_articles_missing_content(
    cur: psycopg.Cursor,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
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
    cur.execute(sql_query, tuple(params))
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


def upsert_filtered_articles(cur: psycopg.Cursor, rows: Sequence[Mapping[str, Any]]) -> int:
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
    cur.executemany(query, prepared)
    return len(prepared)


def fetch_filtered_articles_for_hashing(cur: psycopg.Cursor, limit: int) -> List[Dict[str, Any]]:
    query = """
        SELECT
            article_id,
            title,
            source,
            publish_time,
            publish_time_iso,
            url,
            content_markdown,
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
    cur.execute(query, (max(1, limit),))
    rows = cur.fetchall()
    return [dict(row) for row in rows]


def fetch_filtered_articles_by_hashes(cur: psycopg.Cursor, hashes: Sequence[str]) -> List[Dict[str, Any]]:
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
    cur.execute(query, (ordered_hashes,))
    rows = cur.fetchall()
    return [dict(row) for row in rows]


def update_filtered_article_features(cur: psycopg.Cursor, updates: Sequence[Mapping[str, Any]]) -> int:
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
    cur.executemany(query, prepared)
    return len(prepared)


def fetch_filtered_articles_by_band(
    cur: psycopg.Cursor,
    band_index: int,
    band_value: int,
    limit: int,
) -> List[Dict[str, Any]]:
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
    cur.execute(query, (band_value, max(1, limit)))
    rows = cur.fetchall()
    return [dict(row) for row in rows]


def update_filtered_primary_ids(cur: psycopg.Cursor, updates: Sequence[Mapping[str, Any]]) -> int:
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
    cur.executemany(query, prepared)
    return len(prepared)


def upsert_primary_articles(cur: psycopg.Cursor, rows: Sequence[Mapping[str, Any]]) -> int:
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
    cur.executemany(query, prepared)
    return len(prepared)


def get_existing_raw_article_ids(cur: psycopg.Cursor) -> Set[str]:
    ids: Set[str] = set()
    cur.execute("SELECT article_id FROM raw_articles")
    for row in cur.fetchall():
        article_id = row.get("article_id")
        if article_id:
            ids.add(str(article_id))
    return ids


__all__ = [
    "fetch_filtered_articles_by_band",
    "fetch_filtered_articles_by_hashes",
    "fetch_filtered_articles_for_hashing",
    "fetch_raw_articles_missing_content",
    "get_existing_raw_article_ids",
    "get_raw_articles_missing_content",
    "update_filtered_article_features",
    "update_filtered_primary_ids",
    "update_raw_article_details",
    "upsert_filtered_articles",
    "upsert_primary_articles",
    "upsert_raw_feed_rows",
    "upsert_toutiao_articles",
]
