from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

import psycopg


RAW_SEARCH_TEXT_EXPRESSION = (
    "(coalesce(ra.title, '') || ' ' || coalesce(ra.content_markdown, ''))"
)
SUMMARY_SEARCH_TEXT_EXPRESSION = (
    "(coalesce(ns.title, '') || ' ' || coalesce(ns.llm_summary, '') || ' ' || "
    "coalesce(ns.content_markdown, ''))"
)


def search_article_attributions(
    cur: psycopg.Cursor,
    *,
    query: Optional[str],
    fetched_after: datetime,
    limit: int,
    offset: int,
) -> Dict[str, Any]:
    """Search the full article pipeline and resolve each hit to its primary article."""
    safe_limit = max(1, min(int(limit or 20), 100))
    safe_offset = max(0, int(offset or 0))
    normalized_query = (query or "").strip()
    like_pattern = f"%{normalized_query}%"
    sql = f"""
        WITH raw_hits AS (
            SELECT
                ra.article_id,
                ra.title AS matched_title,
                ra.fetched_at AS matched_fetched_at,
                0 AS match_rank
            FROM raw_articles ra
            WHERE ra.fetched_at >= %s
              AND (%s = '' OR {RAW_SEARCH_TEXT_EXPRESSION} ILIKE %s)
        ),
        summary_hits AS (
            SELECT
                ns.article_id,
                COALESCE(ra.title, ns.title) AS matched_title,
                ra.fetched_at AS matched_fetched_at,
                1 AS match_rank
            FROM news_summaries ns
            JOIN raw_articles ra ON ra.article_id = ns.article_id
            WHERE ra.fetched_at >= %s
              AND (
                  %s = ''
                  OR (
                      {SUMMARY_SEARCH_TEXT_EXPRESSION} ILIKE %s
                      AND COALESCE(ns.llm_summary, '') ILIKE %s
                  )
              )
        ),
        matched_hits AS (
            SELECT * FROM raw_hits
            UNION ALL
            SELECT * FROM summary_hits
        ),
        resolved_hits AS (
            SELECT
                CASE
                    WHEN fa.status = 'duplicate'
                         AND NULLIF(fa.primary_article_id, '') IS NOT NULL
                    THEN fa.primary_article_id
                    ELSE h.article_id
                END AS canonical_article_id,
                h.article_id AS matched_article_id,
                h.matched_title,
                h.matched_fetched_at,
                h.match_rank
            FROM matched_hits h
            LEFT JOIN filtered_articles fa ON fa.article_id = h.article_id
        ),
        canonical_hits AS (
            SELECT DISTINCT ON (canonical_article_id)
                canonical_article_id,
                matched_article_id,
                matched_title
            FROM resolved_hits
            ORDER BY
                canonical_article_id,
                (matched_article_id = canonical_article_id) DESC,
                match_rank,
                matched_fetched_at DESC NULLS LAST,
                matched_article_id
        ),
        ranked_hits AS (
            SELECT
                ch.*,
                COALESCE(ns.created_at, ra.fetched_at) AS ingested_at
            FROM canonical_hits ch
            LEFT JOIN raw_articles ra ON ra.article_id = ch.canonical_article_id
            LEFT JOIN news_summaries ns ON ns.article_id = ch.canonical_article_id
        ),
        totals AS (
            SELECT COUNT(*)::bigint AS total
            FROM canonical_hits
        ),
        page_hits AS (
            SELECT *
            FROM ranked_hits
            ORDER BY ingested_at DESC NULLS LAST, canonical_article_id
            LIMIT %s OFFSET %s
        ),
        decision_rows AS (
            SELECT
                mr.article_id,
                'admin'::text AS workspace,
                COALESCE(u.display_name, mr.decided_by) AS actor,
                mr.decided_at,
                mr.status AS decision
            FROM manual_reviews mr
            JOIN page_hits ph ON ph.canonical_article_id = mr.article_id
            LEFT JOIN console_users u ON u.id = mr.decided_by_user_id

            UNION ALL

            SELECT
                sr.article_id,
                'duty'::text AS workspace,
                u.display_name AS actor,
                sr.decided_at,
                sr.decision
            FROM shift_reviews sr
            JOIN page_hits ph ON ph.canonical_article_id = sr.article_id
            LEFT JOIN console_users u ON u.id = sr.updated_by_user_id
        ),
        decisions AS (
            SELECT
                article_id,
                BOOL_OR(decision = 'discarded') AS has_discarded,
                JSONB_AGG(
                    JSONB_BUILD_OBJECT(
                        'workspace', workspace,
                        'actor', actor,
                        'decided_at', decided_at,
                        'decision', decision
                    )
                    ORDER BY decided_at DESC NULLS LAST, workspace
                ) AS manual_decisions
            FROM decision_rows
            GROUP BY article_id
        ),
        exports AS (
            SELECT
                bi.article_id,
                ARRAY_AGG(
                    DISTINCT bb.report_date
                    ORDER BY bb.report_date DESC
                ) AS export_batch_dates
            FROM brief_items bi
            JOIN page_hits ph ON ph.canonical_article_id = bi.article_id
            JOIN brief_batches bb ON bb.id = bi.brief_batch_id
            GROUP BY bi.article_id
        ),
        enriched AS (
            SELECT
                ph.canonical_article_id AS article_id,
                COALESCE(ns.title, pa.title, ra.title) AS title,
                COALESCE(ns.source, pa.source, ra.source) AS source,
                COALESCE(ns.publish_time, pa.publish_time, ra.publish_time) AS publish_time,
                COALESCE(ns.publish_time_iso, pa.publish_time_iso, ra.publish_time_iso)
                    AS publish_time_iso,
                COALESCE(ns.url, pa.url, ra.url) AS url,
                ns.llm_summary,
                COALESCE(fa.keywords, pa.keywords, ns.llm_keywords, '{{}}'::text[]) AS keywords,
                COALESCE(ns.llm_keywords, '{{}}'::text[]) AS llm_keywords,
                COALESCE(ns.score, pa.score) AS score,
                COALESCE(ns.raw_relevance_score, pa.raw_relevance_score)
                    AS raw_relevance_score,
                COALESCE(ns.keyword_bonus_score, pa.keyword_bonus_score)
                    AS keyword_bonus_score,
                ns.sentiment_label,
                ns.sentiment_confidence,
                ns.status,
                ns.summary_status,
                ns.external_importance_status,
                ns.external_importance_score,
                ns.is_beijing_related,
                ns.is_beijing_related_llm,
                ns.external_importance_checked_at,
                ns.summary_generated_at,
                ns.created_at,
                ns.updated_at,
                CASE
                    WHEN ex.article_id IS NOT NULL THEN 'exported'
                    WHEN COALESCE(d.has_discarded, FALSE) THEN 'discarded'
                    WHEN ns.status = 'external_filtered' THEN 'importance_below'
                    WHEN pa.status = 'filtered_out' THEN 'relevance_below'
                    WHEN fa.article_id IS NULL THEN 'keyword_missed'
                    ELSE 'not_reviewed'
                END AS attribution_level,
                COALESCE(ns.created_at, ra.fetched_at) AS attribution_ingested_at,
                CASE
                    WHEN ns.created_at IS NOT NULL THEN 'news_summaries.created_at'
                    ELSE 'raw_articles.fetched_at'
                END AS attribution_ingested_at_source,
                COALESCE(ns.score, pa.score) AS attribution_relevance_score,
                ns.external_importance_score AS attribution_importance_score,
                COALESCE(d.manual_decisions, '[]'::jsonb) AS attribution_manual_decisions,
                COALESCE(ex.export_batch_dates, '{{}}'::date[]) AS attribution_export_batch_dates,
                CASE
                    WHEN ph.matched_article_id <> ph.canonical_article_id
                    THEN ph.matched_title
                    ELSE NULL
                END AS attribution_matched_article_title
            FROM page_hits ph
            LEFT JOIN raw_articles ra ON ra.article_id = ph.canonical_article_id
            LEFT JOIN filtered_articles fa ON fa.article_id = ph.canonical_article_id
            LEFT JOIN primary_articles pa ON pa.article_id = ph.canonical_article_id
            LEFT JOIN news_summaries ns ON ns.article_id = ph.canonical_article_id
            LEFT JOIN decisions d ON d.article_id = ph.canonical_article_id
            LEFT JOIN exports ex ON ex.article_id = ph.canonical_article_id
        )
        SELECT totals.total, enriched.*
        FROM totals
        LEFT JOIN enriched ON TRUE
        ORDER BY attribution_ingested_at DESC NULLS LAST, article_id
    """
    cur.execute(
        sql,
        (
            fetched_after,
            normalized_query,
            like_pattern,
            fetched_after,
            normalized_query,
            like_pattern,
            like_pattern,
            safe_limit,
            safe_offset,
        ),
    )
    rows = [dict(row) for row in cur.fetchall()]
    if not rows:
        return {
            "items": [],
            "total": 0,
            "limit": safe_limit,
            "offset": safe_offset,
        }
    total = int(rows[0].get("total") or 0)
    items = []
    for row in rows:
        row.pop("total", None)
        if row.get("article_id") is not None:
            items.append(row)
    return {
        "items": items,
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
    }


__all__ = [
    "RAW_SEARCH_TEXT_EXPRESSION",
    "SUMMARY_SEARCH_TEXT_EXPRESSION",
    "search_article_attributions",
]
