from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import psycopg

from src.adapters.db_postgres_manual_reviews._base import DUTY_UNPROCESSED_SQL


def delete_manual_clusters(cur: psycopg.Cursor) -> int:
    cur.execute("DELETE FROM manual_clusters")
    return cur.rowcount


def insert_manual_clusters(
    cur: psycopg.Cursor,
    clusters: Sequence[Mapping[str, Any]],
) -> int:
    if not clusters:
        return 0
    payload: List[Tuple[Any, ...]] = []
    for cluster in clusters:
        cluster_id = str(cluster.get("cluster_id") or "").strip()
        bucket_key = str(cluster.get("bucket_key") or "").strip()
        item_ids = cluster.get("item_ids") or []
        if not cluster_id or not bucket_key:
            continue
        payload.append((bucket_key, cluster_id, list(item_ids)))
    if not payload:
        return 0
    query = """
        INSERT INTO manual_clusters (bucket_key, cluster_id, item_ids)
        VALUES (%s, %s, %s)
    """
    cur.executemany(query, payload)
    return len(payload)


def fetch_manual_clusters(
    cur: psycopg.Cursor,
    *,
    owner_user_id: str,
    bucket_key: Optional[str] = None,
    duty_unprocessed_only: bool = False,
) -> List[Dict[str, Any]]:
    duty_filter_sql = (
        f"AND {DUTY_UNPROCESSED_SQL}"
        if duty_unprocessed_only
        else ""
    )
    query = f"""
        WITH cluster_base AS (
            SELECT cluster_id, bucket_key, item_ids
            FROM manual_clusters
            WHERE (%s::text IS NULL OR bucket_key = %s)
        ),
        all_cluster_items AS (
            SELECT unnest(item_ids) AS article_id
            FROM manual_clusters
        ),
        visible_cluster_items AS (
            SELECT cb.cluster_id, cb.bucket_key, unnest(cb.item_ids) AS article_id
            FROM cluster_base cb
        ),
        pending AS (
            SELECT
                mr.article_id,
                mr.version,
                mr.summary AS manual_summary,
                mr.rank AS manual_rank,
                mr.manual_llm_source,
                ns.title,
                ns.llm_summary,
                ns.llm_source,
                ns.source,
                ns.url,
                ns.score,
                ns.external_importance_score,
                ns.sentiment_label,
                ns.is_beijing_related,
                ns.publish_time_iso,
                ns.publish_time,
                ns.score_details,
                sf.feedback_type AS score_feedback_type,
                sf.score_value AS score_feedback_score_value,
                sf.notes AS score_feedback_notes,
                sf.submitted_by AS score_feedback_submitted_by,
                sf.submitted_by_user_id AS score_feedback_submitted_by_user_id,
                feedback_submitter.display_name AS score_feedback_submitted_by_display_name,
                sf.updated_at AS score_feedback_updated_at,
                CASE WHEN ns.is_beijing_related THEN 'internal' ELSE 'external' END
                    || '_'
                    || CASE
                        WHEN lower(COALESCE(ns.sentiment_label, '')) = 'negative'
                        THEN 'negative'
                        ELSE 'positive'
                    END AS derived_bucket_key
            FROM manual_reviews mr
            JOIN news_summaries ns ON ns.article_id = mr.article_id
            LEFT JOIN score_feedbacks sf
              ON sf.article_id = ns.article_id
             AND sf.prompt_key = ns.external_importance_raw ->> 'prompt_key'
             AND sf.prompt_version = ns.external_importance_raw ->> 'prompt_version'
            LEFT JOIN console_users feedback_submitter
              ON feedback_submitter.id = sf.submitted_by_user_id
            WHERE mr.owner_user_id = %s
              AND mr.status = 'pending'
              AND ns.status = 'ready_for_export'
              {duty_filter_sql}
        ),
        clustered AS (
            SELECT
                ci.cluster_id,
                ci.bucket_key,
                p.*
            FROM visible_cluster_items ci
            JOIN pending p ON p.article_id = ci.article_id
        ),
        singletons AS (
            SELECT
                'single-' || p.article_id AS cluster_id,
                p.derived_bucket_key AS bucket_key,
                p.*
            FROM pending p
            WHERE (%s::text IS NULL OR p.derived_bucket_key = %s)
              AND NOT EXISTS (
                  SELECT 1
                  FROM all_cluster_items aci
                  WHERE aci.article_id = p.article_id
              )
        )
        SELECT
            cluster_id,
            bucket_key,
            article_id,
            version,
            manual_summary,
            manual_rank,
            manual_llm_source,
            title,
            llm_summary,
            llm_source,
            source,
            url,
            score,
            external_importance_score,
            sentiment_label,
            is_beijing_related,
            publish_time_iso,
            publish_time,
            score_details,
            score_feedback_type,
            score_feedback_score_value,
            score_feedback_notes,
            score_feedback_submitted_by,
            score_feedback_submitted_by_user_id,
            score_feedback_submitted_by_display_name,
            score_feedback_updated_at
        FROM (
            SELECT * FROM clustered
            UNION ALL
            SELECT * FROM singletons
        ) visible
        ORDER BY
            cluster_id,
            external_importance_score DESC NULLS LAST,
            manual_rank ASC NULLS LAST,
            score DESC NULLS LAST
    """
    cur.execute(
        query,
        (bucket_key, bucket_key, owner_user_id, bucket_key, bucket_key),
    )
    rows = cur.fetchall()
    return [dict(row) for row in rows]


def try_advisory_lock(cur: psycopg.Cursor, lock_id: int) -> bool:
    cur.execute("SELECT pg_try_advisory_lock(%s) AS locked", (int(lock_id),))
    row = cur.fetchone() or {}
    return bool(row.get("locked"))


def release_advisory_lock(cur: psycopg.Cursor, lock_id: int) -> None:
    cur.execute("SELECT pg_advisory_unlock(%s)", (int(lock_id),))

__all__ = [
    "delete_manual_clusters",
    "fetch_manual_clusters",
    "insert_manual_clusters",
    "release_advisory_lock",
    "try_advisory_lock",
]
