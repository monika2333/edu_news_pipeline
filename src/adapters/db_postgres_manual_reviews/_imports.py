from __future__ import annotations

from typing import Any, Mapping, Sequence

import psycopg

from src.adapters.db_postgres_manual_reviews._base import (
    ManualReviewConflictError,
    manual_review_max_rank,
    normalize_report_type_value,
)


def preview_shift_reviews_for_manual(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    article_ids: Sequence[str],
) -> list[dict[str, Any]]:
    normalized_ids = [
        str(article_id).strip()
        for article_id in article_ids
        if str(article_id).strip()
    ]
    if not normalized_ids:
        return []
    cur.execute(
        """
        SELECT
            sr.article_id,
            sr.decision AS duty_decision,
            sr.report_type AS duty_report_type,
            COALESCE(sr.edited_summary, ns.llm_summary, '') AS duty_summary,
            COALESCE(sr.manual_llm_source, ns.llm_source, ns.source, '') AS duty_source,
            mr.id AS existing_id,
            mr.status AS existing_status,
            mr.report_type AS existing_report_type,
            mr.version AS existing_version,
            COALESCE(mr.summary, ns.llm_summary, '') AS existing_summary,
            COALESCE(mr.manual_llm_source, ns.llm_source, ns.source, '') AS existing_source,
            ns.title,
            ns.llm_summary,
            ns.llm_source,
            ns.source
        FROM shift_reviews sr
        JOIN news_summaries ns ON ns.article_id = sr.article_id
        LEFT JOIN manual_reviews mr ON mr.article_id = sr.article_id
        WHERE sr.shift_id = %s
          AND sr.article_id = ANY(%s)
        ORDER BY array_position(%s::text[], sr.article_id)
        """,
        (shift_id, normalized_ids, normalized_ids),
    )
    return [dict(row) for row in cur.fetchall()]


def _fetch_shift_reviews_for_manual_import(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    article_ids: Sequence[str],
) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            sr.article_id,
            sr.edited_summary,
            sr.manual_llm_source,
            sr.notes,
            ns.llm_summary,
            ns.score
        FROM shift_reviews sr
        JOIN news_summaries ns ON ns.article_id = sr.article_id
        WHERE sr.shift_id = %s
          AND sr.article_id = ANY(%s)
        ORDER BY array_position(%s::text[], sr.article_id)
        FOR UPDATE OF sr
        """,
        (shift_id, article_ids, article_ids),
    )
    return [dict(row) for row in cur.fetchall()]


def _return_manual_import_row(cur: psycopg.Cursor) -> dict[str, Any]:
    row = cur.fetchone()
    if not row:
        raise ManualReviewConflictError(
            "汇总审阅内容在导入前已变化，请重新比较"
        )
    return dict(row)


def _resolved_import_text(
    resolution: Mapping[str, Any],
    key: str,
    fallback: Any,
) -> Any:
    if key not in resolution:
        return fallback
    value = resolution.get(key)
    return None if value is None else str(value)


def import_shift_reviews_into_manual(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    article_ids: Sequence[str],
    target_status: str,
    report_type: str,
    actor_username: str,
    actor_user_id: str,
    existing_reviews: Sequence[Mapping[str, Any]],
    conflict_resolutions: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized_ids = [
        str(article_id).strip()
        for article_id in article_ids
        if str(article_id).strip()
    ]
    if not normalized_ids:
        return []
    if target_status not in {"selected", "backup"}:
        raise ValueError("Imported duty results must be selected or backup")
    normalized_report_type = normalize_report_type_value(report_type)
    if not normalized_report_type:
        raise ValueError("A report type is required")
    rows = _fetch_shift_reviews_for_manual_import(
        cur,
        shift_id=shift_id,
        article_ids=normalized_ids,
    )
    if len(rows) != len(set(normalized_ids)):
        found = {str(row["article_id"]) for row in rows}
        missing = sorted(set(normalized_ids) - found)
        raise ValueError(f"Shift reviews not found: {missing}")
    existing_by_id = {
        str(row["article_id"]): row
        for row in existing_reviews
    }
    uses_rank = target_status in {"selected", "backup"}
    next_rank = (
        manual_review_max_rank(
            cur,
            target_status,
            report_type=normalized_report_type,
        )
        if uses_rank
        else 0
    )
    imported: list[dict[str, Any]] = []
    rank_offset = 0
    for row in rows:
        article_id = str(row["article_id"])
        existing = existing_by_id.get(article_id)
        has_conflict = bool(
            existing
            and existing.get("status") not in {"pending", "discarded"}
        )
        resolution = conflict_resolutions.get(article_id, {})
        if has_conflict:
            if not resolution:
                raise ManualReviewConflictError(
                    f"新闻 {article_id} 已在汇总审阅中，请先选择保留版本"
                )
            expected_version = resolution.get("existing_version")
            if expected_version is None or int(expected_version) != int(existing["version"]):
                raise ManualReviewConflictError(
                    f"新闻 {article_id} 在导入前已被更新，请重新比较"
                )
            choice = resolution.get("choice")
            if choice not in {"existing", "duty"}:
                raise ValueError(f"Invalid import resolution for {article_id}")
            if choice == "existing":
                cur.execute(
                    """
                    UPDATE manual_reviews
                    SET
                        summary = %s,
                        manual_llm_source = %s,
                        decided_by = %s,
                        decided_by_user_id = %s,
                        decided_at = now(),
                        version = version + 1,
                        updated_at = now()
                    WHERE article_id = %s
                    RETURNING
                        id, article_id, status, summary, rank, notes, score,
                        decided_by, decided_by_user_id, decided_at,
                        manual_llm_source, report_type, version, created_at, updated_at
                    """,
                    (
                        _resolved_import_text(resolution, "summary", existing.get("summary")),
                        _resolved_import_text(
                            resolution,
                            "manual_llm_source",
                            existing.get("manual_llm_source"),
                        ),
                        actor_username,
                        actor_user_id,
                        article_id,
                    ),
                )
                imported.append(_return_manual_import_row(cur))
                continue

        if uses_rank:
            rank_offset += 1
        rank = next_rank + rank_offset if uses_rank else None
        summary = row.get("edited_summary") or row.get("llm_summary")
        source = row.get("manual_llm_source")
        if existing:
            summary = _resolved_import_text(resolution, "summary", summary)
            source = _resolved_import_text(resolution, "manual_llm_source", source)
            cur.execute(
                """
                UPDATE manual_reviews
                SET
                    status = %s,
                    summary = %s,
                    rank = %s,
                    notes = %s,
                    score = COALESCE(%s, score),
                    decided_by = %s,
                    decided_by_user_id = %s,
                    decided_at = now(),
                    manual_llm_source = %s,
                    report_type = %s,
                    version = version + 1,
                    updated_at = now()
                WHERE article_id = %s
                RETURNING
                    id, article_id, status, summary, rank, notes, score,
                    decided_by, decided_by_user_id, decided_at,
                    manual_llm_source, report_type, version, created_at, updated_at
                """,
                (
                    target_status,
                    summary,
                    rank,
                    row.get("notes"),
                    row.get("score"),
                    actor_username,
                    actor_user_id,
                    source,
                    normalized_report_type,
                    article_id,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO manual_reviews (
                    article_id,
                    status,
                    summary,
                    rank,
                    notes,
                    score,
                    decided_by,
                    decided_by_user_id,
                    decided_at,
                    manual_llm_source,
                    report_type,
                    version
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), %s, %s, 1)
                ON CONFLICT (article_id) DO NOTHING
                RETURNING
                    id,
                    article_id,
                    status,
                    summary,
                    rank,
                    notes,
                    score,
                    decided_by,
                    decided_by_user_id,
                    decided_at,
                    manual_llm_source,
                    report_type,
                    version,
                    created_at,
                    updated_at
                """,
                (
                    article_id,
                    target_status,
                    summary,
                    rank,
                    row.get("notes"),
                    row.get("score"),
                    actor_username,
                    actor_user_id,
                    source,
                    normalized_report_type,
                ),
            )
        imported.append(_return_manual_import_row(cur))
    return imported

__all__ = [
    "_fetch_shift_reviews_for_manual_import",
    "_resolved_import_text",
    "_return_manual_import_row",
    "import_shift_reviews_into_manual",
    "preview_shift_reviews_for_manual",
]
