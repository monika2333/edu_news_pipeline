from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping, Optional, Sequence

import psycopg

from src.adapters.db_postgres_submission_archive._base import (
    ManualLinkMutationResult,
    _ITEM_PUBLIC_COLUMNS,
)


def fetch_link_candidate_titles(
    cur: psycopg.Cursor,
    *,
    compiled_date: date,
    window_days: int,
) -> list[dict[str, Any]]:
    cur.execute(
        """
        select
            ns.article_id,
            coalesce(ns.title, '') as title
        from news_summaries ns
        where ns.created_at >= %s::date - (%s * interval '1 day')
          and ns.created_at < %s::date + interval '2 days'
        order by ns.created_at desc
        """,
        (compiled_date, window_days, compiled_date),
    )
    return [dict(row) for row in cur.fetchall()]


def fetch_link_candidate_bodies(
    cur: psycopg.Cursor,
    *,
    article_ids: Sequence[str],
) -> list[dict[str, Any]]:
    if not article_ids:
        return []
    cur.execute(
        """
        with requested as (
            select article_id, order_index
            from unnest(%s::text[]) with ordinality
                as requested(article_id, order_index)
        ),
        latest_brief as (
            select distinct on (bi.article_id)
                bi.article_id,
                bi.final_summary
            from brief_items bi
            join requested req on req.article_id = bi.article_id
            where nullif(btrim(bi.final_summary), '') is not null
            order by bi.article_id, bi.created_at desc
        )
        select
            req.article_id,
            coalesce(
                lb.final_summary,
                nullif(btrim(mr.summary), ''),
                nullif(btrim(ns.llm_summary), ''),
                ''
            ) as body
        from requested req
        left join latest_brief lb on lb.article_id = req.article_id
        left join manual_reviews mr on mr.article_id = req.article_id
        left join news_summaries ns on ns.article_id = req.article_id
        order by req.order_index
        """,
        (list(article_ids),),
    )
    return [dict(row) for row in cur.fetchall()]


def update_link_results(
    cur: psycopg.Cursor,
    results: Sequence[Mapping[str, Any]],
) -> None:
    for result in results:
        cur.execute(
            """
            update submitted_report_items
            set article_id = %s,
                link_status = %s,
                link_title_score = %s,
                link_body_score = %s,
                link_combined_score = %s,
                best_candidate_article_id = %s,
                link_matched_at = case
                    when %s = 'matched' then now()
                    else null
                end,
                updated_at = now()
            where id = %s
            """,
            (
                result.get("article_id"),
                result.get("status"),
                result.get("title_score"),
                result.get("body_score"),
                result.get("combined_score"),
                result.get("best_candidate_article_id"),
                result.get("status"),
                result.get("item_id"),
            ),
        )


def fetch_pending_links(
    cur: psycopg.Cursor,
    *,
    limit: int,
    offset: int,
    report_id: Optional[str] = None,
) -> tuple[list[dict[str, Any]], int]:
    count_report_filter = " and report_id = %s" if report_id else ""
    detail_report_filter = " and i.report_id = %s" if report_id else ""
    report_params = (report_id,) if report_id else ()
    cur.execute(
        f"""
        select count(*) as total
        from submitted_report_items
        where link_status = 'pending'
        {count_report_filter}
        """,
        report_params,
    )
    count_row = cur.fetchone()
    total = int(count_row["total"]) if count_row else 0
    cur.execute(
        f"""
        select
            {_ITEM_PUBLIC_COLUMNS},
            r.report_type,
            r.report_date,
            r.title_line as report_title_line,
            ns.title as candidate_title,
            coalesce(
                mr.summary,
                ns.llm_summary,
                ''
            ) as candidate_body,
            ns.source as candidate_source,
            ns.url as candidate_url
        from submitted_report_items i
        join submitted_reports r on r.id = i.report_id
        left join news_summaries ns
          on ns.article_id = i.best_candidate_article_id
        left join manual_reviews mr on mr.article_id = ns.article_id
        where i.link_status = 'pending'
        {detail_report_filter}
        order by r.report_date desc, i.order_index
        limit %s offset %s
        """,
        (
            *report_params,
            max(1, min(limit, 200)),
            max(0, offset),
        ),
    )
    return [dict(row) for row in cur.fetchall()], total


def decide_link(
    cur: psycopg.Cursor,
    *,
    item_id: str,
    accepted: bool,
    actor_user_id: str,
) -> Optional[dict[str, Any]]:
    cur.execute(
        """
        update submitted_report_items
        set article_id = case
                when %s then best_candidate_article_id
                else null
            end,
            link_status = case when %s then 'matched' else 'rejected' end,
            link_decided_by = %s,
            link_matched_at = case when %s then now() else null end,
            updated_at = now()
        where id = %s and link_status = 'pending'
        returning
            id,
            report_id,
            article_id,
            link_status,
            link_title_score,
            link_body_score,
            link_combined_score,
            best_candidate_article_id,
            link_matched_at,
            link_decided_by
        """,
        (accepted, accepted, actor_user_id, accepted, item_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def fetch_manual_link_candidates(
    cur: psycopg.Cursor,
    *,
    item_id: str,
    query: str,
    window_days: int,
    limit: int,
    offset: int,
) -> Optional[dict[str, Any]]:
    cur.execute(
        """
        select
            i.id,
            i.title,
            i.body,
            r.report_type,
            r.report_date,
            r.compiled_date,
            i.link_status,
            i.article_id
        from submitted_report_items i
        join submitted_reports r on r.id = i.report_id
        where i.id = %s
        """,
        (item_id,),
    )
    item_row = cur.fetchone()
    if not item_row:
        return None

    item = dict(item_row)
    compiled_date = item["compiled_date"]
    window_start = compiled_date - timedelta(days=window_days)
    window_end = compiled_date + timedelta(days=window_days)
    pattern = f"%{query}%"
    cur.execute(
        """
        select
            ns.article_id,
            coalesce(ns.title, '') as title,
            ns.source,
            ns.url,
            ns.publish_time_iso,
            ns.created_at as ingested_at,
            coalesce(ns.llm_summary, '') as llm_summary,
            coalesce(
                (
                    select jsonb_agg(
                        jsonb_build_object(
                            'item_id', linked.id,
                            'report_id', linked.report_id,
                            'report_type', linked_report.report_type,
                            'report_date', linked_report.report_date,
                            'title', linked.title
                        )
                        order by linked_report.report_date desc, linked.order_index
                    )
                    from submitted_report_items linked
                    join submitted_reports linked_report
                      on linked_report.id = linked.report_id
                    where linked.article_id = ns.article_id
                      and linked.link_status = 'matched'
                ),
                '[]'::jsonb
            ) as linked_items
        from news_summaries ns
        where ns.created_at >= %s::date
          and ns.created_at < %s::date + interval '1 day'
          and (
              coalesce(ns.title, '') ilike %s
              or coalesce(ns.llm_summary, '') ilike %s
          )
        order by ns.created_at desc, ns.article_id
        limit %s offset %s
        """,
        (window_start, window_end, pattern, pattern, limit + 1, offset),
    )
    rows = [dict(row) for row in cur.fetchall()]
    return {
        "item": item,
        "items": rows[:limit],
        "window_start": window_start,
        "window_end": window_end,
        "has_more": len(rows) > limit,
    }


_LINK_DECISION_RETURNING_COLUMNS = """
    id,
    report_id,
    article_id,
    link_status,
    link_title_score,
    link_body_score,
    link_combined_score,
    best_candidate_article_id,
    link_matched_at,
    link_decided_by
"""


def manual_link_item(
    cur: psycopg.Cursor,
    *,
    item_id: str,
    article_id: str,
    actor_user_id: str,
) -> ManualLinkMutationResult:
    cur.execute(
        """
        select
            i.link_status,
            exists(
                select 1 from news_summaries ns where ns.article_id = %s
            ) as article_exists
        from submitted_report_items i
        where i.id = %s
        for update
        """,
        (article_id, item_id),
    )
    context = cur.fetchone()
    if not context:
        return {"state": "not_found", "item": None}
    if context["link_status"] == "processing":
        return {"state": "processing", "item": None}
    if not context["article_exists"]:
        return {"state": "article_not_found", "item": None}

    cur.execute(
        f"""
        update submitted_report_items
        set article_id = %s,
            link_status = 'matched',
            link_decided_by = %s,
            link_matched_at = now(),
            updated_at = now()
        where id = %s and link_status <> 'processing'
        returning {_LINK_DECISION_RETURNING_COLUMNS}
        """,
        (article_id, actor_user_id, item_id),
    )
    row = cur.fetchone()
    if not row:
        return {"state": "processing", "item": None}
    return {"state": "updated", "item": dict(row)}


def manual_unlink_item(
    cur: psycopg.Cursor,
    *,
    item_id: str,
    actor_user_id: str,
) -> ManualLinkMutationResult:
    cur.execute(
        """
        select link_status
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

    cur.execute(
        f"""
        update submitted_report_items
        set article_id = null,
            link_status = 'unmatched',
            link_matched_at = null,
            link_decided_by = %s,
            updated_at = now()
        where id = %s and link_status <> 'processing'
        returning {_LINK_DECISION_RETURNING_COLUMNS}
        """,
        (actor_user_id, item_id),
    )
    row = cur.fetchone()
    if not row:
        return {"state": "processing", "item": None}
    return {"state": "updated", "item": dict(row)}

__all__ = [
    "decide_link",
    "fetch_link_candidate_bodies",
    "fetch_link_candidate_titles",
    "fetch_manual_link_candidates",
    "fetch_pending_links",
    "manual_link_item",
    "manual_unlink_item",
    "update_link_results",
]
