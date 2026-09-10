"""Facade for the manual-review queue adapter.

The import surface is unchanged from the single-file era: ``db_postgres_core``
binds the namespace class, ``db_postgres_shift_reviews`` reads the shared SQL
fragments and the candidate filter builder, and the console services import
``ManualReviewConflictError``.  All of those names are re-exported here.

Every name is re-exported explicitly rather than with ``import *`` so that
``dir(db_postgres_manual_reviews)`` stays what it was before the split, apart
from the submodule names a package necessarily binds.  The implementation
lives in sibling modules; ``AGENTS.md`` records which one owns which function.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence, Tuple

import psycopg

from src.adapters.db_postgres_manual_reviews._base import (
    CREATED_LOCAL_DATE_EXPRESSION,
    DUTY_UNPROCESSED_SQL,
    MANUAL_REVIEW_DECISION_LOCK_ID,
    MANUAL_REVIEW_SELECT_COLUMNS,
    ManualReviewConflictError,
    SCORE_FEEDBACK_JOIN,
    SEARCH_TEXT_EXPRESSION,
    manual_review_max_rank,
    report_type_expr,
)
from src.adapters.db_postgres_manual_reviews._clusters import (
    delete_manual_clusters,
    fetch_manual_clusters,
    insert_manual_clusters,
    release_advisory_lock,
    try_advisory_lock,
)
from src.adapters.db_postgres_manual_reviews._counts import (
    manual_review_pending_count,
    manual_review_status_counts,
)
from src.adapters.db_postgres_manual_reviews._filters import (
    _build_manual_candidate_filters,
    count_manual_candidates_before_date,
    discard_manual_candidates_before_date,
    fetch_manual_candidates_before_date_for_update,
    search_manual_candidates,
)
from src.adapters.db_postgres_manual_reviews._imports import (
    import_shift_reviews_into_manual,
    preview_shift_reviews_for_manual,
)
from src.adapters.db_postgres_manual_reviews._queries import (
    enqueue_manual_review,
    fetch_manual_pending_for_cluster,
    fetch_manual_reviews,
    fetch_review_buckets_for_update,
)
from src.adapters.db_postgres_manual_reviews._versions import (
    allocate_manual_review_decision_ranks,
    fetch_manual_review_rows,
    update_manual_review_order_as_user,
    update_manual_review_statuses_with_versions,
    update_manual_review_summaries_with_versions,
)
from src.adapters.db_postgres_manual_reviews._writes import (
    reset_manual_reviews_to_pending,
    update_manual_review_statuses,
    update_manual_review_summaries,
)
from src.domain.report_type import normalize_report_type as normalize_report_type_value

if TYPE_CHECKING:
    from src.adapters.db_postgres_core import PostgresAdapter

class ManualReviewsNamespace:
    """Single-table access to manual review queues and cluster cache."""

    def __init__(self, adapter: PostgresAdapter) -> None:
        self._adapter = adapter

    def fetch(
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
        query: Optional[str] = None,
        duty_unprocessed_only: bool = False,
    ) -> Tuple[List[Dict[str, Any]], int]:
        with self._adapter._cursor() as cur:
            return fetch_manual_reviews(
                cur,
                status=status,
                limit=limit,
                offset=offset,
                only_ready=only_ready,
                region=region,
                sentiment=sentiment,
                report_type=report_type,
                order_by_decided_at=order_by_decided_at,
                query=query,
                duty_unprocessed_only=duty_unprocessed_only,
            )

    def fetch_pending_for_cluster(
        self,
        *,
        region: Optional[str] = None,
        sentiment: Optional[str] = None,
        fetch_limit: int = 5000,
        report_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._adapter._cluster_transaction() as cur:
            return fetch_manual_pending_for_cluster(
                cur,
                region=region,
                sentiment=sentiment,
                fetch_limit=fetch_limit,
                report_type=report_type,
            )

    def search_candidates(
        self,
        *,
        query: Optional[str] = None,
        created_before: Optional[date] = None,
        limit: int = 30,
        offset: int = 0,
        region: Optional[str] = None,
        sentiment: Optional[str] = None,
        report_type: Optional[str] = None,
        duty_unprocessed_only: bool = False,
    ) -> Tuple[List[Dict[str, Any]], int]:
        with self._adapter._cursor() as cur:
            return search_manual_candidates(
                cur,
                query=query,
                created_before=created_before,
                limit=limit,
                offset=offset,
                region=region,
                sentiment=sentiment,
                report_type=report_type,
                duty_unprocessed_only=duty_unprocessed_only,
            )

    def count_candidates_before_date(
        self,
        *,
        region: str,
        sentiment: str,
        query: Optional[str] = None,
        created_before: Optional[date] = None,
        report_type: Optional[str] = None,
        duty_unprocessed_only: bool = False,
    ) -> int:
        with self._adapter._cursor() as cur:
            return count_manual_candidates_before_date(
                cur,
                region=region,
                sentiment=sentiment,
                query=query,
                created_before=created_before,
                report_type=report_type,
                duty_unprocessed_only=duty_unprocessed_only,
            )

    def bulk_discard_candidates(
        self,
        *,
        region: str,
        sentiment: str,
        query: Optional[str] = None,
        created_before: Optional[date] = None,
        actor: Optional[str] = None,
        decided_at: Optional[datetime] = None,
        report_type: Optional[str] = None,
        duty_unprocessed_only: bool = False,
    ) -> int:
        with self._adapter._cursor() as cur:
            return discard_manual_candidates_before_date(
                cur,
                region=region,
                sentiment=sentiment,
                query=query,
                created_before=created_before,
                actor=actor,
                decided_at=decided_at,
                report_type=report_type,
                duty_unprocessed_only=duty_unprocessed_only,
            )

    def replace_clusters(self, clusters: Sequence[Mapping[str, Any]]) -> int:
        with self._adapter._cluster_transaction() as cur:
            delete_manual_clusters(cur)
            return insert_manual_clusters(cur, clusters)

    def fetch_clusters(
        self,
        *,
        bucket_key: Optional[str] = None,
        duty_unprocessed_only: bool = False,
    ) -> List[Dict[str, Any]]:
        with self._adapter._cluster_transaction() as cur:
            return fetch_manual_clusters(
                cur,
                bucket_key=bucket_key,
                duty_unprocessed_only=duty_unprocessed_only,
            )

    def status_counts(self, *, report_type: Optional[str] = None) -> Dict[str, int]:
        with self._adapter._cursor() as cur:
            return manual_review_status_counts(cur, report_type=report_type)

    def max_rank(self, status: str, *, report_type: Optional[str] = None) -> float:
        with self._adapter._cursor() as cur:
            return manual_review_max_rank(cur, status, report_type=report_type)

    def update_statuses(
        self,
        updates: Sequence[Mapping[str, Any]],
        *,
        report_type: Optional[str] = None,
    ) -> int:
        with self._adapter._cursor() as cur:
            return update_manual_review_statuses(cur, updates, report_type=report_type)

    def reset_to_pending(
        self,
        article_ids: Sequence[str],
        *,
        actor: Optional[str] = None,
        decided_at: Optional[datetime] = None,
        report_type: Optional[str] = None,
    ) -> int:
        with self._adapter._cursor() as cur:
            return reset_manual_reviews_to_pending(
                cur,
                article_ids,
                actor=actor,
                decided_at=decided_at,
                report_type=report_type,
            )

    def update_summaries(
        self,
        edits: Mapping[str, Mapping[str, Any]],
        *,
        actor: Optional[str] = None,
        decided_at: Optional[datetime] = None,
        report_type: Optional[str] = None,
    ) -> int:
        with self._adapter._cursor() as cur:
            return update_manual_review_summaries(
                cur,
                edits,
                actor=actor,
                decided_at=decided_at,
                report_type=report_type,
            )

    def preview_shift_reviews(
        self,
        *,
        shift_id: str,
        article_ids: Sequence[str],
    ) -> List[Dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return preview_shift_reviews_for_manual(
                cur,
                shift_id=shift_id,
                article_ids=article_ids,
            )

__all__ = [
    "MANUAL_REVIEW_DECISION_LOCK_ID",
    "ManualReviewsNamespace",
    "ManualReviewConflictError",
    "allocate_manual_review_decision_ranks",
    "delete_manual_clusters",
    "enqueue_manual_review",
    "fetch_manual_clusters",
    "fetch_manual_candidates_before_date_for_update",
    "fetch_review_buckets_for_update",
    "fetch_manual_pending_for_cluster",
    "fetch_manual_reviews",
    "fetch_manual_review_rows",
    "import_shift_reviews_into_manual",
    "insert_manual_clusters",
    "manual_review_max_rank",
    "manual_review_pending_count",
    "manual_review_status_counts",
    "normalize_report_type_value",
    "preview_shift_reviews_for_manual",
    "report_type_expr",
    "reset_manual_reviews_to_pending",
    "release_advisory_lock",
    "try_advisory_lock",
    "update_manual_review_statuses",
    "update_manual_review_statuses_with_versions",
    "update_manual_review_order_as_user",
    "update_manual_review_summaries",
    "update_manual_review_summaries_with_versions",
]
