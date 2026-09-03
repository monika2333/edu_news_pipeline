from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Literal, Mapping, Optional, Sequence, TypedDict

import psycopg

from src.domain.report_type import NEWS_REPORT_TYPES

if TYPE_CHECKING:
    from src.adapters.db_postgres_core import PostgresAdapter


PRIOR_MATCH_REPORT_TYPES = frozenset({"feedback"})


class ManualLinkMutationResult(TypedDict):
    state: Literal[
        "updated",
        "not_found",
        "processing",
        "article_not_found",
    ]
    item: Optional[dict[str, Any]]


class ItemFieldUpdateResult(TypedDict):
    state: Literal[
        "updated",
        "not_found",
        "processing",
    ]
    item: Optional[dict[str, Any]]


class SubmissionArchiveNamespace:
    """Access to submitted reports, link decisions, and duplicate metadata."""

    def __init__(self, adapter: PostgresAdapter) -> None:
        self._adapter = adapter

    def find_report_conflict(
        self,
        *,
        report_type: str,
        report_date: date,
    ) -> Optional[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return find_report_conflict(
                cur,
                report_type=report_type,
                report_date=report_date,
            )

    def create_report(
        self,
        *,
        report: Mapping[str, Any],
        items: Sequence[Mapping[str, Any]],
        replace_report_id: Optional[str] = None,
    ) -> dict[str, Any]:
        with self._adapter.transaction() as cur:
            if replace_report_id:
                delete_report(cur, replace_report_id)
            created = insert_report(cur, **report)
            if created is None:
                raise RuntimeError("Failed to create submitted report")
            created["items"] = insert_report_items(
                cur,
                report_id=str(created["id"]),
                items=items,
            )
            created["item_count"] = len(created["items"])
            return created

    def create_report_idempotent(
        self,
        *,
        report: Mapping[str, Any],
        items: Sequence[Mapping[str, Any]],
        replace_report_id: Optional[str] = None,
    ) -> tuple[dict[str, Any], bool]:
        """Create one externally sourced report or return its prior result."""
        ingest_source = str(report.get("ingest_source") or "").strip()
        source_message_id = str(report.get("source_message_id") or "").strip()
        if not ingest_source or not source_message_id:
            raise ValueError("External report source and message id are required")

        with self._adapter.transaction() as cur:
            existing = fetch_report_by_source_message(
                cur,
                ingest_source=ingest_source,
                source_message_id=source_message_id,
            )
            if existing:
                return existing, False
            if replace_report_id:
                delete_report(cur, replace_report_id)
            created = insert_report(
                cur,
                **report,
                ignore_source_conflict=True,
            )
            if created is None:
                existing = fetch_report_by_source_message(
                    cur,
                    ingest_source=ingest_source,
                    source_message_id=source_message_id,
                )
                if not existing:
                    raise RuntimeError(
                        "Failed to resolve idempotent submitted report"
                    )
                return existing, False
            created["items"] = insert_report_items(
                cur,
                report_id=str(created["id"]),
                items=items,
            )
            created["item_count"] = len(created["items"])
            return created, True

    def fetch_reports(
        self,
        *,
        report_type: Optional[str],
        date_from: Optional[date],
        date_to: Optional[date],
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        with self._adapter._cursor() as cur:
            return fetch_reports(
                cur,
                report_type=report_type,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
                offset=offset,
            )

    def fetch_report(self, report_id: str) -> Optional[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_report(cur, report_id)

    def mark_prior_match_completed(self, report_id: str) -> None:
        with self._adapter._cursor() as cur:
            mark_prior_match_completed(cur, report_id)

    def fetch_report_ids_by_type(self, report_type: str) -> list[str]:
        with self._adapter._cursor() as cur:
            return fetch_report_ids_by_type(cur, report_type=report_type)

    def fetch_report_by_source_message(
        self,
        *,
        ingest_source: str,
        source_message_id: str,
    ) -> Optional[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_report_by_source_message(
                cur,
                ingest_source=ingest_source,
                source_message_id=source_message_id,
            )

    def search_report_items(self, *, query: str, limit: int) -> list[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return search_items(cur, query=query, limit=limit)

    def fetch_link_candidate_titles(
        self,
        *,
        compiled_date: date,
        window_days: int,
    ) -> list[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_link_candidate_titles(
                cur,
                compiled_date=compiled_date,
                window_days=window_days,
            )

    def fetch_link_candidate_bodies(
        self,
        *,
        article_ids: Sequence[str],
    ) -> list[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_link_candidate_bodies(cur, article_ids=article_ids)

    def update_link_results(self, results: Sequence[Mapping[str, Any]]) -> None:
        with self._adapter.transaction() as cur:
            update_link_results(cur, results)

    def fetch_pending_links(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        with self._adapter._cursor() as cur:
            return fetch_pending_links(cur, limit=limit, offset=offset)

    def decide_link(
        self,
        *,
        item_id: str,
        accepted: bool,
        actor_user_id: str,
    ) -> Optional[dict[str, Any]]:
        with self._adapter.transaction() as cur:
            return decide_link(
                cur,
                item_id=item_id,
                accepted=accepted,
                actor_user_id=actor_user_id,
            )

    def fetch_manual_link_candidates(
        self,
        *,
        item_id: str,
        query: str,
        window_days: int,
        limit: int,
        offset: int,
    ) -> Optional[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_manual_link_candidates(
                cur,
                item_id=item_id,
                query=query,
                window_days=window_days,
                limit=limit,
                offset=offset,
            )

    def manual_link_item(
        self,
        *,
        item_id: str,
        article_id: str,
        actor_user_id: str,
    ) -> ManualLinkMutationResult:
        with self._adapter.transaction() as cur:
            return manual_link_item(
                cur,
                item_id=item_id,
                article_id=article_id,
                actor_user_id=actor_user_id,
            )

    def manual_unlink_item(
        self,
        *,
        item_id: str,
        actor_user_id: str,
    ) -> ManualLinkMutationResult:
        with self._adapter.transaction() as cur:
            return manual_unlink_item(
                cur,
                item_id=item_id,
                actor_user_id=actor_user_id,
            )

    def update_item_fields(
        self,
        *,
        item_id: str,
        title: str,
        body: str,
        source: Optional[str],
        urls: Sequence[str],
        norm_title: str,
        norm_title_hash: str,
    ) -> ItemFieldUpdateResult:
        with self._adapter.transaction() as cur:
            return update_item_fields(
                cur,
                item_id=item_id,
                title=title,
                body=body,
                source=source,
                urls=urls,
                norm_title=norm_title,
                norm_title_hash=norm_title_hash,
            )

    def fetch_items_missing_embeddings(self, *, limit: int) -> list[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_items_missing_embeddings(cur, limit=limit)

    def update_item_embeddings(
        self,
        embeddings: Sequence[Mapping[str, Any]],
    ) -> int:
        with self._adapter.transaction() as cur:
            return update_item_embeddings(cur, embeddings)

    def fetch_item_match_inputs(
        self,
        item_ids: Sequence[str],
    ) -> list[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_item_match_inputs(cur, item_ids=item_ids)

    def fetch_prior_submission_candidates(
        self,
        *,
        report_date: date,
        lookback_days: int,
    ) -> list[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_prior_submission_candidates(
                cur,
                report_date=report_date,
                lookback_days=lookback_days,
            )

    def replace_item_duplicate_matches(
        self,
        *,
        item_ids: Sequence[str],
        matches: Sequence[Mapping[str, Any]],
    ) -> int:
        with self._adapter.transaction() as cur:
            return replace_item_duplicate_matches(
                cur,
                item_ids=item_ids,
                matches=matches,
            )

    def fetch_item_duplicate_match_summaries(
        self,
        item_ids: Sequence[str],
    ) -> dict[str, dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_item_duplicate_match_summaries(cur, item_ids)

    def fetch_item_duplicate_match_details(
        self,
        item_id: str,
    ) -> list[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_item_duplicate_match_details(cur, item_id)

    def fetch_embeddings(self, *, lookback_days: int) -> list[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_archive_embeddings(cur, lookback_days=lookback_days)

    def fetch_news_for_dedup(
        self,
        *,
        limit: Optional[int],
    ) -> list[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_news_for_submission_dedup(cur, limit=limit)

    def upsert_duplicate_matches(
        self,
        matches: Sequence[Mapping[str, Any]],
    ) -> int:
        with self._adapter.transaction() as cur:
            return upsert_duplicate_matches(cur, matches)

    def fetch_duplicate_badges(
        self,
        article_ids: Sequence[str],
    ) -> dict[str, dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_duplicate_badges(cur, article_ids)

    def fetch_duplicate_match_details(
        self,
        article_id: str,
    ) -> list[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_duplicate_match_details(cur, article_id)

    def dismiss_duplicate_matches(
        self,
        *,
        article_id: str,
        actor_user_id: str,
    ) -> int:
        with self._adapter.transaction() as cur:
            return dismiss_duplicate_matches(
                cur,
                article_id=article_id,
                actor_user_id=actor_user_id,
            )


# 条目对外返回字段：排除 embedding（bytea 无法 JSON 序列化）与归一化内部字段。
_ITEM_PUBLIC_COLUMNS = """
    i.id,
    i.report_id,
    i.section,
    i.marker,
    i.order_index,
    i.title,
    i.body,
    i.source,
    i.urls,
    i.article_id,
    i.link_status,
    i.link_title_score,
    i.link_body_score,
    i.link_combined_score,
    i.best_candidate_article_id,
    i.link_matched_at,
    i.created_at
"""


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
            ) as unmatched_count
        from submitted_reports r
        left join submitted_report_items i on i.report_id = r.id
        where {where_sql}
        group by r.id
        order by r.report_date desc, r.imported_at desc
        limit %s offset %s
        """,
        tuple(params + [max(1, min(limit, 200)), max(0, offset)]),
    )
    return [dict(row) for row in cur.fetchall()], total


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
    if report.get("report_type") in PRIOR_MATCH_REPORT_TYPES:
        summaries = fetch_item_duplicate_match_summaries(
            cur,
            [str(item["id"]) for item in report["items"]],
        )
    for item in report["items"]:
        item["prior_match"] = summaries.get(str(item["id"]))
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
) -> tuple[list[dict[str, Any]], int]:
    cur.execute(
        """
        select count(*) as total
        from submitted_report_items
        where link_status = 'pending'
        """
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
        order by r.report_date desc, i.order_index
        limit %s offset %s
        """,
        (max(1, min(limit, 200)), max(0, offset)),
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
    limit: int,
) -> list[dict[str, Any]]:
    cur.execute(
        """
        select id, title, body
        from submitted_report_items
        where embedding is null
        order by created_at, id
        limit %s
        """,
        (max(1, min(limit, 1000)),),
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


def fetch_item_match_inputs(
    cur: psycopg.Cursor,
    *,
    item_ids: Sequence[str],
) -> list[dict[str, Any]]:
    normalized_ids = [str(item_id) for item_id in item_ids if item_id]
    if not normalized_ids:
        return []
    cur.execute(
        """
        select id, article_id, norm_title_hash, embedding, embedding_model
        from submitted_report_items
        where id = any(%s::uuid[])
        order by order_index, id
        """,
        (normalized_ids,),
    )
    return [dict(row) for row in cur.fetchall()]


def fetch_prior_submission_candidates(
    cur: psycopg.Cursor,
    *,
    report_date: date,
    lookback_days: int,
) -> list[dict[str, Any]]:
    window_start = report_date - timedelta(days=max(1, lookback_days))
    cur.execute(
        """
        select
            i.id,
            i.article_id,
            i.norm_title_hash,
            i.embedding,
            i.embedding_model
        from submitted_report_items i
        join submitted_reports r on r.id = i.report_id
        where r.report_type = any(%s::text[])
          and r.report_date >= %s
          and r.report_date < %s
        order by r.report_date desc, i.order_index, i.id
        """,
        (
            sorted(NEWS_REPORT_TYPES),
            window_start,
            report_date,
        ),
    )
    return [dict(row) for row in cur.fetchall()]


def replace_item_duplicate_matches(
    cur: psycopg.Cursor,
    *,
    item_ids: Sequence[str],
    matches: Sequence[Mapping[str, Any]],
) -> int:
    normalized_ids = [str(item_id) for item_id in item_ids if item_id]
    if not normalized_ids:
        return 0
    cur.execute(
        """
        delete from submission_item_duplicate_matches
        where item_id = any(%s::uuid[])
        """,
        (normalized_ids,),
    )
    for match in matches:
        cur.execute(
            """
            insert into submission_item_duplicate_matches (
                item_id,
                prior_item_id,
                similarity,
                match_method
            )
            values (%s, %s, %s, %s)
            """,
            (
                match.get("item_id"),
                match.get("prior_item_id"),
                match.get("similarity"),
                match.get("match_method"),
            ),
        )
    return len(matches)


def fetch_item_duplicate_match_summaries(
    cur: psycopg.Cursor,
    item_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    normalized_ids = [str(item_id) for item_id in item_ids if item_id]
    if not normalized_ids:
        return {}
    cur.execute(
        """
        select
            item_id,
            case
                when bool_or(match_method in ('article', 'title_hash'))
                then 'submitted'
                else 'suspected'
            end as status,
            max(similarity) as top_similarity,
            count(*) as match_count
        from submission_item_duplicate_matches
        where item_id = any(%s::uuid[])
        group by item_id
        """,
        (normalized_ids,),
    )
    return {
        str(row["item_id"]): {
            "status": row["status"],
            "top_similarity": float(row["top_similarity"]),
            "count": int(row["match_count"]),
        }
        for row in cur.fetchall()
    }


def fetch_item_duplicate_match_details(
    cur: psycopg.Cursor,
    item_id: str,
) -> list[dict[str, Any]]:
    cur.execute(
        """
        select
            m.prior_item_id,
            i.title,
            i.body,
            i.source,
            r.report_type,
            r.report_date,
            r.issue_no,
            m.similarity,
            m.match_method
        from submission_item_duplicate_matches m
        join submitted_report_items i on i.id = m.prior_item_id
        join submitted_reports r on r.id = i.report_id
        where m.item_id = %s
        order by
            case m.match_method
                when 'article' then 1
                when 'title_hash' then 2
                else 3
            end,
            m.similarity desc,
            r.report_date desc,
            m.prior_item_id
        """,
        (item_id,),
    )
    return [
        {
            "prior_item_id": str(row["prior_item_id"]),
            "title": row["title"],
            "body": row["body"],
            "source": row["source"],
            "report_type": row["report_type"],
            "report_date": row["report_date"],
            "issue_no": row["issue_no"],
            "similarity": float(row["similarity"]),
            "match_method": row["match_method"],
        }
        for row in cur.fetchall()
    ]


def fetch_archive_embeddings(
    cur: psycopg.Cursor,
    *,
    lookback_days: int,
) -> list[dict[str, Any]]:
    cur.execute(
        """
        select
            i.id as item_id,
            i.article_id as linked_article_id,
            i.embedding,
            i.embedding_model,
            i.title,
            r.report_date,
            r.report_type
        from submitted_report_items i
        join submitted_reports r on r.id = i.report_id
        where r.report_date >= current_date - (%s * interval '1 day')
          and i.embedding is not null
        order by r.report_date, i.order_index
        """,
        (lookback_days,),
    )
    return [dict(row) for row in cur.fetchall()]


def fetch_news_for_submission_dedup(
    cur: psycopg.Cursor,
    *,
    limit: Optional[int],
) -> list[dict[str, Any]]:
    params: list[Any] = []
    limit_sql = ""
    if limit is not None:
        limit_sql = "limit %s"
        params.append(max(1, limit))
    cur.execute(
        f"""
        select article_id, coalesce(title, '') as title,
               coalesce(llm_summary, '') as body,
               dedup_embedding,
               dedup_embedding_model,
               dedup_source_hash,
               dedup_embedded_at
        from news_summaries
        where status = 'ready_for_export'
          and created_at >= (
              date_trunc('day', now() at time zone 'Asia/Shanghai')
              at time zone 'Asia/Shanghai'
          )
        order by created_at, article_id
        {limit_sql}
        """,
        tuple(params),
    )
    return [dict(row) for row in cur.fetchall()]


def upsert_duplicate_matches(
    cur: psycopg.Cursor,
    matches: Sequence[Mapping[str, Any]],
) -> int:
    updated = 0
    for match in matches:
        cur.execute(
            """
            insert into submission_duplicate_matches (
                article_id,
                item_id,
                similarity,
                match_method,
                state
            )
            values (%s, %s, %s, %s, %s)
            on conflict (article_id, item_id) do update
            set similarity = excluded.similarity,
                detected_at = now(),
                updated_at = now(),
                match_method = case
                    when submission_duplicate_matches.state = 'suspected'
                    then excluded.match_method
                    else submission_duplicate_matches.match_method
                end,
                state = case
                    when submission_duplicate_matches.state = 'suspected'
                    then excluded.state
                    else submission_duplicate_matches.state
                end
            """,
            (
                match.get("article_id"),
                match.get("item_id"),
                match.get("similarity"),
                match.get("match_method"),
                match.get("state"),
            ),
        )
        updated += 1
    return updated


def fetch_duplicate_badges(
    cur: psycopg.Cursor,
    article_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    normalized_ids = [article_id for article_id in article_ids if article_id]
    if not normalized_ids:
        return {}
    cur.execute(
        """
        select
            m.article_id,
            bool_or(m.state = 'confirmed') as has_confirmed,
            bool_or(m.state = 'suspected') as has_suspected,
            max(m.similarity) as top_similarity
        from submission_duplicate_matches m
        where m.article_id = any(%s)
          and m.state <> 'dismissed'
        group by m.article_id
        """,
        (normalized_ids,),
    )
    badges = {
        str(row["article_id"]): {
            "has_confirmed": bool(row["has_confirmed"]),
            "has_suspected": bool(row["has_suspected"]),
            "top_similarity": float(row["top_similarity"]),
            "matches": [],
        }
        for row in cur.fetchall()
    }
    if not badges:
        return {}
    cur.execute(
        """
        select distinct on (
            m.article_id,
            coalesce(i.article_id, i.norm_title_hash)
        )
            m.article_id,
            m.state,
            m.similarity,
            i.title,
            i.article_id as linked_article_id,
            i.norm_title_hash,
            r.report_date,
            r.report_type,
            count(*) over (
                partition by
                    m.article_id,
                    coalesce(i.article_id, i.norm_title_hash)
            ) as grouped_count
        from submission_duplicate_matches m
        join submitted_report_items i on i.id = m.item_id
        join submitted_reports r on r.id = i.report_id
        where m.article_id = any(%s)
          and m.state <> 'dismissed'
        order by
            m.article_id,
            coalesce(i.article_id, i.norm_title_hash),
            r.report_date asc,
            m.similarity desc
        """,
        (normalized_ids,),
    )
    for row in cur.fetchall():
        article_id = str(row["article_id"])
        if article_id not in badges:
            continue
        badges[article_id]["matches"].append(
            {
                "state": row["state"],
                "similarity": float(row["similarity"]),
                "title": row["title"],
                "report_date": row["report_date"],
                "report_type": row["report_type"],
                "extra_count": max(int(row["grouped_count"]) - 1, 0),
            }
        )
    for badge in badges.values():
        badge["matches"].sort(
            key=lambda match: match["report_date"],
        )
    return badges


def fetch_duplicate_match_details(
    cur: psycopg.Cursor,
    article_id: str,
) -> list[dict[str, Any]]:
    cur.execute(
        """
        select
            m.item_id,
            m.state,
            m.similarity,
            i.title,
            i.body,
            i.source,
            r.report_date,
            r.report_type
        from submission_duplicate_matches m
        join submitted_report_items i on i.id = m.item_id
        join submitted_reports r on r.id = i.report_id
        where m.article_id = %s
          and m.state <> 'dismissed'
        order by r.report_date desc, m.similarity desc
        """,
        (article_id,),
    )
    return [
        {
            "item_id": str(row["item_id"]),
            "state": row["state"],
            "similarity": float(row["similarity"]),
            "title": row["title"],
            "body": row["body"],
            "source": row["source"],
            "report_date": row["report_date"],
            "report_type": row["report_type"],
        }
        for row in cur.fetchall()
    ]


def dismiss_duplicate_matches(
    cur: psycopg.Cursor,
    *,
    article_id: str,
    actor_user_id: str,
) -> int:
    cur.execute(
        """
        update submission_duplicate_matches
        set state = 'dismissed',
            decided_by = %s,
            decided_at = now(),
            updated_at = now()
        where article_id = %s and state = 'suspected'
        """,
        (actor_user_id, article_id),
    )
    return cur.rowcount


__all__ = [
    "PRIOR_MATCH_REPORT_TYPES",
    "ItemFieldUpdateResult",
    "ManualLinkMutationResult",
    "SubmissionArchiveNamespace",
    "decide_link",
    "delete_report",
    "dismiss_duplicate_matches",
    "fetch_archive_embeddings",
    "fetch_duplicate_badges",
    "fetch_duplicate_match_details",
    "fetch_item_duplicate_match_details",
    "fetch_item_duplicate_match_summaries",
    "fetch_item_match_inputs",
    "fetch_items_missing_embeddings",
    "fetch_link_candidate_bodies",
    "fetch_link_candidate_titles",
    "fetch_manual_link_candidates",
    "fetch_news_for_submission_dedup",
    "fetch_pending_links",
    "fetch_prior_submission_candidates",
    "fetch_report",
    "fetch_report_by_source_message",
    "fetch_report_ids_by_type",
    "fetch_reports",
    "find_report_conflict",
    "insert_report",
    "insert_report_items",
    "manual_link_item",
    "manual_unlink_item",
    "mark_prior_match_completed",
    "replace_item_duplicate_matches",
    "search_items",
    "update_item_embeddings",
    "update_item_fields",
    "update_link_results",
    "upsert_duplicate_matches",
]
