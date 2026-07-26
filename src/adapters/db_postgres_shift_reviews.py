from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

import psycopg

from src.adapters.db_postgres_manual_reviews import SCORE_FEEDBACK_JOIN

VALID_DECISIONS = frozenset({"pending", "selected", "backup", "discarded"})
VALID_REPORT_TYPES = frozenset({"zongbao", "wanbao"})
_EDITABLE_FIELDS = (
    "decision",
    "report_type",
    "excerpt_text",
    "edited_summary",
    "manual_llm_source",
    "notes",
)

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
    ns.content_markdown,
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
    sf.updated_at AS score_feedback_updated_at
"""


class ShiftReviewConflictError(RuntimeError):
    """Raised when a review update uses a stale version."""


def fetch_shift_review_items(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    decision: Optional[str],
    report_type: Optional[str],
    limit: int,
    offset: int,
    mismatch_only: bool = False,
    include_admin_state: bool = False,
    admin_discarded_only: bool = False,
    exclude_admin_discarded: bool = False,
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
    if admin_discarded_only:
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
        if mismatch_only or include_admin_state
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
    order_sql = (
        "sr.rank ASC NULLS LAST, sr.created_at ASC NULLS LAST, "
        "sr.id ASC NULLS LAST"
        if decision in {"selected", "backup"}
        else "ns.external_importance_score DESC NULLS LAST, sr.rank ASC NULLS LAST"
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


def fetch_shift_clusters(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    report_type: str,
) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            mc.cluster_id,
            mc.bucket_key,
            array_agg(cluster_item.article_id ORDER BY cluster_item.item_position)
                AS item_ids
        FROM manual_clusters mc
        CROSS JOIN LATERAL unnest(mc.item_ids)
            WITH ORDINALITY AS cluster_item(article_id, item_position)
        JOIN duty_shifts s ON s.id = %s
        JOIN news_summaries ns
          ON ns.article_id = cluster_item.article_id
         AND ns.created_at >= s.starts_at
         AND ns.created_at < s.ends_at
        LEFT JOIN shift_reviews sr
          ON sr.shift_id = s.id
         AND sr.article_id = ns.article_id
        WHERE mc.report_type = %s
          AND s.cancelled_at IS NULL
          AND ns.status = 'ready_for_export'
          AND COALESCE(sr.decision, 'pending') = 'pending'
          AND COALESCE(sr.report_type, 'zongbao') = %s
        GROUP BY mc.cluster_id, mc.bucket_key, mc.created_at
        ORDER BY mc.created_at DESC, mc.cluster_id
        """,
        (shift_id, report_type, report_type),
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
) -> dict[str, Any]:
    cur.execute(
        """
        SELECT
            count(*) AS total,
            count(*) FILTER (
                WHERE COALESCE(sr.decision, 'pending') <> 'pending'
            ) AS decided
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
        """,
        (shift_id,),
    )
    row = cur.fetchone() or {"total": 0, "decided": 0}
    total = int(row["total"] or 0)
    decided = int(row["decided"] or 0)
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
        "pending": max(total - decided, 0),
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
        """
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
            ) AS discarded
        FROM duty_shifts s
        JOIN console_users u ON u.id = s.user_id
        LEFT JOIN news_summaries ns
          ON ns.created_at >= s.starts_at
         AND ns.created_at < s.ends_at
         AND ns.status = 'ready_for_export'
        LEFT JOIN shift_reviews sr
          ON sr.shift_id = s.id
         AND sr.article_id = ns.article_id
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


def fetch_uncovered_news(
    cur: psycopg.Cursor,
    *,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    bounded_limit = max(1, min(limit, 200))
    bounded_offset = max(0, offset)
    uncovered_where = """
        ns.status = 'ready_for_export'
        AND NOT EXISTS (
            SELECT 1
            FROM duty_shifts s
            WHERE s.cancelled_at IS NULL
              AND ns.created_at >= s.starts_at
              AND ns.created_at < s.ends_at
        )
    """
    cur.execute(
        f"""
        SELECT count(*) AS total
        FROM news_summaries ns
        WHERE {uncovered_where}
        """
    )
    total_row = cur.fetchone()
    total = int(total_row["total"]) if total_row else 0
    cur.execute(
        f"""
        SELECT
            ns.article_id,
            ns.title,
            ns.llm_summary,
            ns.llm_source,
            ns.url,
            ns.source,
            ns.created_at,
            ns.publish_time_iso,
            ns.external_importance_score
        FROM news_summaries ns
        WHERE {uncovered_where}
        ORDER BY ns.created_at DESC, ns.article_id
        LIMIT %s OFFSET %s
        """,
        (bounded_limit, bounded_offset),
    )
    return [dict(row) for row in cur.fetchall()], total


__all__ = [
    "SHIFT_REVIEW_SELECT",
    "ShiftReviewConflictError",
    "VALID_DECISIONS",
    "VALID_REPORT_TYPES",
    "fetch_admin_shift_summaries",
    "fetch_shift_clusters",
    "fetch_shift_review",
    "fetch_shift_review_items",
    "set_admin_discarded",
    "fetch_shift_stats",
    "fetch_uncovered_news",
    "shift_contains_article",
    "update_shift_review_order",
    "upsert_shift_review",
]
