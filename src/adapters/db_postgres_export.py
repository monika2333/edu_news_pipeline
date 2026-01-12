from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import psycopg
from psycopg.types.json import Json

from src.adapters.db_postgres_shared import article_hash, iso_datetime, json_safe
from src.domain import ExportCandidate


def fetch_export_candidates(cur: psycopg.Cursor, min_score: float) -> List[ExportCandidate]:
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
        record_hash = article_hash(article_id, url, title)
        score_details = row.get("score_details") or {}
        if isinstance(score_details, list):
            score_details = {}
        out.append(
            ExportCandidate(
                filtered_article_id=article_id,
                raw_article_id=article_id,
                article_hash=record_hash,
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
                external_importance_checked_at=iso_datetime(row.get("external_importance_checked_at")),
            )
        )
    return out


def get_batch_by_tag(cur: psycopg.Cursor, report_tag: str) -> Optional[Dict[str, Any]]:
    query = """
        SELECT id, report_date, sequence_no, export_payload
        FROM brief_batches
        WHERE generated_by = %s
        LIMIT 1
    """
    cur.execute(query, (report_tag,))
    row = cur.fetchone()
    return dict(row) if row else None


def parse_report_tag(report_tag: str) -> Tuple[date, str]:
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


def create_batch(cur: psycopg.Cursor, report_tag: str) -> Dict[str, Any]:
    report_date, suffix = parse_report_tag(report_tag)
    fetch_query = """
        SELECT sequence_no
        FROM brief_batches
        WHERE report_date = %s
        ORDER BY sequence_no DESC
        LIMIT 1
    """
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


def get_export_history(cur: psycopg.Cursor, report_tag: str) -> Tuple[Set[str], Optional[str]]:
    batch = get_batch_by_tag(cur, report_tag)
    if not batch:
        return set(), None
    batch_id = str(batch["id"])
    query = "SELECT article_id FROM brief_items WHERE brief_batch_id = %s"
    cur.execute(query, (batch_id,))
    rows = cur.fetchall()
    ids = {str(row.get("article_id")) for row in rows if row.get("article_id")}
    return ids, batch_id


def get_all_exported_article_ids(cur: psycopg.Cursor) -> Set[str]:
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
    cur: psycopg.Cursor,
    report_tag: str,
    exported: Sequence[Tuple[ExportCandidate, str]],
    *,
    output_path: str,
) -> None:
    existing_ids, batch_id = get_export_history(cur, report_tag)
    if batch_id is None:
        batch = create_batch(cur, report_tag)
        batch_id = str(batch["id"])
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
            "title": json_safe(candidate.title),
            "score": json_safe(candidate.score),
            "original_url": json_safe(candidate.original_url),
            "published_at": json_safe(candidate.published_at),
            "source": json_safe(candidate.source),
            "is_beijing_related": json_safe(candidate.is_beijing_related),
            "sentiment_label": json_safe(candidate.sentiment_label),
            "sentiment_confidence": json_safe(candidate.sentiment_confidence),
            "external_importance_score": json_safe(candidate.external_importance_score),
            "external_importance_checked_at": json_safe(candidate.external_importance_checked_at),
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


def fetch_latest_brief_batch(cur: psycopg.Cursor) -> Optional[Dict[str, Any]]:
    query = """
        SELECT *
        FROM brief_batches
        ORDER BY report_date DESC, sequence_no DESC
        LIMIT 1
    """
    cur.execute(query)
    row = cur.fetchone()
    return dict(row) if row else None


def fetch_brief_items_by_batch(cur: psycopg.Cursor, batch_id: str) -> List[Dict[str, Any]]:
    query = """
        SELECT id, article_id, section, order_index, final_summary, metadata
        FROM brief_items
        WHERE brief_batch_id = %s
        ORDER BY order_index ASC
    """
    cur.execute(query, (batch_id,))
    rows = cur.fetchall()
    return [dict(row) for row in rows]


def fetch_brief_item_count(cur: psycopg.Cursor, batch_id: str) -> int:
    query = "SELECT COUNT(*) AS total FROM brief_items WHERE brief_batch_id = %s"
    cur.execute(query, (batch_id,))
    row = cur.fetchone()
    return int(row["total"]) if row else 0


__all__ = [
    "create_batch",
    "fetch_brief_item_count",
    "fetch_brief_items_by_batch",
    "fetch_export_candidates",
    "fetch_latest_brief_batch",
    "get_all_exported_article_ids",
    "get_batch_by_tag",
    "get_export_history",
    "parse_report_tag",
    "record_export",
]
