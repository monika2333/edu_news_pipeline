from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import psycopg
from psycopg.types.json import Json

from src.adapters.db_postgres_shared import iso_datetime
from src.domain import BeijingGateCandidate, ExternalFilterCandidate, PrimaryArticleForScoring


def fetch_beijing_gate_candidates(
    cur: psycopg.Cursor,
    limit: int,
    *,
    max_failures: Optional[int] = None,
) -> List[BeijingGateCandidate]:
    if limit <= 0:
        return []
    clauses = [
        "status = 'pending_beijing_gate'",
        "summary_status = 'completed'",
    ]
    params: List[Any] = []
    if max_failures is not None:
        clauses.append("beijing_gate_fail_count < %s")
        params.append(max_failures)
    where_sql = " AND ".join(clauses)
    query = f"""
        SELECT
            article_id,
            title,
            source,
            publish_time_iso,
            llm_summary,
            content_markdown,
            sentiment_label,
            is_beijing_related,
            is_beijing_related_llm,
            external_importance_status,
            beijing_gate_fail_count,
            beijing_gate_attempted_at
        FROM news_summaries
        WHERE {where_sql}
        ORDER BY beijing_gate_attempted_at ASC NULLS FIRST,
                 summary_generated_at ASC NULLS LAST,
                 article_id ASC
        LIMIT %s
    """
    params.append(limit)
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    results: List[BeijingGateCandidate] = []
    for row in rows:
        article_id = row.get("article_id")
        if not article_id:
            continue
        results.append(
            BeijingGateCandidate(
                article_id=str(article_id),
                title=row.get("title"),
                source=row.get("source"),
                publish_time_iso=iso_datetime(row.get("publish_time_iso")),
                summary=row.get("llm_summary") or "",
                content=row.get("content_markdown") or "",
                sentiment_label=row.get("sentiment_label"),
                is_beijing_related=row.get("is_beijing_related"),
                is_beijing_related_llm=row.get("is_beijing_related_llm"),
                external_importance_status=row.get("external_importance_status") or "pending",
                beijing_gate_fail_count=int(row.get("beijing_gate_fail_count") or 0),
                beijing_gate_attempted_at=iso_datetime(row.get("beijing_gate_attempted_at")),
            )
        )
    return results


def fetch_external_filter_candidates(
    cur: psycopg.Cursor,
    limit: int,
    *,
    max_failures: Optional[int] = None,
) -> List[ExternalFilterCandidate]:
    if limit <= 0:
        return []
    clauses = [
        "status = 'pending_external_filter'",
        "external_importance_status = 'pending_external_filter'",
        "summary_status = 'completed'",
    ]
    params: List[Any] = []
    if max_failures is not None:
        clauses.append("external_filter_fail_count < %s")
        params.append(max_failures)
    where_sql = " AND ".join(clauses)
    query = f"""
        SELECT
            article_id,
            title,
            source,
            publish_time_iso,
            llm_summary,
            content_markdown,
            sentiment_label,
            is_beijing_related,
            is_beijing_related_llm,
            external_importance_status,
            external_filter_fail_count,
            score_details
        FROM news_summaries
        WHERE {where_sql}
        ORDER BY external_filter_attempted_at ASC NULLS FIRST,
                 summary_generated_at ASC NULLS LAST,
                 article_id ASC
        LIMIT %s
    """
    params.append(limit)
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    results: List[ExternalFilterCandidate] = []
    for row in rows:
        article_id = row.get("article_id")
        if not article_id:
            continue
        score_details = row.get("score_details") or {}
        if isinstance(score_details, list):
            score_details = {}
        matched_rules = score_details.get("matched_rules") if isinstance(score_details, dict) else None
        keyword_matches = []
        if isinstance(matched_rules, list):
            for rule in matched_rules:
                if not isinstance(rule, dict):
                    continue
                label = rule.get("label") or rule.get("rule_id")
                if label:
                    keyword_matches.append(str(label))

        results.append(
            ExternalFilterCandidate(
                article_id=str(article_id),
                title=row.get("title"),
                source=row.get("source"),
                publish_time_iso=iso_datetime(row.get("publish_time_iso")),
                summary=row.get("llm_summary") or "",
                content=row.get("content_markdown") or "",
                sentiment_label=row.get("sentiment_label"),
                is_beijing_related=row.get("is_beijing_related"),
                is_beijing_related_llm=row.get("is_beijing_related_llm"),
                external_importance_status=row.get("external_importance_status") or "pending_external_filter",
                external_filter_fail_count=int(row.get("external_filter_fail_count") or 0),
                keyword_matches=tuple(keyword_matches),
            )
        )
    return results


def complete_beijing_gate(
    cur: psycopg.Cursor,
    article_id: str,
    *,
    status: str,
    is_beijing_related: Optional[bool],
    is_beijing_related_llm: Optional[bool],
    raw_output: Optional[Mapping[str, Any]],
    external_importance_status: Optional[str] = None,
    reset_external_filter: bool = False,
    sentiment_label: Optional[str] = None,
    candidate_category: Optional[str] = None,
) -> None:
    if not article_id:
        raise ValueError("complete_beijing_gate requires article_id")
    timestamp = datetime.now(timezone.utc)
    sentiment_value = (sentiment_label or "").strip().lower()
    positive_sentiment = sentiment_value == "positive"
    negative_sentiment = sentiment_value == "negative"
    category = (candidate_category or "").strip().lower() or ("internal" if is_beijing_related else "external")
    route_to_external_filter = bool(is_beijing_related) and (positive_sentiment or negative_sentiment)
    target_status = "pending_external_filter" if route_to_external_filter else status
    target_external_status = "pending_external_filter" if route_to_external_filter else external_importance_status or status
    payload: Dict[str, Any] = {
        "status": target_status,
        "external_importance_status": target_external_status,
        "is_beijing_related": is_beijing_related,
        "is_beijing_related_llm": is_beijing_related_llm,
        "beijing_gate_checked_at": timestamp,
        "beijing_gate_fail_count": 0,
        "beijing_gate_attempted_at": timestamp,
        "external_importance_score": None,
        "external_importance_checked_at": None,
        "external_importance_raw": None,
    }
    if raw_output is not None:
        payload["beijing_gate_raw"] = Json(raw_output)
    else:
        payload["beijing_gate_raw"] = None
    if route_to_external_filter:
        payload["external_importance_raw"] = Json({"category": category or "internal"})
        payload.update(
            {
                "external_filter_fail_count": 0,
                "external_filter_attempted_at": None,
            }
        )
    elif reset_external_filter:
        payload.update(
            {
                "external_filter_fail_count": 0,
                "external_filter_attempted_at": None,
            }
        )
    sets = ", ".join(f"{field} = %s" for field in payload)
    values = list(payload.values()) + [article_id]
    query = f"""
        UPDATE news_summaries
        SET {sets}
        WHERE article_id = %s
    """
    cur.execute(query, values)
    if cur.rowcount != 1:
        raise ValueError(f"Unable to update Beijing gate result for {article_id}")


def mark_beijing_gate_failure(
    cur: psycopg.Cursor,
    article_id: str,
    *,
    fail_count: int,
    error: str,
    final_status: Optional[str] = None,
    external_importance_status: Optional[str] = None,
) -> None:
    if not article_id:
        return
    timestamp = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "beijing_gate_fail_count": fail_count,
        "beijing_gate_attempted_at": timestamp,
        "beijing_gate_raw": Json(
            {
                "error": str(error)[:500],
                "recorded_at": timestamp.isoformat(),
            }
        ),
    }
    if final_status:
        payload["status"] = final_status
        payload["external_importance_status"] = external_importance_status or final_status
    sets = ", ".join(f"{field} = %s" for field in payload)
    values = list(payload.values()) + [article_id]
    query = f"""
        UPDATE news_summaries
        SET {sets}
        WHERE article_id = %s
    """
    cur.execute(query, values)


def complete_external_filter(
    cur: psycopg.Cursor,
    article_id: str,
    *,
    passed: bool,
    score: int,
    raw_output: str,
    category: Optional[str] = None,
) -> datetime:
    if not article_id:
        raise ValueError("complete_external_filter requires article_id")
    target_status = "ready_for_export" if passed else "external_filtered"
    timestamp = datetime.now(timezone.utc)
    payload = {
        "status": target_status,
        "external_importance_status": target_status,
        "external_importance_score": score,
        "external_importance_checked_at": timestamp,
        "external_importance_raw": Json(
            {
                "model_output": raw_output,
                "decided_at": timestamp.isoformat(),
                "category": (category or "").strip().lower() or None,
            }
        ),
        "external_filter_attempted_at": timestamp,
        "external_filter_fail_count": 0,
    }
    sets = ", ".join(f"{field} = %s" for field in payload)
    values = list(payload.values()) + [article_id]
    query = f"""
        UPDATE news_summaries
        SET {sets}
        WHERE article_id = %s
    """
    cur.execute(query, values)
    if cur.rowcount != 1:
        raise ValueError(f"Unable to update external filter status for {article_id}")
    return timestamp


def mark_external_filter_failure(
    cur: psycopg.Cursor,
    article_id: str,
    *,
    fail_count: int,
    final_failure: bool,
    error: str,
) -> None:
    if not article_id:
        return
    timestamp = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "external_filter_fail_count": fail_count,
        "external_filter_attempted_at": timestamp,
        "external_importance_raw": Json(
            {
                "error": str(error)[:500],
                "recorded_at": timestamp.isoformat(),
            }
        ),
    }
    if final_failure:
        payload.update(
            {
                "status": "external_filtered",
                "external_importance_status": "external_filtered",
                "external_importance_checked_at": timestamp,
                "external_importance_score": None,
            }
        )
    sets = ", ".join(f"{field} = %s" for field in payload)
    values = list(payload.values()) + [article_id]
    query = f"""
        UPDATE news_summaries
        SET {sets}
        WHERE article_id = %s
    """
    cur.execute(query, values)


def fetch_external_backfill_candidates(
    cur: psycopg.Cursor,
    limit: int,
    since_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    if limit <= 0:
        return []
    parts: List[str] = [
        "SELECT",
        "    article_id,",
        "    title,",
        "    publish_time_iso,",
        "    summary_generated_at,",
        "    sentiment_label",
        "FROM news_summaries",
        "WHERE status = 'ready_for_export'",
        "  AND summary_status = 'completed'",
        "  AND (is_beijing_related IS DISTINCT FROM TRUE)",
        "  AND lower(coalesce(sentiment_label, '')) = 'positive'",
        "  AND (external_importance_status IS NULL OR external_importance_status NOT IN ('pending_external_filter'))",
    ]
    params: List[Any] = []
    if since_date is not None:
        parts.append("  AND publish_time_iso::date >= %s")
        params.append(since_date)
    parts.extend(
        [
            "ORDER BY summary_generated_at ASC NULLS LAST, article_id ASC",
            "LIMIT %s",
        ]
    )
    params.append(limit)
    query = "\n".join(parts)
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    return [dict(row) for row in rows]


def reset_external_filter_pending(cur: psycopg.Cursor, article_ids: Sequence[str]) -> int:
    if not article_ids:
        return 0
    query = """
        UPDATE news_summaries
        SET status = 'pending_external_filter',
            external_importance_status = 'pending_external_filter',
            external_importance_score = NULL,
            external_importance_checked_at = NULL,
            external_importance_raw = NULL,
            external_filter_attempted_at = NULL,
            external_filter_fail_count = 0,
            updated_at = NOW()
        WHERE article_id = ANY(%s)
    """
    cur.execute(query, (list(article_ids),))
    return cur.rowcount


def fetch_primary_articles_for_scoring(cur: psycopg.Cursor, limit: int) -> List[PrimaryArticleForScoring]:
    query = """
        SELECT
            article_id,
            primary_article_id,
            status,
            score,
            raw_relevance_score,
            keyword_bonus_score,
            score_details,
            title,
            source,
            publish_time,
            publish_time_iso,
            url,
            content_markdown,
            keywords,
            content_hash,
            simhash,
            created_at
        FROM primary_articles
        WHERE status IN ('pending', 'failed')
           OR score IS NULL
        ORDER BY created_at ASC
        LIMIT %s
    """
    cur.execute(query, (max(1, limit),))
    rows = cur.fetchall()
    results: List[PrimaryArticleForScoring] = []
    for row in rows:
        article_id = row.get("article_id")
        content = row.get("content_markdown")
        if not article_id or content is None:
            continue
        keywords = row.get("keywords") or []
        if keywords is None:
            keywords = []
        score_details = row.get("score_details") or {}
        if isinstance(score_details, list):
            score_details = {}
        results.append(
            PrimaryArticleForScoring(
                article_id=str(article_id),
                content=str(content),
                title=row.get("title"),
                source=row.get("source"),
                publish_time=row.get("publish_time"),
                publish_time_iso=row.get("publish_time_iso"),
                url=row.get("url"),
                keywords=list(keywords),
                content_hash=row.get("content_hash"),
                simhash=row.get("simhash"),
                raw_relevance_score=row.get("raw_relevance_score"),
                keyword_bonus_score=row.get("keyword_bonus_score"),
                score_details=score_details,
            )
        )
    return results


def update_primary_article_scores(cur: psycopg.Cursor, updates: Sequence[Mapping[str, Any]]) -> int:
    if not updates:
        return 0
    prepared: List[Tuple[Any, ...]] = []
    for row in updates:
        article_id = str(row.get("article_id") or "").strip()
        if not article_id:
            continue
        score_details = row.get("score_details")
        if score_details is None:
            score_details = {}
        prepared.append(
            (
                row.get("score"),
                row.get("raw_relevance_score"),
                row.get("keyword_bonus_score"),
                Json(score_details),
                row.get("status") or "pending",
                article_id,
            )
        )
    if not prepared:
        return 0
    query = """
        UPDATE primary_articles
        SET
            score = %s,
            raw_relevance_score = %s,
            keyword_bonus_score = %s,
            score_details = %s,
            status = %s,
            score_updated_at = NOW(),
            updated_at = NOW()
        WHERE article_id = %s
    """
    cur.executemany(query, prepared)
    return len(prepared)


def fetch_beijing_tag_candidates(cur: psycopg.Cursor, limit: int) -> List[Dict[str, Any]]:
    query = """
        SELECT
            article_id,
            content_markdown,
            llm_summary,
            llm_keywords
        FROM news_summaries
        WHERE is_beijing_related IS NULL
        ORDER BY summary_generated_at ASC NULLS LAST
        LIMIT %s
    """
    cur.execute(query, (max(1, limit),))
    rows = cur.fetchall()
    results: List[Dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        article_id = record.get("article_id")
        if not article_id:
            continue
        results.append(record)
    return results


def update_beijing_related_bulk(cur: psycopg.Cursor, updates: Sequence[Tuple[str, bool]]) -> int:
    if not updates:
        return 0
    payload = []
    for article_id, value in updates:
        if not article_id:
            continue
        payload.append((value, str(article_id)))
    if not payload:
        return 0
    cur.executemany(
        "UPDATE news_summaries SET is_beijing_related = %s, updated_at = NOW() WHERE article_id = %s",
        payload,
    )
    return len(payload)


def record_pipeline_run_start(
    cur: psycopg.Cursor,
    *,
    run_id: str,
    started_at: datetime,
    plan: Sequence[str],
    trigger_source: Optional[str] = None,
) -> None:
    payload = {
        "run_id": run_id,
        "status": "running",
        "trigger_source": trigger_source,
        "plan": plan,
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "steps_completed": 0,
        "artifacts": None,
        "error_summary": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    columns = list(payload.keys())
    values = [Json(v) if isinstance(v, (list, dict)) else v for v in payload.values()]
    updates = [
        "status = EXCLUDED.status",
        "trigger_source = EXCLUDED.trigger_source",
        "plan = EXCLUDED.plan",
        "started_at = EXCLUDED.started_at",
        "finished_at = EXCLUDED.finished_at",
        "steps_completed = EXCLUDED.steps_completed",
        "artifacts = EXCLUDED.artifacts",
        "error_summary = EXCLUDED.error_summary",
        "updated_at = EXCLUDED.updated_at",
    ]
    query = f"""
        INSERT INTO pipeline_runs ({', '.join(columns)})
        VALUES ({', '.join(['%s'] * len(columns))})
        ON CONFLICT (run_id) DO UPDATE SET {', '.join(updates)}
    """
    cur.execute(query, values)


def record_pipeline_run_step(
    cur: psycopg.Cursor,
    *,
    run_id: str,
    order_index: int,
    step_name: str,
    status: str,
    started_at: datetime,
    finished_at: datetime,
    duration_seconds: Optional[float],
    error: Optional[str],
) -> None:
    cur.execute(
        """
        INSERT INTO pipeline_run_steps (
            run_id,
            order_index,
            step_name,
            status,
            started_at,
            finished_at,
            duration_seconds,
            error
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            run_id,
            order_index,
            step_name,
            status,
            started_at.isoformat(),
            finished_at.isoformat(),
            duration_seconds,
            error,
        ),
    )
    cur.execute(
        """
        UPDATE pipeline_runs
        SET steps_completed = %s,
            updated_at = NOW()
        WHERE run_id = %s
        """,
        (order_index, run_id),
    )


def finalize_pipeline_run(
    cur: psycopg.Cursor,
    *,
    run_id: str,
    status: str,
    finished_at: datetime,
    steps_completed: int,
    artifacts: Optional[Mapping[str, str]] = None,
    error_summary: Optional[str] = None,
) -> None:
    cur.execute(
        """
        UPDATE pipeline_runs
        SET status = %s,
            finished_at = %s,
            steps_completed = %s,
            artifacts = %s,
            error_summary = %s,
            updated_at = NOW()
        WHERE run_id = %s
        """,
        (
            status,
            finished_at.isoformat(),
            steps_completed,
            (Json(dict(artifacts)) if artifacts else None),
            error_summary,
            run_id,
        ),
    )


def fetch_pipeline_runs(cur: psycopg.Cursor, limit: int = 20) -> List[Dict[str, Any]]:
    query = """
        SELECT *
        FROM pipeline_runs
        ORDER BY started_at DESC
        LIMIT %s
    """
    cur.execute(query, (limit,))
    rows = cur.fetchall()
    return [dict(row) for row in rows]


def fetch_pipeline_run(cur: psycopg.Cursor, run_id: str) -> Optional[Dict[str, Any]]:
    query = "SELECT * FROM pipeline_runs WHERE run_id = %s LIMIT 1"
    cur.execute(query, (run_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def fetch_pipeline_run_steps(cur: psycopg.Cursor, run_id: str) -> List[Dict[str, Any]]:
    query = """
        SELECT *
        FROM pipeline_run_steps
        WHERE run_id = %s
        ORDER BY order_index ASC
    """
    cur.execute(query, (run_id,))
    rows = cur.fetchall()
    return [dict(row) for row in rows]


__all__ = [
    "complete_beijing_gate",
    "complete_external_filter",
    "fetch_beijing_gate_candidates",
    "fetch_beijing_tag_candidates",
    "fetch_external_backfill_candidates",
    "fetch_external_filter_candidates",
    "fetch_pipeline_run",
    "fetch_pipeline_run_steps",
    "fetch_pipeline_runs",
    "fetch_primary_articles_for_scoring",
    "finalize_pipeline_run",
    "mark_beijing_gate_failure",
    "mark_external_filter_failure",
    "record_pipeline_run_start",
    "record_pipeline_run_step",
    "reset_external_filter_pending",
    "update_beijing_related_bulk",
    "update_primary_article_scores",
]
