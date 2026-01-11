from __future__ import annotations

import contextlib
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from src.adapters import (
    db_postgres_export as export,
    db_postgres_ingest as ingest,
    db_postgres_manual_reviews as manual_reviews,
    db_postgres_news_summaries as news_summaries,
    db_postgres_process as process,
)
from src.adapters.db_postgres_shared import MISSING as _MISSING
from src.adapters.db_postgres_shared import article_hash, iso_datetime, json_safe, to_iso
from src.config import get_settings
from src.domain import BeijingGateCandidate, ExportCandidate, ExternalFilterCandidate, PrimaryArticleForScoring

_CONNECTION: Optional[psycopg.Connection] = None
_ADAPTER: Optional["PostgresAdapter"] = None


def _get_connection() -> psycopg.Connection:
    global _CONNECTION
    settings = get_settings()
    if _CONNECTION is None or _CONNECTION.closed:
        _CONNECTION = psycopg.connect(
            host=settings.db_host,
            port=settings.db_port,
            user=settings.db_user,
            password=settings.db_password,
            dbname=settings.db_name,
            autocommit=True,
        )
        schema = settings.db_schema or "public"
        with _CONNECTION.cursor() as cur:
            cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
    return _CONNECTION


class PostgresAdapter:
    """High-level helpers for interacting with the local PostgreSQL database."""

    def __init__(self, connection: Optional[psycopg.Connection] = None) -> None:
        self._settings = get_settings()
        self._schema = self._settings.db_schema or "public"
        self._conn = connection or _get_connection()

    def _conn_cursor(self):
        if self._conn.closed:
            self._conn = _get_connection()
        return self._conn.cursor(row_factory=dict_row)

    @contextlib.contextmanager
    def _cursor(self):
        cur = self._conn_cursor()
        try:
            yield cur
            if not self._conn.autocommit:
                self._conn.commit()
        except Exception:
            if not self._conn.autocommit:
                self._conn.rollback()
            raise
        finally:
            cur.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _article_hash(article_id: Optional[str], original_url: Optional[str], title: Optional[str]) -> str:
        return article_hash(article_id, original_url, title)

    @staticmethod
    def _to_iso(publish_time: Optional[int]) -> Optional[str]:
        return to_iso(publish_time)

    @staticmethod
    def _iso_datetime(value: Any) -> Optional[str]:
        return iso_datetime(value)

    @staticmethod
    def _json_safe(value: Any) -> Any:
        return json_safe(value)

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------
    def upsert_toutiao_articles(self, rows: Sequence[Mapping[str, Any]]) -> int:
        with self._cursor() as cur:
            return ingest.upsert_toutiao_articles(cur, rows)

    def upsert_raw_feed_rows(self, rows: Sequence[Mapping[str, Any]]) -> int:
        with self._cursor() as cur:
            return ingest.upsert_raw_feed_rows(cur, rows)

    def update_raw_article_details(self, rows: Sequence[Mapping[str, Any]]) -> int:
        with self._cursor() as cur:
            return ingest.update_raw_article_details(cur, rows)

    def get_raw_articles_missing_content(self, article_ids: Sequence[str]) -> Set[str]:
        with self._cursor() as cur:
            return ingest.get_raw_articles_missing_content(cur, article_ids)

    def fetch_raw_articles_missing_content(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return ingest.fetch_raw_articles_missing_content(cur, limit)

    def upsert_filtered_articles(self, rows: Sequence[Mapping[str, Any]]) -> int:
        with self._cursor() as cur:
            return ingest.upsert_filtered_articles(cur, rows)

    def fetch_filtered_articles_for_hashing(self, limit: int) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return ingest.fetch_filtered_articles_for_hashing(cur, limit)

    def fetch_filtered_articles_by_hashes(self, hashes: Sequence[str]) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return ingest.fetch_filtered_articles_by_hashes(cur, hashes)

    def update_filtered_article_features(self, updates: Sequence[Mapping[str, Any]]) -> int:
        with self._cursor() as cur:
            return ingest.update_filtered_article_features(cur, updates)

    def fetch_filtered_articles_by_band(self, band_index: int, band_value: int, limit: int) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return ingest.fetch_filtered_articles_by_band(cur, band_index, band_value, limit)

    def update_filtered_primary_ids(self, updates: Sequence[Mapping[str, Any]]) -> int:
        with self._cursor() as cur:
            return ingest.update_filtered_primary_ids(cur, updates)

    def upsert_primary_articles(self, rows: Sequence[Mapping[str, Any]]) -> int:
        with self._cursor() as cur:
            return ingest.upsert_primary_articles(cur, rows)

    def get_existing_raw_article_ids(self) -> Set[str]:
        with self._cursor() as cur:
            return ingest.get_existing_raw_article_ids(cur)

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------
    def insert_pending_summary(
        self,
        article: Mapping[str, Any],
        *,
        keywords: Optional[Sequence[str]] = None,
        fetched_at: Optional[str] = None,
    ) -> None:
        with self._cursor() as cur:
            news_summaries.insert_pending_summary(cur, article, keywords=keywords, fetched_at=fetched_at)

    def fetch_pending_summaries(
        self,
        limit: Optional[int] = None,
        *,
        max_attempts: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return news_summaries.fetch_pending_summaries(cur, limit, max_attempts=max_attempts)

    def mark_summary_attempt(self, article_id: str) -> bool:
        with self._cursor() as cur:
            return news_summaries.mark_summary_attempt(cur, article_id)

    def complete_summary(
        self,
        article_id: str,
        summary_text: str,
        *,
        llm_source: Optional[str] = None,
        keywords: Optional[Sequence[str]] = None,
        beijing_related: Optional[bool] = None,
        sentiment_label: Optional[str] = None,
        sentiment_confidence: Optional[float] = None,
        status: str = "ready_for_export",
        external_importance_status: Any = _MISSING,
        external_importance_score: Any = _MISSING,
        external_importance_checked_at: Any = _MISSING,
        external_importance_raw: Any = _MISSING,
        external_filter_attempted_at: Any = _MISSING,
        external_filter_fail_count: Any = _MISSING,
        is_beijing_related_llm: Any = _MISSING,
        beijing_gate_checked_at: Any = _MISSING,
        beijing_gate_raw: Any = _MISSING,
        beijing_gate_attempted_at: Any = _MISSING,
        beijing_gate_fail_count: Any = _MISSING,
    ) -> None:
        with self._cursor() as cur:
            news_summaries.complete_summary(
                cur,
                article_id,
                summary_text,
                llm_source=llm_source,
                keywords=keywords,
                beijing_related=beijing_related,
                sentiment_label=sentiment_label,
                sentiment_confidence=sentiment_confidence,
                status=status,
                external_importance_status=external_importance_status,
                external_importance_score=external_importance_score,
                external_importance_checked_at=external_importance_checked_at,
                external_importance_raw=external_importance_raw,
                external_filter_attempted_at=external_filter_attempted_at,
                external_filter_fail_count=external_filter_fail_count,
                is_beijing_related_llm=is_beijing_related_llm,
                beijing_gate_checked_at=beijing_gate_checked_at,
                beijing_gate_raw=beijing_gate_raw,
                beijing_gate_attempted_at=beijing_gate_attempted_at,
                beijing_gate_fail_count=beijing_gate_fail_count,
            )

    def mark_summary_failed(self, article_id: str, *, message: Optional[str] = None) -> None:
        with self._cursor() as cur:
            news_summaries.mark_summary_failed(cur, article_id, message=message)

    def search_news_summaries(
        self,
        *,
        query: Optional[str] = None,
        sources: Optional[Sequence[str]] = None,
        sentiments: Optional[Sequence[str]] = None,
        statuses: Optional[Sequence[str]] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        with self._cursor() as cur:
            return news_summaries.search_news_summaries(
                cur,
                query=query,
                sources=sources,
                sentiments=sentiments,
                statuses=statuses,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
                offset=offset,
            )

    def fetch_raw_articles_for_summary(
        self,
        *,
        after_fetched_at: Optional[str],
        limit: Optional[int],
    ) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return news_summaries.fetch_raw_articles_for_summary(
                cur,
                after_fetched_at=after_fetched_at,
                limit=limit,
            )

    def get_existing_news_summary_ids(self, article_ids: Sequence[str]) -> Set[str]:
        with self._cursor() as cur:
            return news_summaries.get_existing_news_summary_ids(cur, article_ids)

    def upsert_news_summary(
        self,
        article: Dict[str, Any],
        summary: str,
        *,
        keywords: Optional[Sequence[str]] = None,
    ) -> None:
        with self._cursor() as cur:
            news_summaries.upsert_news_summary(cur, article, summary, keywords=keywords)

    def update_summary_score(self, article_id: str, score: Optional[float]) -> None:
        with self._cursor() as cur:
            news_summaries.update_summary_score(cur, article_id, score)

    # ------------------------------------------------------------------
    # Backward-compat wrappers (to be removed after refactor)
    # ------------------------------------------------------------------
    def upsert_toutiao_feed_rows(self, rows: Sequence[Mapping[str, Any]]) -> int:
        return self.upsert_raw_feed_rows(rows)

    def update_toutiao_article_details(self, rows: Sequence[Mapping[str, Any]]) -> int:
        return self.update_raw_article_details(rows)

    def get_toutiao_articles_missing_content(self, article_ids: Sequence[str]) -> Set[str]:
        return self.get_raw_articles_missing_content(article_ids)

    def fetch_toutiao_articles_missing_content(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        return self.fetch_raw_articles_missing_content(limit)

    def get_existing_toutiao_article_ids(self) -> Set[str]:
        return self.get_existing_raw_article_ids()

    def fetch_toutiao_articles_for_summary(
        self,
        *,
        after_fetched_at: Optional[str],
        limit: Optional[int],
    ) -> List[Dict[str, Any]]:
        return self.fetch_raw_articles_for_summary(after_fetched_at=after_fetched_at, limit=limit)

    def upsert_news_summaries_from_primary(self, rows: Sequence[Mapping[str, Any]]) -> int:
        with self._cursor() as cur:
            return news_summaries.upsert_news_summaries_from_primary(cur, rows)

    # ------------------------------------------------------------------
    # Process + Scoring
    # ------------------------------------------------------------------
    def fetch_primary_articles_for_scoring(self, limit: int) -> List[PrimaryArticleForScoring]:
        with self._cursor() as cur:
            return process.fetch_primary_articles_for_scoring(cur, limit)

    def update_primary_article_scores(self, updates: Sequence[Mapping[str, Any]]) -> int:
        with self._cursor() as cur:
            return process.update_primary_article_scores(cur, updates)

    def fetch_beijing_gate_candidates(
        self,
        limit: int,
        *,
        max_failures: Optional[int] = None,
    ) -> List[BeijingGateCandidate]:
        with self._cursor() as cur:
            return process.fetch_beijing_gate_candidates(cur, limit, max_failures=max_failures)

    def fetch_external_filter_candidates(
        self,
        limit: int,
        *,
        max_failures: Optional[int] = None,
    ) -> List[ExternalFilterCandidate]:
        with self._cursor() as cur:
            return process.fetch_external_filter_candidates(cur, limit, max_failures=max_failures)

    def complete_beijing_gate(
        self,
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
        with self._cursor() as cur:
            process.complete_beijing_gate(
                cur,
                article_id,
                status=status,
                is_beijing_related=is_beijing_related,
                is_beijing_related_llm=is_beijing_related_llm,
                raw_output=raw_output,
                external_importance_status=external_importance_status,
                reset_external_filter=reset_external_filter,
                sentiment_label=sentiment_label,
                candidate_category=candidate_category,
            )

    def mark_beijing_gate_failure(
        self,
        article_id: str,
        *,
        fail_count: int,
        error: str,
        final_status: Optional[str] = None,
        external_importance_status: Optional[str] = None,
    ) -> None:
        with self._cursor() as cur:
            process.mark_beijing_gate_failure(
                cur,
                article_id,
                fail_count=fail_count,
                error=error,
                final_status=final_status,
                external_importance_status=external_importance_status,
            )

    def complete_external_filter(
        self,
        article_id: str,
        *,
        passed: bool,
        score: int,
        raw_output: str,
        category: Optional[str] = None,
    ) -> None:
        with self._cursor() as cur:
            timestamp = process.complete_external_filter(
                cur,
                article_id,
                passed=passed,
                score=score,
                raw_output=raw_output,
                category=category,
            )
        if passed:
            with self._cursor() as cur:
                manual_reviews.enqueue_manual_review(cur, article_id, status="pending")
        else:
            with self._cursor() as cur:
                manual_reviews.update_manual_review_statuses(
                    cur,
                    [
                        {
                            "article_id": article_id,
                            "status": "discarded",
                            "decided_at": timestamp,
                        }
                    ],
                )

    def mark_external_filter_failure(
        self,
        article_id: str,
        *,
        fail_count: int,
        final_failure: bool,
        error: str,
    ) -> None:
        with self._cursor() as cur:
            process.mark_external_filter_failure(
                cur,
                article_id,
                fail_count=fail_count,
                final_failure=final_failure,
                error=error,
            )

    def fetch_external_backfill_candidates(self, limit: int, since_date: Optional[date] = None) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return process.fetch_external_backfill_candidates(cur, limit, since_date=since_date)

    def reset_external_filter_pending(self, article_ids: Sequence[str]) -> int:
        with self._cursor() as cur:
            return process.reset_external_filter_pending(cur, article_ids)

    def fetch_beijing_tag_candidates(self, limit: int) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return process.fetch_beijing_tag_candidates(cur, limit)

    def update_beijing_related_bulk(self, updates: Sequence[Tuple[str, bool]]) -> int:
        with self._cursor() as cur:
            return process.update_beijing_related_bulk(cur, updates)

    # ------------------------------------------------------------------
    # Manual reviews
    # ------------------------------------------------------------------
    def _normalize_report_type_value(self, report_type: Optional[str]) -> Optional[str]:
        return manual_reviews.normalize_report_type_value(report_type)

    @staticmethod
    def _report_type_expr(alias: str = "") -> str:
        return manual_reviews.report_type_expr(alias)

    def enqueue_manual_review(
        self,
        article_id: str,
        *,
        status: str = "pending",
        report_type: Optional[str] = None,
        rank: Optional[float] = None,
        summary: Optional[str] = None,
        notes: Optional[str] = None,
        score: Optional[float] = None,
        decided_by: Optional[str] = None,
        decided_at: Optional[datetime] = None,
    ) -> None:
        with self._cursor() as cur:
            manual_reviews.enqueue_manual_review(
                cur,
                article_id,
                status=status,
                report_type=report_type,
                rank=rank,
                summary=summary,
                notes=notes,
                score=score,
                decided_by=decided_by,
                decided_at=decided_at,
            )

    def fetch_manual_reviews(
        self,
        *,
        status: str,
        limit: int,
        offset: int,
        only_ready: bool = False,
        region: Optional[str] = None,
        sentiment: Optional[str] = None,
        report_type: Optional[str] = None,
        order_by_decided_at: bool = False,
    ) -> Tuple[List[Dict[str, Any]], int]:
        with self._cursor() as cur:
            return manual_reviews.fetch_manual_reviews(
                cur,
                status=status,
                limit=limit,
                offset=offset,
                only_ready=only_ready,
                region=region,
                sentiment=sentiment,
                report_type=report_type,
                order_by_decided_at=order_by_decided_at,
            )

    def fetch_manual_pending_for_cluster(
        self,
        *,
        region: Optional[str] = None,
        sentiment: Optional[str] = None,
        fetch_limit: int = 5000,
        report_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return manual_reviews.fetch_manual_pending_for_cluster(
                cur,
                region=region,
                sentiment=sentiment,
                fetch_limit=fetch_limit,
                report_type=report_type,
            )

    def manual_review_status_counts(self, *, report_type: Optional[str] = None) -> Dict[str, int]:
        with self._cursor() as cur:
            return manual_reviews.manual_review_status_counts(cur, report_type=report_type)

    def manual_review_pending_count(self, *, report_type: Optional[str] = None) -> int:
        with self._cursor() as cur:
            return manual_reviews.manual_review_pending_count(cur, report_type=report_type)

    def manual_review_max_rank(self, status: str, *, report_type: Optional[str] = None) -> float:
        with self._cursor() as cur:
            return manual_reviews.manual_review_max_rank(cur, status, report_type=report_type)

    def update_manual_review_statuses(
        self,
        updates: Sequence[Mapping[str, Any]],
        *,
        report_type: Optional[str] = None,
    ) -> int:
        with self._cursor() as cur:
            return manual_reviews.update_manual_review_statuses(cur, updates, report_type=report_type)

    def reset_manual_reviews_to_pending(
        self,
        article_ids: Sequence[str],
        *,
        actor: Optional[str] = None,
        decided_at: Optional[datetime] = None,
        report_type: Optional[str] = None,
    ) -> int:
        with self._cursor() as cur:
            return manual_reviews.reset_manual_reviews_to_pending(
                cur,
                article_ids,
                actor=actor,
                decided_at=decided_at,
                report_type=report_type,
            )

    def update_manual_review_summaries(
        self,
        edits: Mapping[str, Mapping[str, Any]],
        *,
        actor: Optional[str] = None,
        decided_at: Optional[datetime] = None,
        report_type: Optional[str] = None,
    ) -> int:
        with self._cursor() as cur:
            return manual_reviews.update_manual_review_summaries(
                cur,
                edits,
                actor=actor,
                decided_at=decided_at,
                report_type=report_type,
            )

    def fetch_manual_selected_for_export(self, *, report_type: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return manual_reviews.fetch_manual_selected_for_export(cur, report_type=report_type)

    # ------------------------------------------------------------------
    # Export + batches
    # ------------------------------------------------------------------
    def fetch_export_candidates(self, min_score: float) -> List[ExportCandidate]:
        with self._cursor() as cur:
            return export.fetch_export_candidates(cur, min_score)

    def _get_batch_by_tag(self, report_tag: str) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            return export.get_batch_by_tag(cur, report_tag)

    def _get_manual_batch_by_tag(self, report_tag: str) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            return export.get_manual_batch_by_tag(cur, report_tag)

    def _parse_report_tag(self, report_tag: str) -> Tuple[date, str]:
        return export.parse_report_tag(report_tag)

    def _create_batch(self, report_tag: str) -> Dict[str, Any]:
        with self._cursor() as cur:
            return export.create_batch(cur, report_tag)

    def _create_manual_batch(self, report_tag: str) -> Dict[str, Any]:
        with self._cursor() as cur:
            return export.create_manual_batch(cur, report_tag)

    def get_export_history(self, report_tag: str) -> Tuple[Set[str], Optional[str]]:
        with self._cursor() as cur:
            return export.get_export_history(cur, report_tag)

    def get_manual_export_history(self, report_tag: str) -> Tuple[Set[str], Optional[str]]:
        with self._cursor() as cur:
            return export.get_manual_export_history(cur, report_tag)

    def get_all_exported_article_ids(self) -> Set[str]:
        with self._cursor() as cur:
            return export.get_all_exported_article_ids(cur)

    def record_export(
        self,
        report_tag: str,
        exported: Sequence[Tuple[ExportCandidate, str]],
        *,
        output_path: str,
    ) -> None:
        with self._cursor() as cur:
            export.record_export(cur, report_tag, exported, output_path=output_path)

    def record_manual_export(
        self,
        report_tag: str,
        exported: Sequence[Tuple[ExportCandidate, str]],
        *,
        output_path: str,
    ) -> None:
        with self._cursor() as cur:
            export.record_manual_export(cur, report_tag, exported, output_path=output_path)

    def fetch_latest_brief_batch(self) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            return export.fetch_latest_brief_batch(cur)

    def fetch_brief_items_by_batch(self, batch_id: str) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return export.fetch_brief_items_by_batch(cur, batch_id)

    def fetch_brief_item_count(self, batch_id: str) -> int:
        with self._cursor() as cur:
            return export.fetch_brief_item_count(cur, batch_id)

    def fetch_latest_manual_export_batch(self) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            return export.fetch_latest_manual_export_batch(cur)

    def fetch_manual_export_items_by_batch(self, batch_id: str) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return export.fetch_manual_export_items_by_batch(cur, batch_id)

    def fetch_manual_export_item_count(self, batch_id: str) -> int:
        with self._cursor() as cur:
            return export.fetch_manual_export_item_count(cur, batch_id)

    # ------------------------------------------------------------------
    # Pipeline run metadata
    # ------------------------------------------------------------------
    def record_pipeline_run_start(
        self,
        *,
        run_id: str,
        started_at: datetime,
        plan: Sequence[str],
        trigger_source: Optional[str] = None,
    ) -> None:
        with self._cursor() as cur:
            process.record_pipeline_run_start(
                cur,
                run_id=run_id,
                started_at=started_at,
                plan=plan,
                trigger_source=trigger_source,
            )

    def record_pipeline_run_step(
        self,
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
        with self._cursor() as cur:
            process.record_pipeline_run_step(
                cur,
                run_id=run_id,
                order_index=order_index,
                step_name=step_name,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=duration_seconds,
                error=error,
            )

    def finalize_pipeline_run(
        self,
        *,
        run_id: str,
        status: str,
        finished_at: datetime,
        steps_completed: int,
        artifacts: Optional[Mapping[str, str]] = None,
        error_summary: Optional[str] = None,
    ) -> None:
        with self._cursor() as cur:
            process.finalize_pipeline_run(
                cur,
                run_id=run_id,
                status=status,
                finished_at=finished_at,
                steps_completed=steps_completed,
                artifacts=artifacts,
                error_summary=error_summary,
            )

    def fetch_pipeline_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return process.fetch_pipeline_runs(cur, limit=limit)

    def fetch_pipeline_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            return process.fetch_pipeline_run(cur, run_id)

    def fetch_pipeline_run_steps(self, run_id: str) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            return process.fetch_pipeline_run_steps(cur, run_id)


def get_adapter() -> PostgresAdapter:
    global _ADAPTER
    if _ADAPTER is None:
        _ADAPTER = PostgresAdapter()
    return _ADAPTER


__all__ = ["PostgresAdapter", "get_adapter"]
