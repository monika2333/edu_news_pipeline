from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import psycopg

from src.adapters import db_postgres_core as db_factory
from src.adapters.http_toutiao import ArticleRecord, format_article_rows
from src.config import get_settings


def _reset_adapter_cache() -> None:
    os.environ.pop("DB_BACKEND", None)
    db_factory._ADAPTER = None  # type: ignore[attr-defined]
    get_settings.cache_clear()


def test_postgres_adapter_core_roundtrip() -> None:
    _reset_adapter_cache()

    settings = get_settings()
    adapter = db_factory.get_adapter()

    article_id = f"test-{uuid.uuid4()}"
    fetched_at = datetime.now(timezone.utc)
    publish_iso = fetched_at.replace(microsecond=0)

    article_record = ArticleRecord(
        token="unit-test",
        profile_url="https://example.com/profile",
        article_id=article_id,
        title="Unit Test Article",
        source="Unit Test Source",
        publish_time=int(fetched_at.timestamp()),
        publish_time_iso=publish_iso.isoformat(),
        url=f"https://example.com/articles/{article_id}",
        summary="placeholder",
        comment_count=1,
        digg_count=2,
        content_markdown="# Heading\nBody content",
        fetched_at=fetched_at.isoformat(),
    )

    run_id = f"run-{uuid.uuid4()}"
    summary_text = "This is a generated summary."

    try:
        rows = format_article_rows([article_record])
        adapter.upsert_toutiao_articles(rows)
        adapter.update_raw_article_details(
            [
                {
                    "article_id": article_id,
                    "token": article_record.token,
                    "profile_url": article_record.profile_url,
                    "title": article_record.title,
                    "source": article_record.source,
                    "publish_time": article_record.publish_time,
                    "publish_time_iso": article_record.publish_time_iso,
                    "url": article_record.url,
                    "summary": article_record.summary,
                    "comment_count": article_record.comment_count,
                    "digg_count": article_record.digg_count,
                    "content_markdown": article_record.content_markdown,
                    "detail_fetched_at": fetched_at.isoformat(),
                }
            ]
        )

        fetched_articles = adapter.fetch_toutiao_articles_for_summary(
            after_fetched_at=fetched_at.isoformat(),
            limit=20,
        )
        assert any(row.get("article_id") == article_id for row in fetched_articles)

        article_payload = {
            "article_id": article_id,
            "title": article_record.title,
            "source": article_record.source,
            "publish_time": article_record.publish_time,
            "publish_time_iso": article_record.publish_time_iso,
            "url": article_record.url,
            "content_markdown": article_record.content_markdown,
            "fetched_at": article_record.fetched_at,
        }

        adapter.upsert_news_summary(article_payload, summary_text, keywords=["unit", "test"])
        adapter.complete_summary(article_id, summary_text, llm_source="unit-test", keywords=["unit", "test"], beijing_related=True)
        existing_ids = adapter.get_existing_news_summary_ids([article_id])
        assert article_id in existing_ids

        # Set a score on the summary to enable export candidates
        adapter.update_summary_score(article_id, 0.92)
        export_candidates = adapter.fetch_export_candidates(min_score=0.5)
        matched_candidates = [candidate for candidate in export_candidates if candidate.filtered_article_id == article_id]
        assert matched_candidates
        assert matched_candidates[0].is_beijing_related is True

        tag = f"geo-{uuid.uuid4()}"
        adapter.record_export(tag, [(matched_candidates[0], "jingnei")], output_path="demo/path.txt")
        history_ids, batch_id = adapter.get_export_history(tag)
        assert batch_id is not None
        assert article_id in history_ids
        items = adapter.fetch_brief_items_by_batch(batch_id)
        assert items
        metadata = items[0].get("metadata") or {}
        assert metadata.get("is_beijing_related") is True

        adapter.record_pipeline_run_start(
            run_id=run_id,
            started_at=fetched_at,
            plan=["crawl", "summarize", "score"],
            trigger_source="unit-test",
        )
        adapter.record_pipeline_run_step(
            run_id=run_id,
            order_index=1,
            step_name="summarize",
            status="ok",
            started_at=fetched_at,
            finished_at=fetched_at,
            duration_seconds=0.0,
            error=None,
        )
        adapter.finalize_pipeline_run(
            run_id=run_id,
            status="succeeded",
            finished_at=fetched_at,
            steps_completed=1,
            artifacts={"summary_count": "1"},
            error_summary=None,
        )

        runs = adapter.fetch_pipeline_runs(limit=5)
        assert any(run.get("run_id") == run_id for run in runs)
        steps = adapter.fetch_pipeline_run_steps(run_id)
        assert any(step.get("step_name") == "summarize" for step in steps)

    finally:
        with psycopg.connect(
            host=settings.db_host,
            port=settings.db_port,
            user=settings.db_user,
            password=settings.db_password,
            dbname=settings.db_name,
            autocommit=True,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM pipeline_run_steps WHERE run_id = %s", (run_id,))
                cur.execute("DELETE FROM pipeline_runs WHERE run_id = %s", (run_id,))
                cur.execute("DELETE FROM news_summaries WHERE article_id = %s", (article_id,))
                cur.execute("DELETE FROM raw_articles WHERE article_id = %s", (article_id,))
                cur.execute("DELETE FROM brief_items WHERE article_id = %s", (article_id,))
                cur.execute("DELETE FROM brief_batches WHERE generated_by LIKE %s", ("geo-%",))

    _reset_adapter_cache()
