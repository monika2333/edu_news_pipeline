from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping, Optional, Sequence

import psycopg

from src.domain.report_type import NEWS_REPORT_TYPES


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
    compiled_date: date,
    lookback_days: int,
) -> list[dict[str, Any]]:
    window_start = compiled_date - timedelta(days=max(1, lookback_days))
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
          and r.compiled_date >= %s
          and r.compiled_date <= %s
        order by r.compiled_date desc, i.order_index, i.id
        """,
        (
            sorted(NEWS_REPORT_TYPES),
            window_start,
            compiled_date,
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
            m.item_id,
            case
                when bool_or(m.match_method in ('article', 'title_hash'))
                then 'submitted'
                when i.prior_match_decision = 'submitted'
                then 'submitted'
                when i.prior_match_decision = 'not_submitted'
                then 'dismissed'
                else 'suspected'
            end as status,
            not bool_or(
                m.match_method in ('article', 'title_hash')
            ) as decidable,
            case
                when bool_or(m.match_method in ('article', 'title_hash'))
                then null
                else i.prior_match_decision
            end as decision,
            max(m.similarity) as top_similarity,
            count(*) as match_count
        from submission_item_duplicate_matches m
        join submitted_report_items i on i.id = m.item_id
        where m.item_id = any(%s::uuid[])
        group by m.item_id, i.prior_match_decision
        """,
        (normalized_ids,),
    )
    return {
        str(row["item_id"]): {
            "status": row["status"],
            "decidable": bool(row["decidable"]),
            "decision": row["decision"],
            "top_similarity": float(row["top_similarity"]),
            "count": int(row["match_count"]),
        }
        for row in cur.fetchall()
    }


def set_item_prior_match_decision(
    cur: psycopg.Cursor,
    *,
    item_id: str,
    decision: Optional[str],
    actor_user_id: str,
) -> PriorMatchDecisionMutationResult:
    """Persist and re-derive one feedback item's human prior-match decision."""
    cur.execute(
        """
        select id
        from submitted_report_items
        where id = %s
        for update
        """,
        (item_id,),
    )
    if not cur.fetchone():
        return {"state": "not_found", "prior_match": None}

    current = fetch_item_duplicate_match_summaries(cur, [item_id]).get(item_id)
    if not current or not current["decidable"]:
        return {"state": "not_decidable", "prior_match": current}

    cur.execute(
        """
        update submitted_report_items
        set prior_match_decision = %s,
            prior_match_decided_by = case
                when %s::text is null then null
                else %s::uuid
            end,
            prior_match_decided_at = case
                when %s::text is null then null
                else now()
            end,
            updated_at = now()
        where id = %s
        """,
        (decision, decision, actor_user_id, decision, item_id),
    )
    refreshed = fetch_item_duplicate_match_summaries(cur, [item_id]).get(item_id)
    if not refreshed:
        raise RuntimeError("Prior-match summary disappeared during update")
    return {"state": "updated", "prior_match": refreshed}


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
    "dismiss_duplicate_matches",
    "fetch_archive_embeddings",
    "fetch_duplicate_badges",
    "fetch_duplicate_match_details",
    "fetch_item_duplicate_match_details",
    "fetch_item_duplicate_match_summaries",
    "fetch_item_match_inputs",
    "fetch_news_for_submission_dedup",
    "fetch_prior_submission_candidates",
    "replace_item_duplicate_matches",
    "set_item_prior_match_decision",
    "upsert_duplicate_matches",
]
