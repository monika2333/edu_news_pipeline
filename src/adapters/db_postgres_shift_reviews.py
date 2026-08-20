from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence

import psycopg

if TYPE_CHECKING:
    from src.adapters.db_postgres_core import PostgresAdapter

from src.adapters.db_postgres_manual_reviews import (
    CREATED_LOCAL_DATE_EXPRESSION,
    SCORE_FEEDBACK_JOIN,
    SEARCH_TEXT_EXPRESSION,
    _build_manual_candidate_filters,
)
from src.domain.report_type import NEWS_REPORT_TYPES as VALID_REPORT_TYPES

VALID_DECISIONS = frozenset({"pending", "selected", "backup", "discarded"})
_EDITABLE_FIELDS = (
    "decision",
    "report_type",
    "excerpt_text",
    "edited_summary",
    "manual_llm_source",
    "notes",
)
_ADMIN_UNPROCESSED_SQL = """(
    sr.admin_discarded_at IS NULL
    AND (
        mr.id IS NULL
        OR COALESCE(mr.status, 'pending') IN ('pending', 'discarded')
    )
)"""

SHIFT_REVIEW_SELECT = """
    ns.article_id,
    COALESCE(sr.decision, 'pending') AS decision,
    sr.report_type,
    sr.rank,
    sr.excerpt_text,
    sr.edited_summary,
    sr.manual_llm_source,
    sr.notes,
    sr.version,
    sr.finalized_batch_id,
    sr.finalized_rank,
    finalization_batch.finalized_at,
    finalization_batch.finalized_by_user_id,
    finalizer.display_name AS finalized_by_display_name,
    sr.created_by_user_id,
    creator.display_name AS created_by_display_name,
    sr.updated_by_user_id,
    updater.display_name AS updated_by_display_name,
    sr.decided_at,
    sr.created_at AS review_created_at,
    sr.updated_at AS review_updated_at,
    ns.title,
    ns.llm_summary,
    ns.llm_source,
    ns.score,
    ns.url,
    ns.source,
    ns.publish_time_iso,
    ns.publish_time,
    ns.sentiment_label,
    ns.sentiment_confidence,
    ns.is_beijing_related,
    ns.external_importance_score,
    ns.external_importance_checked_at,
    ns.score_details,
    sf.feedback_type AS score_feedback_type,
    sf.score_value AS score_feedback_score_value,
    sf.notes AS score_feedback_notes,
    sf.submitted_by AS score_feedback_submitted_by,
    sf.submitted_by_user_id AS score_feedback_submitted_by_user_id,
    feedback_submitter.display_name AS score_feedback_submitted_by_display_name,
    sf.updated_at AS score_feedback_updated_at
"""


class ShiftReviewConflictError(RuntimeError):
    """Raised when a review update uses a stale version."""


class ShiftReviewsNamespace:
    """Read access to shift review items, clusters, and summaries."""

    def __init__(self, adapter: PostgresAdapter) -> None:
        self._adapter = adapter

    def fetch_items(
        self,
        *,
        shift_id: str,
        decision: Optional[str],
        report_type: Optional[str],
        limit: int,
        offset: int,
        region: Optional[str] = None,
        sentiment: Optional[str] = None,
        query: Optional[str] = None,
        created_before: Optional[date] = None,
        article_ids: Optional[Sequence[str]] = None,
        mismatch_only: bool = False,
        include_admin_state: bool = False,
        admin_discarded_only: bool = False,
        exclude_admin_discarded: bool = False,
        admin_unprocessed_only: bool = False,
        exclude_finalized: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        with self._adapter._cursor() as cur:
            return fetch_shift_review_items(
                cur,
                shift_id=shift_id,
                decision=decision,
                report_type=report_type,
                limit=limit,
                offset=offset,
                region=region,
                sentiment=sentiment,
                query=query,
                created_before=created_before,
                article_ids=article_ids,
                mismatch_only=mismatch_only,
                include_admin_state=include_admin_state,
                admin_discarded_only=admin_discarded_only,
                exclude_admin_discarded=exclude_admin_discarded,
                admin_unprocessed_only=admin_unprocessed_only,
                exclude_finalized=exclude_finalized,
            )

    def fetch_clusters(
        self,
        *,
        shift_id: str,
        report_type: str,
    ) -> list[dict[str, Any]]:
        with self._adapter._cluster_transaction() as cur:
            return fetch_shift_clusters(
                cur,
                shift_id=shift_id,
                report_type=report_type,
            )

    def fetch_finalization_status(
        self,
        *,
        shift_id: str,
        report_type: str,
    ) -> Optional[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_shift_finalization_status(
                cur,
                shift_id=shift_id,
                report_type=report_type,
            )

    def fetch_stats(
        self,
        shift_id: str,
        *,
        report_type: Optional[str] = None,
    ) -> dict[str, Any]:
        with self._adapter._cursor() as cur:
            return fetch_shift_stats(cur, shift_id, report_type=report_type)

    def fetch_admin_summaries(self, *, limit: int = 60) -> list[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_admin_shift_summaries(cur, limit=limit)


def fetch_shift_review_items(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    decision: Optional[str],
    report_type: Optional[str],
    limit: int,
    offset: int,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    query: Optional[str] = None,
    created_before: Optional[date] = None,
    article_ids: Optional[Sequence[str]] = None,
    mismatch_only: bool = False,
    include_admin_state: bool = False,
    admin_discarded_only: bool = False,
    exclude_admin_discarded: bool = False,
    admin_unprocessed_only: bool = False,
    exclude_finalized: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    bounded_limit = max(1, min(limit, 200))
    bounded_offset = max(0, offset)
    clauses = [
        "s.id = %s",
        "s.cancelled_at IS NULL",
        "ns.status = 'ready_for_export'",
        "ns.created_at >= s.starts_at",
        "ns.created_at < s.ends_at",
    ]
    params: list[Any] = [shift_id]
    if decision:
        clauses.append("COALESCE(sr.decision, 'pending') = %s")
        params.append(decision)
    if report_type:
        clauses.append("COALESCE(sr.report_type, 'zongbao') = %s")
        params.append(report_type)
    if region in {"internal", "external"}:
        clauses.append("ns.is_beijing_related = %s")
        params.append(region == "internal")
    if sentiment in {"positive", "negative"}:
        clauses.append("ns.sentiment_label = %s")
        params.append(sentiment)
    normalized_query = (query or "").strip()
    if normalized_query:
        clauses.append(f"{SEARCH_TEXT_EXPRESSION} ILIKE %s")
        params.append(f"%{normalized_query}%")
    if created_before is not None:
        clauses.append(f"{CREATED_LOCAL_DATE_EXPRESSION} < %s")
        params.append(created_before)
    if article_ids is not None:
        normalized_article_ids = [
            str(article_id).strip()
            for article_id in article_ids
            if str(article_id).strip()
        ]
        if not normalized_article_ids:
            return [], 0
        clauses.append("ns.article_id = ANY(%s)")
        params.append(normalized_article_ids)
    if exclude_finalized:
        clauses.append("sr.finalized_batch_id IS NULL")
    if admin_unprocessed_only:
        clauses.append(_ADMIN_UNPROCESSED_SQL)
    elif admin_discarded_only:
        clauses.append("sr.admin_discarded_at IS NOT NULL")
    elif exclude_admin_discarded:
        clauses.append("sr.admin_discarded_at IS NULL")
    if mismatch_only:
        clauses.extend(
            [
                "sr.id IS NOT NULL",
                """(
                    CASE
                        WHEN mr.status = 'exported' THEN 'selected'
                        ELSE COALESCE(mr.status, 'pending')
                    END IS DISTINCT FROM sr.decision
                    OR COALESCE(mr.report_type, 'zongbao')
                       IS DISTINCT FROM COALESCE(sr.report_type, 'zongbao')
                )""",
            ]
        )
    where_sql = " AND ".join(clauses)
    manual_join_sql = (
        "LEFT JOIN manual_reviews mr ON mr.article_id = ns.article_id"
        if mismatch_only or include_admin_state or admin_unprocessed_only
        else ""
    )
    admin_select_sql = (
        """,
        mr.status AS admin_status,
        mr.report_type AS admin_report_type,
        mr.decided_by AS admin_decided_by,
        mr.decided_at AS admin_decided_at,
        mr.version AS admin_version
        """
        if include_admin_state
        else ""
    )
    admin_discard_select_sql = (
        """,
        sr.admin_discarded_at,
        sr.admin_discarded_by_user_id,
        admin_discarder.display_name AS admin_discarded_by_display_name
        """
        if include_admin_state
        else ""
    )
    admin_discard_join_sql = (
        """LEFT JOIN console_users admin_discarder
          ON admin_discarder.id = sr.admin_discarded_by_user_id"""
        if include_admin_state
        else ""
    )
    cur.execute(
        f"""
        SELECT count(*) AS total
        FROM duty_shifts s
        JOIN news_summaries ns
          ON ns.created_at >= s.starts_at
         AND ns.created_at < s.ends_at
        LEFT JOIN shift_reviews sr
          ON sr.shift_id = s.id
         AND sr.article_id = ns.article_id
        {manual_join_sql}
        WHERE {where_sql}
        """,
        tuple(params),
    )
    total_row = cur.fetchone()
    total = int(total_row["total"]) if total_row else 0
    if decision == "selected" and not exclude_finalized:
        order_sql = (
            "finalization_batch.finalized_at ASC NULLS LAST, "
            "CASE WHEN sr.finalized_batch_id IS NOT NULL "
            "THEN sr.finalized_rank ELSE sr.rank END ASC NULLS LAST, "
            "sr.created_at ASC NULLS LAST, sr.id ASC NULLS LAST"
        )
    elif decision in {"selected", "backup"}:
        order_sql = (
            "sr.rank ASC NULLS LAST, sr.created_at ASC NULLS LAST, "
            "sr.id ASC NULLS LAST"
        )
    elif decision == "discarded":
        order_sql = (
            "sr.decided_at DESC NULLS LAST, sr.updated_at DESC NULLS LAST, "
            "ns.external_importance_score DESC NULLS LAST, "
            "sr.id ASC NULLS LAST"
        )
    else:
        order_sql = (
            "ns.external_importance_score DESC NULLS LAST, "
            "sr.rank ASC NULLS LAST"
        )
    cur.execute(
        f"""
        SELECT {SHIFT_REVIEW_SELECT}
        {admin_discard_select_sql}
        {admin_select_sql}
        FROM duty_shifts s
        JOIN news_summaries ns
          ON ns.created_at >= s.starts_at
         AND ns.created_at < s.ends_at
        LEFT JOIN shift_reviews sr
          ON sr.shift_id = s.id
         AND sr.article_id = ns.article_id
        {manual_join_sql}
        LEFT JOIN console_users creator ON creator.id = sr.created_by_user_id
        LEFT JOIN console_users updater ON updater.id = sr.updated_by_user_id
        LEFT JOIN shift_review_finalization_batches finalization_batch
          ON finalization_batch.id = sr.finalized_batch_id
        LEFT JOIN console_users finalizer
          ON finalizer.id = finalization_batch.finalized_by_user_id
        {admin_discard_join_sql}
        {SCORE_FEEDBACK_JOIN}
        WHERE {where_sql}
        ORDER BY
            {order_sql},
            ns.score DESC NULLS LAST,
            ns.publish_time_iso DESC NULLS LAST,
            ns.article_id
        LIMIT %s OFFSET %s
        """,
        tuple(params + [bounded_limit, bounded_offset]),
    )
    return [dict(row) for row in cur.fetchall()], total


def bulk_discard_shift_candidates(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    actor_user_id: str,
    region: str,
    sentiment: str,
    query: Optional[str] = None,
    created_before: Optional[date] = None,
    report_type: str = "zongbao",
    dry_run: bool = True,
) -> dict[str, int]:
    """Discard pending candidates in one shift without per-row versions."""
    clauses, filter_params = _build_manual_candidate_filters(
        region=region,
        sentiment=sentiment,
        query=query,
        created_before=created_before,
        report_type=None,
    )
    where_sql = " AND ".join(clauses)
    matched_sql = f"""
        SELECT
            s.id AS shift_id,
            ns.article_id,
            sr.finalized_batch_id
        FROM duty_shifts s
        JOIN news_summaries ns
          ON ns.created_at >= s.starts_at
         AND ns.created_at < s.ends_at
        LEFT JOIN shift_reviews sr
          ON sr.shift_id = s.id
         AND sr.article_id = ns.article_id
        CROSS JOIN LATERAL (
            SELECT
                ns.article_id,
                COALESCE(sr.decision, 'pending') AS status,
                COALESCE(sr.report_type, 'zongbao') AS report_type
        ) AS mr
        WHERE s.id = %s
          AND s.cancelled_at IS NULL
          AND {where_sql}
    """
    params: list[Any] = [shift_id, *filter_params]
    if dry_run:
        cur.execute(
            f"""
            WITH matched_candidates AS MATERIALIZED (
                {matched_sql}
            )
            SELECT
                count(*) AS matched,
                0 AS updated,
                count(*) FILTER (
                    WHERE finalized_batch_id IS NOT NULL
                ) AS skipped_finalized
            FROM matched_candidates
            """,
            tuple(params),
        )
    else:
        cur.execute(
            f"""
            WITH matched_candidates AS MATERIALIZED (
                {matched_sql}
            ),
            upserted AS (
                INSERT INTO shift_reviews (
                    shift_id,
                    article_id,
                    created_by_user_id,
                    updated_by_user_id,
                    report_type,
                    decision,
                    decided_at
                )
                SELECT
                    shift_id,
                    article_id,
                    %s,
                    %s,
                    %s,
                    'discarded',
                    now()
                FROM matched_candidates
                WHERE finalized_batch_id IS NULL
                ON CONFLICT (shift_id, article_id) DO UPDATE
                SET report_type = EXCLUDED.report_type,
                    decision = 'discarded',
                    rank = NULL,
                    updated_by_user_id = EXCLUDED.updated_by_user_id,
                    version = shift_reviews.version + 1,
                    decided_at = now(),
                    updated_at = now()
                WHERE shift_reviews.decision = 'pending'
                  AND shift_reviews.finalized_batch_id IS NULL
                RETURNING shift_reviews.article_id
            )
            SELECT
                (SELECT count(*) FROM matched_candidates) AS matched,
                (SELECT count(*) FROM upserted) AS updated,
                (
                    SELECT count(*)
                    FROM matched_candidates
                    WHERE finalized_batch_id IS NOT NULL
                ) AS skipped_finalized
            """,
            tuple(params + [actor_user_id, actor_user_id, report_type]),
        )
    row = cur.fetchone() or {}
    return {
        "matched": int(row.get("matched") or 0),
        "updated": int(row.get("updated") or 0),
        "skipped_finalized": int(row.get("skipped_finalized") or 0),
    }


def fetch_shift_clusters(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    report_type: str,
) -> list[dict[str, Any]]:
    cur.execute(
        """
        WITH shift_pending AS (
            SELECT
                ns.article_id,
                ns.external_importance_score,
                sr.rank AS manual_rank,
                ns.score,
                ns.publish_time_iso,
                CASE
                    WHEN ns.is_beijing_related THEN 'internal'
                    ELSE 'external'
                END || '_' || CASE
                    WHEN lower(COALESCE(ns.sentiment_label, '')) = 'negative'
                        THEN 'negative'
                    ELSE 'positive'
                END AS bucket_key
            FROM duty_shifts s
            JOIN news_summaries ns
              ON ns.created_at >= s.starts_at
             AND ns.created_at < s.ends_at
            LEFT JOIN shift_reviews sr
              ON sr.shift_id = s.id
             AND sr.article_id = ns.article_id
            WHERE s.id = %s
              AND s.cancelled_at IS NULL
              AND ns.status = 'ready_for_export'
              AND COALESCE(sr.decision, 'pending') = 'pending'
              AND COALESCE(sr.report_type, 'zongbao') = %s
        ),
        cluster_memberships AS (
            SELECT
                mc.cluster_id,
                mc.bucket_key,
                pending.article_id,
                pending.external_importance_score,
                pending.manual_rank,
                pending.score,
                pending.publish_time_iso
            FROM manual_clusters mc
            CROSS JOIN LATERAL unnest(mc.item_ids)
                AS cluster_item(article_id)
            JOIN shift_pending pending
              ON pending.article_id = cluster_item.article_id
        ),
        unclustered_items AS (
            SELECT
                'single-' || pending.article_id AS cluster_id,
                pending.bucket_key,
                pending.article_id,
                pending.external_importance_score,
                pending.manual_rank,
                pending.score,
                pending.publish_time_iso
            FROM shift_pending pending
            WHERE NOT EXISTS (
                SELECT 1
                FROM manual_clusters mc
                CROSS JOIN LATERAL unnest(mc.item_ids)
                    AS cluster_item(article_id)
                WHERE cluster_item.article_id = pending.article_id
            )
        ),
        all_cluster_items AS (
            SELECT * FROM cluster_memberships
            UNION ALL
            SELECT * FROM unclustered_items
        ),
        ranked_cluster_items AS (
            SELECT
                cluster_id,
                bucket_key,
                article_id,
                external_importance_score,
                manual_rank,
                score,
                publish_time_iso,
                row_number() OVER (
                    PARTITION BY cluster_id
                    ORDER BY
                        external_importance_score DESC NULLS LAST,
                        manual_rank DESC NULLS LAST,
                        score DESC NULLS LAST,
                        publish_time_iso DESC NULLS LAST,
                        article_id
                ) AS item_rank
            FROM all_cluster_items
        )
        SELECT
            cluster_id,
            bucket_key,
            array_agg(article_id ORDER BY item_rank) AS item_ids,
            max(external_importance_score)
                FILTER (WHERE item_rank = 1)
                AS representative_external_importance_score,
            max(manual_rank)
                FILTER (WHERE item_rank = 1)
                AS representative_manual_rank,
            max(score)
                FILTER (WHERE item_rank = 1)
                AS representative_score,
            max(publish_time_iso)
                FILTER (WHERE item_rank = 1)
                AS representative_publish_time
        FROM ranked_cluster_items
        GROUP BY cluster_id, bucket_key
        ORDER BY
            representative_external_importance_score DESC NULLS LAST,
            representative_manual_rank DESC NULLS LAST,
            representative_score DESC NULLS LAST,
            representative_publish_time DESC NULLS LAST,
            cluster_id
        """,
        (
            shift_id,
            report_type,
        ),
    )
    return [dict(row) for row in cur.fetchall()]


def shift_contains_article(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    article_id: str,
) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM duty_shifts s
        JOIN news_summaries ns
          ON ns.created_at >= s.starts_at
         AND ns.created_at < s.ends_at
        WHERE s.id = %s
          AND s.cancelled_at IS NULL
          AND ns.article_id = %s
          AND ns.status = 'ready_for_export'
        """,
        (shift_id, article_id),
    )
    return cur.fetchone() is not None


def fetch_shift_review(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    article_id: str,
    for_update: bool = False,
) -> Optional[dict[str, Any]]:
    lock_sql = "FOR UPDATE" if for_update else ""
    cur.execute(
        f"""
        SELECT
            id,
            shift_id,
            article_id,
            created_by_user_id,
            updated_by_user_id,
            admin_discarded_at,
            admin_discarded_by_user_id,
            report_type,
            decision,
            rank,
            excerpt_text,
            edited_summary,
            manual_llm_source,
            notes,
            version,
            finalized_batch_id,
            finalized_rank,
            decided_at,
            created_at,
            updated_at
        FROM shift_reviews
        WHERE shift_id = %s
          AND article_id = %s
        {lock_sql}
        """,
        (shift_id, article_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def upsert_shift_review(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    article_id: str,
    actor_user_id: str,
    expected_version: Optional[int],
    patch: Mapping[str, Any],
) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    existing = fetch_shift_review(
        cur,
        shift_id=shift_id,
        article_id=article_id,
        for_update=True,
    )
    if existing and existing.get("finalized_batch_id"):
        raise ValueError("已定稿新闻需先撤回当前列表后再修改")
    if existing is None:
        if expected_version not in (None, 0):
            raise ShiftReviewConflictError("Review version is stale")
        values = {field: patch.get(field) for field in _EDITABLE_FIELDS}
        decision = str(values.get("decision") or "pending")
        decided_at_sql = "now()" if decision != "pending" else "NULL"
        cur.execute(
            f"""
            INSERT INTO shift_reviews (
                shift_id,
                article_id,
                created_by_user_id,
                updated_by_user_id,
                report_type,
                decision,
                excerpt_text,
                edited_summary,
                manual_llm_source,
                notes,
                decided_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, {decided_at_sql})
            RETURNING *
            """,
            (
                shift_id,
                article_id,
                actor_user_id,
                actor_user_id,
                values.get("report_type"),
                decision,
                values.get("excerpt_text"),
                values.get("edited_summary"),
                values.get("manual_llm_source"),
                values.get("notes"),
            ),
        )
    else:
        if expected_version is None or int(existing["version"]) != expected_version:
            raise ShiftReviewConflictError("Review version is stale")
        values = {
            field: patch[field] if field in patch else existing.get(field)
            for field in _EDITABLE_FIELDS
        }
        decision = str(values["decision"] or "pending")
        clear_rank = decision not in {"selected", "backup"}
        cur.execute(
            """
            UPDATE shift_reviews
            SET report_type = %s,
                decision = %s,
                rank = CASE WHEN %s THEN NULL ELSE rank END,
                excerpt_text = %s,
                edited_summary = %s,
                manual_llm_source = %s,
                notes = %s,
                updated_by_user_id = %s,
                version = version + 1,
                decided_at = CASE
                    WHEN %s = 'pending' THEN NULL
                    WHEN decision IS DISTINCT FROM %s THEN now()
                    ELSE decided_at
                END,
                updated_at = now()
            WHERE id = %s
            RETURNING *
            """,
            (
                values["report_type"],
                decision,
                clear_rank,
                values["excerpt_text"],
                values["edited_summary"],
                values["manual_llm_source"],
                values["notes"],
                actor_user_id,
                decision,
                decision,
                existing["id"],
            ),
        )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("Failed to save shift review")
    return existing, dict(row)


def set_admin_discarded(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    article_id: str,
    actor_user_id: str,
    discarded: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    existing = fetch_shift_review(
        cur,
        shift_id=shift_id,
        article_id=article_id,
        for_update=True,
    )
    if existing is None:
        raise ValueError("值班审阅记录不存在")
    cur.execute(
        """
        UPDATE shift_reviews
        SET admin_discarded_at = CASE
                WHEN %s THEN COALESCE(admin_discarded_at, now())
                ELSE NULL
            END,
            admin_discarded_by_user_id = CASE
                WHEN %s THEN %s::uuid
                ELSE NULL
            END
        WHERE id = %s
        RETURNING *
        """,
        (
            discarded,
            discarded,
            actor_user_id,
            existing["id"],
        ),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("管理员放弃状态保存失败")
    return existing, dict(row)


def finalize_shift_review_batch(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    report_type: str,
    actor_user_id: str,
) -> dict[str, Any]:
    cur.execute(
        """
        SELECT id
        FROM duty_shifts
        WHERE id = %s
          AND cancelled_at IS NULL
        FOR UPDATE
        """,
        (shift_id,),
    )
    if not cur.fetchone():
        raise ValueError("班次不存在或已取消")
    cur.execute(
        """
        SELECT sr.finalized_batch_id AS batch_id
        FROM shift_reviews sr
        WHERE sr.shift_id = %s
          AND sr.decision = 'selected'
          AND COALESCE(sr.report_type, 'zongbao') = %s
          AND sr.finalized_batch_id IS NOT NULL
        LIMIT 1
        """,
        (shift_id, report_type),
    )
    if cur.fetchone():
        raise ValueError("当前报告已经定稿，请先撤回定稿")
    cur.execute(
        """
        SELECT sr.article_id
        FROM shift_reviews sr
        JOIN duty_shifts s ON s.id = sr.shift_id
        WHERE sr.shift_id = %s
          AND s.cancelled_at IS NULL
          AND sr.decision = 'selected'
          AND COALESCE(sr.report_type, 'zongbao') = %s
          AND sr.finalized_batch_id IS NULL
        ORDER BY
            sr.rank ASC NULLS LAST,
            sr.created_at ASC,
            sr.id ASC
        FOR UPDATE OF sr
        """,
        (shift_id, report_type),
    )
    article_ids = [str(row["article_id"]) for row in cur.fetchall()]
    if not article_ids:
        raise ValueError("当前采纳列表没有可定稿的新闻")

    cur.execute(
        """
        INSERT INTO shift_review_finalization_batches (
            shift_id,
            report_type,
            finalized_by_user_id
        )
        VALUES (%s, %s, %s)
        RETURNING id, shift_id, report_type, finalized_by_user_id, finalized_at
        """,
        (shift_id, report_type, actor_user_id),
    )
    batch_row = cur.fetchone()
    if not batch_row:
        raise RuntimeError("定稿批次创建失败")
    batch = dict(batch_row)

    cur.execute(
        """
        UPDATE shift_reviews AS sr
        SET finalized_batch_id = %s,
            finalized_rank = ordered.finalized_rank::integer,
            updated_by_user_id = %s,
            version = version + 1,
            updated_at = now()
        FROM unnest(%s::text[])
            WITH ORDINALITY AS ordered(article_id, finalized_rank)
        WHERE sr.shift_id = %s
          AND sr.article_id = ordered.article_id
          AND sr.decision = 'selected'
          AND sr.finalized_batch_id IS NULL
        RETURNING sr.article_id
        """,
        (batch["id"], actor_user_id, article_ids, shift_id),
    )
    updated_ids = [str(row["article_id"]) for row in cur.fetchall()]
    if len(updated_ids) != len(article_ids):
        raise ShiftReviewConflictError("采纳列表已变化，请刷新后重试")
    return {
        **batch,
        "item_count": len(article_ids),
        "article_ids": article_ids,
    }


def fetch_shift_finalization_status(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    report_type: str,
) -> Optional[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            finalization_batch.id AS batch_id,
            finalization_batch.report_type,
            finalization_batch.finalized_at,
            finalizer.display_name AS finalized_by_display_name,
            count(sr.id) AS item_count
        FROM shift_review_finalization_batches finalization_batch
        JOIN shift_reviews sr
          ON sr.finalized_batch_id = finalization_batch.id
         AND sr.decision = 'selected'
        LEFT JOIN console_users finalizer
          ON finalizer.id = finalization_batch.finalized_by_user_id
        WHERE finalization_batch.shift_id = %s
          AND finalization_batch.report_type = %s
        GROUP BY
            finalization_batch.id,
            finalization_batch.report_type,
            finalization_batch.finalized_at,
            finalizer.display_name
        ORDER BY finalization_batch.finalized_at DESC
        LIMIT 1
        """,
        (shift_id, report_type),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def fetch_shift_finalized_items(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    report_type: str,
) -> list[dict[str, Any]]:
    cur.execute(
        f"""
        SELECT {SHIFT_REVIEW_SELECT}
        FROM duty_shifts s
        JOIN shift_reviews sr ON sr.shift_id = s.id
        JOIN news_summaries ns ON ns.article_id = sr.article_id
        LEFT JOIN console_users creator ON creator.id = sr.created_by_user_id
        LEFT JOIN console_users updater ON updater.id = sr.updated_by_user_id
        JOIN shift_review_finalization_batches finalization_batch
          ON finalization_batch.id = sr.finalized_batch_id
        LEFT JOIN console_users finalizer
          ON finalizer.id = finalization_batch.finalized_by_user_id
        {SCORE_FEEDBACK_JOIN}
        WHERE s.id = %s
          AND sr.decision = 'selected'
          AND COALESCE(sr.report_type, 'zongbao') = %s
          AND ns.status = 'ready_for_export'
        ORDER BY
            finalization_batch.finalized_at DESC,
            finalization_batch.id DESC,
            sr.finalized_rank ASC,
            sr.article_id
        """,
        (shift_id, report_type),
    )
    return [dict(row) for row in cur.fetchall()]


def restore_shift_review_finalization(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    batch_id: str,
    actor_user_id: str,
) -> dict[str, Any]:
    cur.execute(
        """
        SELECT id, shift_id, report_type, finalized_at
        FROM shift_review_finalization_batches
        WHERE id = %s
          AND shift_id = %s
        FOR UPDATE
        """,
        (batch_id, shift_id),
    )
    batch_row = cur.fetchone()
    if not batch_row:
        raise ValueError("定稿批次不存在")
    batch = dict(batch_row)

    cur.execute(
        """
        SELECT article_id, finalized_rank
        FROM shift_reviews
        WHERE finalized_batch_id = %s
          AND decision = 'selected'
        ORDER BY finalized_rank ASC, article_id
        FOR UPDATE
        """,
        (batch_id,),
    )
    rows = [dict(row) for row in cur.fetchall()]
    if not rows:
        raise ValueError("该定稿批次中没有可撤回的新闻")

    cur.execute(
        """
        SELECT COALESCE(max(rank), 0) AS max_rank
        FROM shift_reviews
        WHERE shift_id = %s
          AND decision = 'selected'
          AND COALESCE(report_type, 'zongbao') = %s
          AND finalized_batch_id IS NULL
        """,
        (shift_id, batch["report_type"]),
    )
    rank_row = cur.fetchone() or {"max_rank": 0}
    start_rank = int(rank_row["max_rank"] or 0)
    article_ids = [str(row["article_id"]) for row in rows]
    restored_ranks = list(
        range(start_rank + 1, start_rank + len(article_ids) + 1)
    )
    cur.execute(
        """
        UPDATE shift_reviews AS sr
        SET finalized_batch_id = NULL,
            finalized_rank = NULL,
            rank = restored.restored_rank,
            updated_by_user_id = %s,
            version = version + 1,
            updated_at = now()
        FROM unnest(%s::text[], %s::integer[])
            AS restored(article_id, restored_rank)
        WHERE sr.shift_id = %s
          AND sr.finalized_batch_id = %s
          AND sr.article_id = restored.article_id
        RETURNING sr.article_id
        """,
        (
            actor_user_id,
            article_ids,
            restored_ranks,
            shift_id,
            batch_id,
        ),
    )
    updated_ids = [str(row["article_id"]) for row in cur.fetchall()]
    if len(updated_ids) != len(article_ids):
        raise ShiftReviewConflictError("定稿批次已变化，请刷新后重试")
    return {
        "batch_id": str(batch["id"]),
        "report_type": str(batch["report_type"]),
        "restored": len(updated_ids),
        "article_ids": article_ids,
    }


def update_shift_review_order(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    actor_user_id: str,
    selected_order: Sequence[str],
    backup_order: Sequence[str],
) -> int:
    updated = 0
    for decision, article_ids in (
        ("selected", selected_order),
        ("backup", backup_order),
    ):
        for rank, article_id in enumerate(article_ids, start=1):
            cur.execute(
                """
                UPDATE shift_reviews
                SET decision = %s,
                    rank = %s,
                    updated_by_user_id = %s,
                    decided_at = CASE
                        WHEN decision IS DISTINCT FROM %s THEN now()
                        ELSE decided_at
                    END,
                    updated_at = now()
                WHERE shift_id = %s
                  AND article_id = %s
                  AND finalized_batch_id IS NULL
                """,
                (
                    decision,
                    rank,
                    actor_user_id,
                    decision,
                    shift_id,
                    article_id,
                ),
            )
            if cur.rowcount != 1:
                raise ValueError(f"Review is missing from shift order: {article_id}")
            updated += 1
    return updated


def fetch_shift_stats(
    cur: psycopg.Cursor,
    shift_id: str,
    report_type: Optional[str] = None,
) -> dict[str, Any]:
    report_clause = (
        "AND COALESCE(sr.report_type, 'zongbao') = %s"
        if report_type
        else ""
    )
    params: tuple[Any, ...] = (
        (shift_id, report_type) if report_type else (shift_id,)
    )
    cur.execute(
        f"""
        SELECT
            count(*) AS total,
            count(*) FILTER (
                WHERE COALESCE(sr.decision, 'pending') <> 'pending'
            ) AS decided,
            count(*) FILTER (
                WHERE COALESCE(sr.decision, 'pending') = 'pending'
            ) AS pending,
            count(*) FILTER (
                WHERE sr.decision = 'selected'
                  AND sr.finalized_batch_id IS NULL
            ) AS selected,
            count(*) FILTER (WHERE sr.decision = 'backup') AS backup,
            count(*) FILTER (WHERE sr.decision = 'discarded') AS discarded
        FROM duty_shifts s
        JOIN news_summaries ns
          ON ns.created_at >= s.starts_at
         AND ns.created_at < s.ends_at
        LEFT JOIN shift_reviews sr
          ON sr.shift_id = s.id
         AND sr.article_id = ns.article_id
        WHERE s.id = %s
          AND s.cancelled_at IS NULL
          AND ns.status = 'ready_for_export'
          {report_clause}
        """,
        params,
    )
    row = cur.fetchone() or {"total": 0, "decided": 0}
    total = int(row.get("total") or 0)
    decided = int(row.get("decided") or 0)
    pending = int(row.get("pending") or 0)
    selected = int(row.get("selected") or 0)
    backup = int(row.get("backup") or 0)
    discarded = int(row.get("discarded") or 0)
    cur.execute(
        """
        SELECT
            COALESCE(mr.report_type, 'zongbao') AS report_type,
            max(mr.decided_at) AS archived_at
        FROM duty_shifts s
        JOIN news_summaries ns
          ON ns.created_at >= s.starts_at
         AND ns.created_at < s.ends_at
        JOIN manual_reviews mr ON mr.article_id = ns.article_id
        WHERE s.id = %s
          AND mr.status = 'exported'
        GROUP BY COALESCE(mr.report_type, 'zongbao')
        """,
        (shift_id,),
    )
    archive_rows = {
        str(item["report_type"]): item["archived_at"]
        for item in cur.fetchall()
    }
    return {
        "total": total,
        "decided": decided,
        "pending": pending,
        "selected": selected,
        "backup": backup,
        "discarded": discarded,
        "exported": discarded,
        "archive_status": {
            "zongbao": archive_rows.get("zongbao"),
            "wanbao": archive_rows.get("wanbao"),
        },
    }


def fetch_admin_shift_summaries(
    cur: psycopg.Cursor,
    *,
    limit: int = 60,
) -> list[dict[str, Any]]:
    cur.execute(
        f"""
        SELECT
            s.id AS shift_id,
            s.user_id,
            u.username,
            u.display_name,
            u.is_active AS user_is_active,
            s.starts_at,
            s.ends_at,
            s.cancelled_at,
            count(ns.article_id) AS total,
            count(ns.article_id) FILTER (
                WHERE COALESCE(sr.decision, 'pending') = 'pending'
            ) AS pending,
            count(ns.article_id) FILTER (
                WHERE sr.decision = 'selected'
            ) AS selected,
            count(ns.article_id) FILTER (
                WHERE sr.decision = 'backup'
            ) AS backup,
            count(ns.article_id) FILTER (
                WHERE sr.decision = 'discarded'
            ) AS discarded,
            count(ns.article_id) FILTER (
                WHERE sr.decision = 'selected'
                  AND COALESCE(sr.report_type, 'zongbao') = 'zongbao'
                  AND {_ADMIN_UNPROCESSED_SQL}
            ) AS zongbao_selected,
            count(ns.article_id) FILTER (
                WHERE sr.decision = 'backup'
                  AND COALESCE(sr.report_type, 'zongbao') = 'zongbao'
                  AND {_ADMIN_UNPROCESSED_SQL}
            ) AS zongbao_backup,
            count(ns.article_id) FILTER (
                WHERE sr.decision = 'selected'
                  AND COALESCE(sr.report_type, 'zongbao') = 'wanbao'
                  AND {_ADMIN_UNPROCESSED_SQL}
            ) AS wanbao_selected,
            count(ns.article_id) FILTER (
                WHERE sr.decision = 'backup'
                  AND COALESCE(sr.report_type, 'zongbao') = 'wanbao'
                  AND {_ADMIN_UNPROCESSED_SQL}
            ) AS wanbao_backup,
            count(ns.article_id) FILTER (
                WHERE sr.decision = 'selected'
                  AND COALESCE(sr.report_type, 'zongbao') = 'zongbao'
            ) AS zongbao_selected_all,
            count(ns.article_id) FILTER (
                WHERE sr.decision = 'backup'
                  AND COALESCE(sr.report_type, 'zongbao') = 'zongbao'
            ) AS zongbao_backup_all,
            count(ns.article_id) FILTER (
                WHERE sr.decision = 'selected'
                  AND COALESCE(sr.report_type, 'zongbao') = 'wanbao'
            ) AS wanbao_selected_all,
            count(ns.article_id) FILTER (
                WHERE sr.decision = 'backup'
                  AND COALESCE(sr.report_type, 'zongbao') = 'wanbao'
            ) AS wanbao_backup_all
        FROM duty_shifts s
        JOIN console_users u ON u.id = s.user_id
        LEFT JOIN news_summaries ns
          ON ns.created_at >= s.starts_at
         AND ns.created_at < s.ends_at
         AND ns.status = 'ready_for_export'
        LEFT JOIN shift_reviews sr
          ON sr.shift_id = s.id
         AND sr.article_id = ns.article_id
        LEFT JOIN manual_reviews mr ON mr.article_id = ns.article_id
        WHERE s.starts_at <= CURRENT_TIMESTAMP
        GROUP BY
            s.id,
            s.user_id,
            u.username,
            u.display_name,
            u.is_active,
            s.starts_at,
            s.ends_at,
            s.cancelled_at
        ORDER BY s.ends_at DESC
        LIMIT %s
        """,
        (max(1, min(limit, 365)),),
    )
    return [dict(row) for row in cur.fetchall()]


__all__ = [
    "ShiftReviewsNamespace",
    "SHIFT_REVIEW_SELECT",
    "ShiftReviewConflictError",
    "VALID_DECISIONS",
    "VALID_REPORT_TYPES",
    "bulk_discard_shift_candidates",
    "fetch_shift_finalized_items",
    "fetch_admin_shift_summaries",
    "fetch_shift_clusters",
    "fetch_shift_review",
    "fetch_shift_review_items",
    "set_admin_discarded",
    "fetch_shift_stats",
    "finalize_shift_review_batch",
    "restore_shift_review_finalization",
    "shift_contains_article",
    "update_shift_review_order",
    "upsert_shift_review",
]
