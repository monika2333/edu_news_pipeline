from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Json

from src.adapters.db_postgres_core import PostgresAdapter
from src.adapters.llm_prompt_optimizer import (
    DECISION_CHANGE,
    PromptOptimizerDecision,
)
from src.config import get_settings
from src.domain.prompt import PromptTemplate
from src.domain.prompt_edit import PromptEdit
from src.workers import prompt_optimizer as worker

NOW = datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture()
def conn() -> Iterator[psycopg.Connection]:
    settings = get_settings()
    try:
        connection = psycopg.connect(
            host=settings.db_host,
            port=settings.db_port,
            user=settings.db_user,
            password=settings.db_password,
            dbname=settings.db_name,
            autocommit=False,
            row_factory=dict_row,
            connect_timeout=5,
        )
    except psycopg.OperationalError as exc:  # pragma: no cover - local without DB
        pytest.skip(f"Postgres unavailable: {exc}")
    with connection.cursor() as cur:
        for table in (
            "console_users",
            "prompt_revisions",
            "prompt_optimization_runs",
            "news_summaries",
            "score_feedbacks",
            "submitted_reports",
            "submitted_report_items",
        ):
            cur.execute(
                f"CREATE TEMP TABLE {table} "
                f"(LIKE public.{table} INCLUDING ALL) ON COMMIT PRESERVE ROWS"
            )
    try:
        yield connection
    finally:
        connection.close()


def _seed(connection: psycopg.Connection) -> None:
    with connection.cursor() as cur:
        for article_id, score in (("target", 30), ("reference", 80)):
            cur.execute(
                """
                INSERT INTO news_summaries (
                    article_id, title, source, llm_summary, content_markdown,
                    sentiment_label, is_beijing_related,
                    external_importance_score, external_importance_raw
                )
                VALUES (%s, %s, '来源', '摘要', '正文', 'positive', false, %s, %s)
                """,
                (
                    article_id,
                    f"标题-{article_id}",
                    score,
                    Json({"prompt_key": "external_positive"}),
                ),
            )
        cur.execute(
            """
            INSERT INTO score_feedbacks (
                article_id, feedback_type, score_value, prompt_key, prompt_version,
                notes, created_at, updated_at
            )
            VALUES ('target', 'too_low', 50, 'external_positive', 'v1',
                    '应该高分', %s, %s)
            """,
            (NOW - timedelta(days=2), NOW - timedelta(days=1)),
        )
        cur.execute(
            """
            INSERT INTO submitted_reports (
                report_type, report_date, compiled_date, pasted_text
            )
            VALUES ('zongbao', current_date, current_date, '正文')
            RETURNING id
            """
        )
        report_id = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO submitted_report_items (
                report_id, title, norm_title, norm_title_hash, article_id,
                link_status, link_matched_at
            )
            VALUES (%s, '参照', '参照', 'hash', 'reference', 'matched', %s)
            """,
            (report_id, NOW - timedelta(days=1)),
        )
        cur.execute(
            """
            INSERT INTO prompt_revisions (
                id, prompt_key, revision, content, note, created_by_user_id
            )
            VALUES (1, 'external_positive', 1, 'BASE PROMPT', '初始文件版本', NULL)
            """
        )
    connection.commit()


def _snapshot(connection: psycopg.Connection, table: str) -> list[tuple]:
    with connection.cursor() as cur:
        cur.execute(f"SELECT * FROM {table} ORDER BY 1")
        return [tuple(row.values()) for row in cur.fetchall()]


def _prompt() -> PromptTemplate:
    return PromptTemplate(
        key="external_positive",
        version="v1",
        content="BASE PROMPT",
        source="database",
        revision=1,
        revision_id=1,
        updated_at=NOW - timedelta(days=2),
    )


def _optimizer(*args) -> PromptOptimizerDecision:
    return PromptOptimizerDecision(
        decision=DECISION_CHANGE,
        edits=(PromptEdit(old="BASE", new="CAND"),),
        note="自动优化说明",
        raw_output="{}",
        model="optimizer-model",
    )


def _replay(row, key: str, content: str):
    if "CAND" in content:
        return 80
    return 30 if row["article_id"] == "target" else 80


def test_dry_run_then_real_run_are_read_only_and_linked(
    conn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(conn)
    adapter = PostgresAdapter(conn)
    monkeypatch.setattr(worker, "load_active_prompt", lambda key, adapter: _prompt())
    monkeypatch.setattr(
        worker,
        "get_llm_step_config",
        lambda step: type("C", (), {"model": "optimizer-model"})(),
    )
    before_news = _snapshot(conn, "news_summaries")
    before_feedback = _snapshot(conn, "score_feedbacks")
    before_revisions = _snapshot(conn, "prompt_revisions")
    before_runs = _snapshot(conn, "prompt_optimization_runs")
    dry_replay_calls: list[str] = []
    dry_optimizer_calls: list[str] = []

    def dry_replay(row, key, content):
        dry_replay_calls.append(row["article_id"])
        return _replay(row, key, content)

    def dry_optimizer(*args):
        dry_optimizer_calls.append("called")
        return _optimizer(*args)

    dry = worker.run(
        ["external_positive"],
        dry_run=True,
        adapter=adapter,
        replay=dry_replay,
        optimizer=dry_optimizer,
        now=NOW,
    )
    dry_item = dry["items"][0]
    assert dry_item["outcome"] == "preview"
    assert dry_item["new_revision_id"] is None
    assert dry_item["case_count"] == 2
    assert dry_item["preview_precheck_will_finish"] is False
    assert dry_item["preview_estimated_skipped_count"] == 0
    assert dry_item["preview_estimated_replay_count"] == 2
    assert dry_item["preview_min_model_calls"] == 3
    assert dry_item["model_calls"] == 0
    assert dry_replay_calls == []
    assert dry_optimizer_calls == []
    assert _snapshot(conn, "news_summaries") == before_news
    assert _snapshot(conn, "score_feedbacks") == before_feedback
    assert _snapshot(conn, "prompt_revisions") == before_revisions
    assert _snapshot(conn, "prompt_optimization_runs") == before_runs

    result = worker.run(
        ["external_positive"],
        adapter=adapter,
        replay=_replay,
        optimizer=_optimizer,
        now=NOW,
    )
    item = result["items"][0]
    assert item["outcome"] == "revision_created"
    assert item["new_version"] == "v2"
    assert _snapshot(conn, "news_summaries") == before_news
    assert _snapshot(conn, "score_feedbacks") == before_feedback

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, revision, content, note, created_by_user_id "
            "FROM prompt_revisions ORDER BY revision"
        )
        revisions = cur.fetchall()
        cur.execute("SELECT * FROM prompt_optimization_runs")
        runs = cur.fetchall()

    assert [row["revision"] for row in revisions] == [1, 2]
    assert revisions[1]["content"] == "CAND PROMPT"
    assert revisions[1]["note"] == "自动优化说明"
    assert revisions[1]["created_by_user_id"] is None
    assert len(runs) == 1
    assert runs[0]["outcome"] == "revision_created"
    assert runs[0]["new_revision_id"] == revisions[1]["id"]
    assert runs[0]["baseline_revision_id"] == revisions[0]["id"]
    assert runs[0]["cases"]
    assert runs[0]["attempts"][0]["check"]["passed"] is True
