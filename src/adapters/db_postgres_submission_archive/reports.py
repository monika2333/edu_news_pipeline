from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Optional, Sequence

import psycopg

import src.adapters.db_postgres_submission_archive as _facade
from src.adapters.db_postgres_submission_archive._base import _ITEM_PUBLIC_COLUMNS
from src.adapters.db_postgres_submission_archive.dedup import (
    fetch_item_duplicate_match_summaries,
)
from src.domain.submission_archive_config import (
    COVERAGE_EXCLUDED_REPORT_TYPE,
    COVERAGE_EXCLUDED_SECTION,
    is_coverage_excluded,
)


def find_report_conflict(
    cur: psycopg.Cursor,
    *,
    report_type: str,
    report_date: date,
) -> Optional[dict[str, Any]]:
    cur.execute(
        """
        select id, report_type, report_date, title_line, item_count
        from submitted_reports
        where report_type = %s and report_date = %s
        order by imported_at desc
        limit 1
        """,
        (report_type, report_date),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def insert_report(
    cur: psycopg.Cursor,
    *,
    report_type: str,
    report_date: date,
    compiled_date: date,
    issue_no: Optional[str],
    title_line: Optional[str],
    pasted_text: str,
    ingest_source: str = "console",
    source_message_id: Optional[str] = None,
    source_sender_id: Optional[str] = None,
    ignore_source_conflict: bool = False,
) -> Optional[dict[str, Any]]:
    conflict_sql = (
        "on conflict (ingest_source, source_message_id) do nothing"
        if ignore_source_conflict
        else ""
    )
    cur.execute(
        f"""
        insert into submitted_reports (
            report_type,
            report_date,
            compiled_date,
            issue_no,
            title_line,
            pasted_text,
            ingest_source,
            source_message_id,
            source_sender_id
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        {conflict_sql}
        returning *
        """,
        (
            report_type,
            report_date,
            compiled_date,
            issue_no,
            title_line,
            pasted_text,
            ingest_source,
            source_message_id,
            source_sender_id,
        ),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def insert_report_items(
    cur: psycopg.Cursor,
    *,
    report_id: str,
    items: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    inserted: list[dict[str, Any]] = []
    for item in items:
        cur.execute(
            """
            insert into submitted_report_items (
                report_id,
                section,
                marker,
                order_index,
                title,
                body,
                source,
                urls,
                norm_title,
                norm_title_hash
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            returning *
            """,
            (
                report_id,
                item.get("section"),
                item.get("marker"),
                item.get("order_index", 0),
                item.get("title"),
                item.get("body") or "",
                item.get("source"),
                list(item.get("urls") or []),
                item.get("norm_title"),
                item.get("norm_title_hash"),
            ),
        )
        row = cur.fetchone()
        if row:
            inserted.append(dict(row))
    cur.execute(
        """
        update submitted_reports
        set item_count = %s, updated_at = now()
        where id = %s
        """,
        (len(inserted), report_id),
    )
    return inserted


def delete_report(cur: psycopg.Cursor, report_id: str) -> bool:
    cur.execute(
        "delete from submitted_reports where id = %s",
        (report_id,),
    )
    return cur.rowcount > 0


def fetch_reports(
    cur: psycopg.Cursor,
    *,
    report_type: Optional[str],
    date_from: Optional[date],
    date_to: Optional[date],
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    clauses = ["true"]
    params: list[Any] = []
    if report_type:
        clauses.append("r.report_type = %s")
        params.append(report_type)
    if date_from:
        clauses.append("r.report_date >= %s")
        params.append(date_from)
    if date_to:
        clauses.append("r.report_date <= %s")
        params.append(date_to)
    where_sql = " and ".join(clauses)
    cur.execute(
        f"select count(*) as total from submitted_reports r where {where_sql}",
        tuple(params),
    )
    count_row = cur.fetchone()
    total = int(count_row["total"]) if count_row else 0
    cur.execute(
        f"""
        select
            r.*,
            count(i.id) filter (where i.link_status = 'matched') as matched_count,
            count(i.id) filter (
                where i.link_status = 'processing'
            ) as processing_count,
            count(i.id) filter (where i.link_status = 'pending') as pending_count,
            count(i.id) filter (
                where i.link_status in ('unmatched', 'rejected')
                  and not (
                      r.report_type is not distinct from %s
                      and i.section is not distinct from %s
                  )
            ) as unmatched_count
        from submitted_reports r
        left join submitted_report_items i on i.report_id = r.id
        where {where_sql}
        group by r.id
        order by r.report_date desc, r.imported_at desc
        limit %s offset %s
        """,
        tuple(
            [
                COVERAGE_EXCLUDED_REPORT_TYPE,
                COVERAGE_EXCLUDED_SECTION,
                *params,
                max(1, min(limit, 200)),
                max(0, offset),
            ]
        ),
    )
    return [dict(row) for row in cur.fetchall()], total


def _export_where_sql(
    *,
    date_from: Optional[date],
    date_to: Optional[date],
    report_types: Optional[Sequence[str]],
) -> tuple[str, list[Any]]:
    clauses = ["true"]
    params: list[Any] = []
    if date_from:
        clauses.append("r.report_date >= %s")
        params.append(date_from)
    if date_to:
        clauses.append("r.report_date <= %s")
        params.append(date_to)
    if report_types:
        clauses.append("r.report_type = any(%s)")
        params.append(list(report_types))
    return " and ".join(clauses), params


def fetch_export_rows(
    cur: psycopg.Cursor,
    *,
    date_from: Optional[date],
    date_to: Optional[date],
    report_types: Optional[Sequence[str]],
    limit: int,
) -> list[dict[str, Any]]:
    where_sql, params = _export_where_sql(
        date_from=date_from,
        date_to=date_to,
        report_types=report_types,
    )
    cur.execute(
        f"""
        select
            r.report_type,
            r.report_date,
            r.compiled_date,
            r.issue_no,
            i.order_index,
            i.section,
            i.title,
            i.body,
            i.source,
            i.urls
        from submitted_reports r
        join submitted_report_items i on i.report_id = r.id
        where {where_sql}
        order by r.report_date, r.report_type, i.order_index, i.id
        limit %s
        """,
        tuple([*params, max(1, limit)]),
    )
    return [dict(row) for row in cur.fetchall()]


def count_export_rows(
    cur: psycopg.Cursor,
    *,
    date_from: Optional[date],
    date_to: Optional[date],
    report_types: Optional[Sequence[str]],
) -> tuple[int, int]:
    where_sql, params = _export_where_sql(
        date_from=date_from,
        date_to=date_to,
        report_types=report_types,
    )
    cur.execute(
        f"""
        select
            count(distinct r.id) as report_count,
            count(i.id) as item_count
        from submitted_reports r
        join submitted_report_items i on i.report_id = r.id
        where {where_sql}
        """,
        tuple(params),
    )
    row = cur.fetchone()
    if not row:
        return 0, 0
    return int(row["report_count"]), int(row["item_count"])


def fetch_report(
    cur: psycopg.Cursor,
    report_id: str,
) -> Optional[dict[str, Any]]:
    cur.execute(
        "select * from submitted_reports where id = %s",
        (report_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    report = dict(row)
    cur.execute(
        f"""
        select {_ITEM_PUBLIC_COLUMNS}
        from submitted_report_items i
        where report_id = %s
        order by order_index, id
        """,
        (report_id,),
    )
    report["items"] = [dict(item) for item in cur.fetchall()]
    summaries: dict[str, dict[str, Any]] = {}
    # Read through the public module so the name stays monkeypatchable
    # exactly as it was when this file was part of that module.
    if report.get("report_type") in _facade.PRIOR_MATCH_REPORT_TYPES:
        summaries = fetch_item_duplicate_match_summaries(
            cur,
            [str(item["id"]) for item in report["items"]],
        )
    for item in report["items"]:
        item["prior_match"] = summaries.get(str(item["id"]))
        item["coverage_excluded"] = is_coverage_excluded(
            report.get("report_type"),
            item.get("section"),
        )
    return report


def mark_prior_match_completed(
    cur: psycopg.Cursor,
    report_id: str,
) -> None:
    cur.execute(
        """
        update submitted_reports
        set prior_match_completed_at = now()
        where id = %s
        """,
        (report_id,),
    )


def fetch_report_ids_by_type(
    cur: psycopg.Cursor,
    *,
    report_type: str,
) -> list[str]:
    cur.execute(
        """
        select id
        from submitted_reports
        where report_type = %s
        order by report_date, imported_at, id
        """,
        (report_type,),
    )
    return [str(row["id"]) for row in cur.fetchall()]


def fetch_report_by_source_message(
    cur: psycopg.Cursor,
    *,
    ingest_source: str,
    source_message_id: str,
) -> Optional[dict[str, Any]]:
    cur.execute(
        """
        select id
        from submitted_reports
        where ingest_source = %s and source_message_id = %s
        """,
        (ingest_source, source_message_id),
    )
    row = cur.fetchone()
    if not row:
        return None
    return fetch_report(cur, str(row["id"]))

__all__ = [
    "count_export_rows",
    "delete_report",
    "fetch_export_rows",
    "fetch_report",
    "fetch_report_by_source_message",
    "fetch_report_ids_by_type",
    "fetch_reports",
    "find_report_conflict",
    "insert_report",
    "insert_report_items",
    "mark_prior_match_completed",
]
