from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

import psycopg

from src.adapters.db_postgres_submission_archive._base import (
    ItemFieldUpdateResult,
    _ITEM_PUBLIC_COLUMNS,
)


def update_item_fields(
    cur: psycopg.Cursor,
    *,
    item_id: str,
    title: str,
    body: str,
    source: Optional[str],
    urls: Sequence[str],
    norm_title: str,
    norm_title_hash: str,
) -> ItemFieldUpdateResult:
    """Edit stored text fields of one item; refuses worker-owned items."""
    cur.execute(
        """
        select link_status, title, body
        from submitted_report_items
        where id = %s
        for update
        """,
        (item_id,),
    )
    context = cur.fetchone()
    if not context:
        return {"state": "not_found", "item": None}
    if context["link_status"] == "processing":
        return {"state": "processing", "item": None}

    # 标题或正文变化会让既有查重向量失效：清空后由 backfill-submission-embeddings 重算
    text_changed = context["title"] != title or context["body"] != body
    cur.execute(
        f"""
        update submitted_report_items i
        set title = %s,
            body = %s,
            source = %s,
            urls = %s,
            norm_title = %s,
            norm_title_hash = %s,
            embedding = case when %s then null else embedding end,
            embedding_model = case when %s then null else embedding_model end,
            embedded_at = case when %s then null else embedded_at end,
            updated_at = now()
        where i.id = %s
        returning {_ITEM_PUBLIC_COLUMNS}
        """,
        (
            title,
            body,
            source,
            list(urls),
            norm_title,
            norm_title_hash,
            text_changed,
            text_changed,
            text_changed,
            item_id,
        ),
    )
    row = cur.fetchone()
    if not row:
        return {"state": "not_found", "item": None}
    return {"state": "updated", "item": dict(row)}


def search_items(
    cur: psycopg.Cursor,
    *,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    pattern = f"%{query}%"
    cur.execute(
        """
        select
            i.id,
            i.report_id,
            i.section,
            i.marker,
            i.order_index,
            i.title,
            i.body,
            i.source,
            i.urls,
            i.link_status,
            r.report_type,
            r.report_date,
            r.title_line as report_title_line
        from submitted_report_items i
        join submitted_reports r on r.id = i.report_id
        where i.title ilike %s or i.body ilike %s or i.source ilike %s
        order by r.report_date desc, i.order_index
        limit %s
        """,
        (pattern, pattern, pattern, max(1, min(limit, 200))),
    )
    return [dict(row) for row in cur.fetchall()]


def fetch_items_missing_embeddings(
    cur: psycopg.Cursor,
    *,
    lookback_days: int,
    limit: int,
) -> list[dict[str, Any]]:
    cur.execute(
        """
        select i.id, i.title, i.body
        from submitted_report_items i
        join submitted_reports r on r.id = i.report_id
        where r.report_date >= current_date - (%s * interval '1 day')
          and i.embedding is null
        order by i.created_at, i.id
        limit %s
        """,
        (
            max(1, lookback_days),
            max(1, min(limit, 1000)),
        ),
    )
    return [dict(row) for row in cur.fetchall()]


def update_item_embeddings(
    cur: psycopg.Cursor,
    embeddings: Sequence[Mapping[str, Any]],
) -> int:
    updated = 0
    for item in embeddings:
        cur.execute(
            """
            update submitted_report_items
            set embedding = %s,
                embedding_model = %s,
                embedded_at = now(),
                updated_at = now()
            where id = %s and embedding is null
            """,
            (
                item.get("embedding"),
                item.get("embedding_model"),
                item.get("item_id"),
            ),
        )
        updated += cur.rowcount
    return updated

__all__ = [
    "fetch_items_missing_embeddings",
    "search_items",
    "update_item_embeddings",
    "update_item_fields",
]
