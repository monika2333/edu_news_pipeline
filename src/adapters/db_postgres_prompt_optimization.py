from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence

import psycopg
from psycopg.types.json import Json

from src.adapters.db_postgres_prompt_revisions import append_prompt_revision
from src.domain.report_type import SUBMISSION_DOC_TYPES

if TYPE_CHECKING:
    from src.adapters.db_postgres_core import PostgresAdapter

OUTCOME_NO_CORRECTION = "no_correction"
OUTCOME_MODEL_DECLINED = "model_declined"
OUTCOME_CHECK_FAILED = "check_failed"
OUTCOME_REVISION_CREATED = "revision_created"
OUTCOME_ERROR = "error"
PROMPT_OPTIMIZATION_OUTCOMES = frozenset(
    {
        OUTCOME_NO_CORRECTION,
        OUTCOME_MODEL_DECLINED,
        OUTCOME_CHECK_FAILED,
        OUTCOME_REVISION_CREATED,
        OUTCOME_ERROR,
    }
)

_RUN_COLUMNS = """
    r.id,
    r.prompt_key,
    r.outcome,
    r.started_at,
    r.finished_at,
    r.window_start,
    r.baseline_revision_id,
    r.new_revision_id,
    r.model,
    r.cases,
    r.attempts,
    r.error
"""


class PromptOptimizationNamespace:
    """Read-only case collection plus append-only optimizer run records."""

    def __init__(self, adapter: PostgresAdapter) -> None:
        self._adapter = adapter

    def collect_cases(
        self,
        prompt_key: str,
        *,
        since: datetime,
        limit: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        with self._adapter._cursor() as cur:
            feedback = collect_feedback_cases(
                cur,
                prompt_key,
                since=since,
                limit=limit,
            )
            submissions = collect_submission_cases(
                cur,
                prompt_key,
                since=since,
                limit=limit,
            )
            return feedback, submissions

    def record_run(
        self,
        *,
        prompt_key: str,
        outcome: str,
        started_at: datetime,
        finished_at: datetime,
        window_start: datetime,
        baseline_revision_id: Optional[int],
        new_revision_id: Optional[int],
        model: str,
        cases: Sequence[Mapping[str, Any]],
        attempts: Sequence[Mapping[str, Any]],
        error: Optional[str],
    ) -> dict[str, Any]:
        with self._adapter.transaction() as cur:
            return insert_prompt_optimization_run(
                cur,
                prompt_key=prompt_key,
                outcome=outcome,
                started_at=started_at,
                finished_at=finished_at,
                window_start=window_start,
                baseline_revision_id=baseline_revision_id,
                new_revision_id=new_revision_id,
                model=model,
                cases=cases,
                attempts=attempts,
                error=error,
            )

    def record_run_with_revision(
        self,
        *,
        prompt_key: str,
        content: str,
        note: Optional[str],
        expected_revision_id: Optional[int],
        baseline: Optional[Mapping[str, Any]],
        baseline_revision_id: Optional[int],
        started_at: datetime,
        finished_at: datetime,
        window_start: datetime,
        model: str,
        cases: Sequence[Mapping[str, Any]],
        attempts: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Append one system-authored revision and its run record atomically.

        Using ``append_prompt_revision`` keeps the same advisory lock and
        optimistic token as a manual save. If someone saved while the optimizer
        was thinking, the stale token raises PromptRevisionConflictError and the
        whole transaction (including the run record) rolls back; the caller
        records the failure separately.
        """

        with self._adapter.transaction() as cur:
            revision = append_prompt_revision(
                cur,
                prompt_key=prompt_key,
                content=content,
                note=note,
                expected_revision_id=expected_revision_id,
                baseline=baseline,
                actor_user_id=None,
            )
            resolved_baseline_id = baseline_revision_id
            if baseline is not None:
                cur.execute(
                    """
                    SELECT id
                    FROM prompt_revisions
                    WHERE prompt_key = %s AND revision = %s
                    """,
                    (prompt_key, int(baseline["revision"])),
                )
                baseline_row = cur.fetchone()
                if baseline_row is not None:
                    resolved_baseline_id = int(baseline_row["id"])
            run = insert_prompt_optimization_run(
                cur,
                prompt_key=prompt_key,
                outcome=OUTCOME_REVISION_CREATED,
                started_at=started_at,
                finished_at=finished_at,
                window_start=window_start,
                baseline_revision_id=resolved_baseline_id,
                new_revision_id=int(revision["id"]),
                model=model,
                cases=cases,
                attempts=attempts,
                error=None,
            )
            return {"run": run, "revision": revision}

    def fetch_latest_runs(
        self,
        prompt_keys: Sequence[str],
    ) -> dict[str, dict[str, Any]]:
        with self._adapter._cursor() as cur:
            try:
                return fetch_latest_prompt_optimization_runs(cur, prompt_keys)
            except psycopg.errors.UndefinedTable:
                # 代码先于迁移上线时，提示词页签仍应可用，只是没有状态行。
                return {}

    def fetch_runs_for_revision_ids(
        self,
        revision_ids: Sequence[int],
    ) -> dict[int, dict[str, Any]]:
        ids = [int(item) for item in revision_ids]
        if not ids:
            return {}
        with self._adapter._cursor() as cur:
            try:
                return fetch_prompt_optimization_runs_for_revisions(cur, ids)
            except psycopg.errors.UndefinedTable:
                return {}

    def fetch_run_by_new_revision_id(
        self,
        revision_id: int,
    ) -> Optional[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            try:
                return fetch_prompt_optimization_run_by_revision(cur, revision_id)
            except psycopg.errors.UndefinedTable:
                return None


def collect_feedback_cases(
    cur: psycopg.Cursor,
    prompt_key: str,
    *,
    since: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    """Latest feedback per article in the lookback window for one prompt key.

    ``updated_at`` is the window field, not ``created_at``: editing a feedback
    (including flipping its direction) must bring it back into scope.
    """

    cur.execute(
        """
        SELECT *
        FROM (
            SELECT DISTINCT ON (sf.article_id)
                sf.article_id,
                sf.feedback_type,
                sf.score_value::float8 AS original_score,
                sf.notes AS reason,
                sf.updated_at AS event_at,
                sf.prompt_key,
                ns.title,
                ns.source,
                ns.llm_summary AS summary,
                ns.external_importance_score::float8 AS stored_score
            FROM score_feedbacks sf
            JOIN news_summaries ns ON ns.article_id = sf.article_id
            WHERE sf.prompt_key = %s
              AND sf.updated_at >= %s
            ORDER BY sf.article_id, sf.updated_at DESC, sf.id DESC
        ) latest
        ORDER BY event_at DESC, article_id
        LIMIT %s
        """,
        (prompt_key, since, limit),
    )
    return [dict(row) for row in cur.fetchall()]


def collect_submission_cases(
    cur: psycopg.Cursor,
    prompt_key: str,
    *,
    since: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    """Matched archive items in the lookback window for one prompt key.

    Only ``matched`` with a non-null ``article_id`` counts; ``pending`` has not
    been confirmed and is not a positive example. Attribution is by the prompt
    key actually used for that article's score.
    """

    cur.execute(
        """
        SELECT
            sri.article_id,
            max(sri.link_matched_at) AS event_at,
            array_agg(DISTINCT sr.report_type) AS report_types,
            ns.title,
            ns.source,
            ns.llm_summary AS summary,
            ns.external_importance_score::float8 AS stored_score
        FROM submitted_report_items sri
        JOIN submitted_reports sr ON sr.id = sri.report_id
        JOIN news_summaries ns ON ns.article_id = sri.article_id
        WHERE sri.link_status = 'matched'
          AND sri.article_id IS NOT NULL
          AND sri.link_matched_at >= %s
          AND sr.report_type = ANY(%s)
          AND ns.external_importance_raw ->> 'prompt_key' = %s
          AND NOT EXISTS (
              SELECT 1
              FROM score_feedbacks sf
              WHERE sf.article_id = sri.article_id
                AND sf.prompt_key = %s
                AND sf.updated_at >= %s
          )
        GROUP BY
            sri.article_id,
            ns.title,
            ns.source,
            ns.llm_summary,
            ns.external_importance_score
        ORDER BY event_at DESC, sri.article_id
        LIMIT %s
        """,
        (
            since,
            sorted(SUBMISSION_DOC_TYPES),
            prompt_key,
            prompt_key,
            since,
            limit,
        ),
    )
    return [dict(row) for row in cur.fetchall()]


def insert_prompt_optimization_run(
    cur: psycopg.Cursor,
    *,
    prompt_key: str,
    outcome: str,
    started_at: datetime,
    finished_at: datetime,
    window_start: datetime,
    baseline_revision_id: Optional[int],
    new_revision_id: Optional[int],
    model: str,
    cases: Sequence[Mapping[str, Any]],
    attempts: Sequence[Mapping[str, Any]],
    error: Optional[str],
) -> dict[str, Any]:
    if outcome not in PROMPT_OPTIMIZATION_OUTCOMES:
        raise ValueError(f"未知优化结果：{outcome}")
    cur.execute(
        """
        INSERT INTO prompt_optimization_runs (
            prompt_key,
            outcome,
            started_at,
            finished_at,
            window_start,
            baseline_revision_id,
            new_revision_id,
            model,
            cases,
            attempts,
            error
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            prompt_key,
            outcome,
            started_at,
            finished_at,
            window_start,
            baseline_revision_id,
            new_revision_id,
            model,
            Json(list(cases)),
            Json(list(attempts)),
            error,
        ),
    )
    return dict(cur.fetchone())


def fetch_latest_prompt_optimization_runs(
    cur: psycopg.Cursor,
    prompt_keys: Sequence[str],
) -> dict[str, dict[str, Any]]:
    keys = [str(key) for key in prompt_keys]
    if not keys:
        return {}
    cur.execute(
        f"""
        SELECT DISTINCT ON (r.prompt_key)
            {_RUN_COLUMNS},
            p.revision AS new_revision
        FROM prompt_optimization_runs r
        LEFT JOIN prompt_revisions p ON p.id = r.new_revision_id
        WHERE r.prompt_key = ANY(%s)
        ORDER BY r.prompt_key, r.finished_at DESC, r.id DESC
        """,
        (keys,),
    )
    return {str(row["prompt_key"]): dict(row) for row in cur.fetchall()}


def fetch_prompt_optimization_runs_for_revisions(
    cur: psycopg.Cursor,
    revision_ids: Sequence[int],
) -> dict[int, dict[str, Any]]:
    ids = [int(item) for item in revision_ids]
    if not ids:
        return {}
    cur.execute(
        f"""
        SELECT {_RUN_COLUMNS}
        FROM prompt_optimization_runs r
        WHERE r.new_revision_id = ANY(%s)
        """,
        (ids,),
    )
    return {
        int(row["new_revision_id"]): dict(row)
        for row in cur.fetchall()
    }


def fetch_prompt_optimization_run_by_revision(
    cur: psycopg.Cursor,
    revision_id: int,
) -> Optional[dict[str, Any]]:
    cur.execute(
        f"""
        SELECT {_RUN_COLUMNS}
        FROM prompt_optimization_runs r
        WHERE r.new_revision_id = %s
        """,
        (int(revision_id),),
    )
    row = cur.fetchone()
    return dict(row) if row else None


__all__ = [
    "OUTCOME_CHECK_FAILED",
    "OUTCOME_ERROR",
    "OUTCOME_MODEL_DECLINED",
    "OUTCOME_NO_CORRECTION",
    "OUTCOME_REVISION_CREATED",
    "PROMPT_OPTIMIZATION_OUTCOMES",
    "PromptOptimizationNamespace",
    "collect_feedback_cases",
    "collect_submission_cases",
    "fetch_latest_prompt_optimization_runs",
    "fetch_prompt_optimization_run_by_revision",
    "fetch_prompt_optimization_runs_for_revisions",
    "insert_prompt_optimization_run",
]
