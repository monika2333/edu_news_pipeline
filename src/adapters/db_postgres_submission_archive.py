from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence

import psycopg

if TYPE_CHECKING:
    from src.adapters.db_postgres_core import PostgresAdapter


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
            created["items"] = insert_report_items(
                cur,
                report_id=str(created["id"]),
                items=items,
            )
            created["item_count"] = len(created["items"])
            return created

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

    def delete_report(self, report_id: str) -> bool:
        with self._adapter.transaction() as cur:
            return delete_report(cur, report_id)

    def replace_report_items(
        self,
        *,
        report_id: str,
        items: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        with self._adapter.transaction() as cur:
            return replace_report_items(cur, report_id=report_id, items=items)

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

    def fetch_items_missing_embeddings(self, *, limit: int) -> list[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_items_missing_embeddings(cur, limit=limit)

    def update_item_embeddings(
        self,
        embeddings: Sequence[Mapping[str, Any]],
    ) -> int:
        with self._adapter.transaction() as cur:
            return update_item_embeddings(cur, embeddings)

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
) -> dict[str, Any]:
    cur.execute(
        """
        insert into submitted_reports (
            report_type,
            report_date,
            compiled_date,
            issue_no,
            title_line,
            pasted_text
        )
        values (%s, %s, %s, %s, %s, %s)
        returning *
        """,
        (
            report_type,
            report_date,
            compiled_date,
            issue_no,
            title_line,
            pasted_text,
        ),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("Failed to create submitted report")
    return dict(row)


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


def replace_report_items(
    cur: psycopg.Cursor,
    *,
    report_id: str,
    items: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    cur.execute(
        "delete from submitted_report_items where report_id = %s",
        (report_id,),
    )
    return insert_report_items(cur, report_id=report_id, items=items)


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
            count(i.id) filter (where i.link_status = 'exact') as exact_count,
            count(i.id) filter (where i.link_status = 'fuzzy') as fuzzy_count,
            count(i.id) filter (where i.link_status = 'manual') as manual_count,
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
    return report


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
                    when %s in ('exact', 'fuzzy') then now()
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
            link_status = case when %s then 'manual' else 'rejected' end,
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
        where i.title ilike %s or i.body ilike %s
        order by r.report_date desc, i.order_index
        limit %s
        """,
        (pattern, pattern, max(1, min(limit, 200))),
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
            r.report_date,
            r.report_type
        from submission_duplicate_matches m
        join submitted_report_items i on i.id = m.item_id
        join submitted_reports r on r.id = i.report_id
        where m.article_id = %s
          and m.state <> 'dismissed'
        order by r.report_date asc, m.similarity desc
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
    "SubmissionArchiveNamespace",
    "decide_link",
    "delete_report",
    "dismiss_duplicate_matches",
    "fetch_archive_embeddings",
    "fetch_duplicate_badges",
    "fetch_duplicate_match_details",
    "fetch_items_missing_embeddings",
    "fetch_link_candidate_bodies",
    "fetch_link_candidate_titles",
    "fetch_news_for_submission_dedup",
    "fetch_pending_links",
    "fetch_report",
    "fetch_reports",
    "find_report_conflict",
    "insert_report",
    "insert_report_items",
    "replace_report_items",
    "search_items",
    "update_item_embeddings",
    "update_link_results",
    "upsert_duplicate_matches",
]
