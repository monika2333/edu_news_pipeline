from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Json

from src.adapters.db_postgres_core import PostgresAdapter
from src.adapters.db_postgres_prompt_optimization import (
    collect_feedback_cases,
    collect_submission_cases,
)
from src.adapters.db_postgres_prompt_revisions import PromptRevisionConflictError
from src.config import get_settings

NOW = datetime.now(timezone.utc)


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


def _news(
    cur: psycopg.Cursor,
    article_id: str,
    *,
    prompt_key: str = "external_positive",
    score: float = 40,
) -> None:
    cur.execute(
        """
        INSERT INTO news_summaries (
            article_id, title, source, llm_summary, content_markdown,
            external_importance_score, external_importance_raw
        )
        VALUES (%s, %s, '来源', '摘要', '正文', %s, %s)
        """,
        (
            article_id,
            f"标题-{article_id}",
            score,
            Json({"prompt_key": prompt_key}),
        ),
    )


def _feedback(
    cur: psycopg.Cursor,
    article_id: str,
    *,
    score: float = 50,
    updated_days_ago: int = 1,
    created_days_ago: int = 40,
    prompt_key: str = "external_positive",
    prompt_version: str = "v1",
) -> None:
    cur.execute(
        """
        INSERT INTO score_feedbacks (
            article_id, feedback_type, score_value, prompt_key, prompt_version,
            notes, created_at, updated_at
        )
        VALUES (%s, 'too_low', %s, %s, %s, '原因', %s, %s)
        """,
        (
            article_id,
            score,
            prompt_key,
            prompt_version,
            NOW - timedelta(days=created_days_ago),
            NOW - timedelta(days=updated_days_ago),
        ),
    )


def _submission(
    cur: psycopg.Cursor,
    article_id: str | None,
    *,
    report_type: str = "zongbao",
    link_status: str = "matched",
    days_ago: int = 1,
) -> None:
    cur.execute(
        """
        INSERT INTO submitted_reports (
            report_type, report_date, compiled_date, pasted_text
        )
        VALUES (%s, current_date, current_date, '正文')
        RETURNING id
        """,
        (report_type,),
    )
    report_id = cur.fetchone()["id"]
    cur.execute(
        """
        INSERT INTO submitted_report_items (
            report_id, title, norm_title, norm_title_hash, article_id,
            link_status, link_matched_at
        )
        VALUES (%s, '条目', '条目', 'hash', %s, %s, %s)
        """,
        (
            report_id,
            article_id,
            link_status,
            NOW - timedelta(days=days_ago),
        ),
    )


def test_feedback_collection_uses_updated_at_latest_row_and_prompt_key(conn) -> None:
    with conn.cursor() as cur:
        for article_id in ("a1", "a2", "old-update", "wrong-key", "wrong-update"):
            _news(cur, article_id)
        _feedback(cur, "a1", score=50, updated_days_ago=1, created_days_ago=90)
        _feedback(
            cur,
            "a2",
            score=20,
            updated_days_ago=4,
            prompt_version="v1",
        )
        _feedback(
            cur,
            "a2",
            score=30,
            updated_days_ago=2,
            prompt_version="v2",
        )
        _feedback(cur, "old-update", updated_days_ago=40)
        _feedback(cur, "wrong-key", prompt_key="external_negative")
        _feedback(cur, "wrong-update", updated_days_ago=40)

        rows = collect_feedback_cases(
            cur,
            "external_positive",
            since=NOW - timedelta(days=30),
            limit=90,
        )

    by_id = {row["article_id"]: row for row in rows}
    assert set(by_id) == {"a1", "a2"}
    # created_at 远早于窗口，但 updated_at 在窗口内，必须纳入。
    assert by_id["a1"]["original_score"] == 50
    # 同一篇多条标注取 updated_at 最新的一条。
    assert by_id["a2"]["original_score"] == 30
    assert timedelta(days=1, hours=23) < NOW - by_id["a2"]["event_at"] < timedelta(
        days=2, hours=1
    )


def test_submission_collection_filters_matched_article_and_prompt_key(conn) -> None:
    with conn.cursor() as cur:
        for article_id in (
            "zongbao",
            "wanbao",
            "feedback",
            "pending",
            "wrong-key",
            "null-article",
            "feedback-covered",
        ):
            _news(
                cur,
                article_id,
                prompt_key=(
                    "external_negative" if article_id == "wrong-key"
                    else "external_positive"
                ),
            )
        _feedback(cur, "feedback-covered", updated_days_ago=1)
        _submission(cur, "zongbao", report_type="zongbao")
        _submission(cur, "wanbao", report_type="wanbao")
        _submission(cur, "feedback", report_type="feedback")
        _submission(cur, "pending", link_status="pending")
        _submission(cur, "wrong-key", report_type="zongbao")
        _submission(cur, "feedback-covered", report_type="zongbao")
        _submission(cur, None, report_type="zongbao")

        rows = collect_submission_cases(
            cur,
            "external_positive",
            since=NOW - timedelta(days=30),
            limit=90,
        )

    by_id = {row["article_id"]: row for row in rows}
    assert set(by_id) == {"zongbao", "wanbao", "feedback"}
    assert set(by_id["zongbao"]["report_types"]) == {"zongbao"}
    assert set(by_id["wanbao"]["report_types"]) == {"wanbao"}
    assert set(by_id["feedback"]["report_types"]) == {"feedback"}


def test_system_append_with_file_baseline_and_run_link(conn) -> None:
    adapter = PostgresAdapter(conn)
    result = adapter.prompt_optimization.record_run_with_revision(
        prompt_key="external_positive",
        content="new body",
        note="自动优化说明",
        expected_revision_id=None,
        baseline={"revision": 2, "content": "file body"},
        baseline_revision_id=None,
        started_at=NOW,
        finished_at=NOW,
        window_start=NOW - timedelta(days=30),
        model="optimizer-model",
        cases=[{"article_id": "a1", "bucket": "T"}],
        attempts=[{"attempt": 1, "check": {"passed": True}}],
    )

    revision = result["revision"]
    run = result["run"]
    assert revision["revision"] == 3
    assert revision["created_by_user_id"] is None
    assert revision["note"] == "自动优化说明"
    assert run["new_revision_id"] == revision["id"]
    assert run["baseline_revision_id"] is not None
    assert run["cases"][0]["article_id"] == "a1"

    with conn.cursor() as cur:
        cur.execute(
            "SELECT revision, note, created_by_user_id FROM prompt_revisions "
            "WHERE prompt_key = %s ORDER BY revision",
            ("external_positive",),
        )
        history = cur.fetchall()
    assert [(row["revision"], row["created_by_user_id"]) for row in history] == [
        (2, None),
        (3, None),
    ]
    assert history[0]["note"] == "初始文件版本"

    fetched = adapter.prompt_optimization.fetch_run_by_new_revision_id(
        int(revision["id"])
    )
    assert fetched is not None
    assert fetched["id"] == run["id"]


def test_stale_expected_revision_id_rolls_back_version_and_run(conn) -> None:
    adapter = PostgresAdapter(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO prompt_revisions (
                prompt_key, revision, content, note, created_by_user_id
            )
            VALUES ('external_positive', 1, 'current', '初始文件版本', NULL)
            """
        )
        cur.execute("SELECT count(*) AS count FROM prompt_optimization_runs")
        before_runs = int(cur.fetchone()["count"])
    # 先提交建表与种子；否则冲突回滚会把 TEMP TABLE 的创建一起撤销。
    conn.commit()

    with pytest.raises(PromptRevisionConflictError):
        adapter.prompt_optimization.record_run_with_revision(
            prompt_key="external_positive",
            content="candidate",
            note="不应写入",
            expected_revision_id=999,
            baseline=None,
            baseline_revision_id=1,
            started_at=NOW,
            finished_at=NOW,
            window_start=NOW - timedelta(days=30),
            model="optimizer-model",
            cases=[],
            attempts=[],
        )

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS count FROM prompt_revisions")
        assert int(cur.fetchone()["count"]) == 1
        cur.execute("SELECT count(*) AS count FROM prompt_optimization_runs")
        assert int(cur.fetchone()["count"]) == before_runs
