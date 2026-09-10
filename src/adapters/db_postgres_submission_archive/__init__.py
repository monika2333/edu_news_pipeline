"""Facade for the submitted-report archive adapter.

The import surface is unchanged from the single-file era: callers keep
importing ``db_postgres_submission_archive`` and see the same namespace class,
the same module-level functions and the same ``PRIOR_MATCH_REPORT_TYPES``.

Every public name is re-exported explicitly rather than with ``import *`` so
that ``dir(db_postgres_submission_archive)`` stays what it was before the
split, apart from the submodule names a package necessarily binds.  The
implementation lives in sibling modules; ``AGENTS.md`` records which one owns
which function.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Mapping,
    Optional,
    Sequence,
    TypedDict,
)

import psycopg

from src.adapters.db_postgres_submission_archive._base import (
    ItemFieldUpdateResult,
    ManualLinkMutationResult,
    PRIOR_MATCH_REPORT_TYPES,
    PriorMatchDecisionMutationResult,
)
from src.adapters.db_postgres_submission_archive.dedup import (
    dismiss_duplicate_matches,
    fetch_archive_embeddings,
    fetch_duplicate_badges,
    fetch_duplicate_match_details,
    fetch_item_duplicate_match_details,
    fetch_item_duplicate_match_summaries,
    fetch_item_match_inputs,
    fetch_news_for_submission_dedup,
    fetch_prior_submission_candidates,
    replace_item_duplicate_matches,
    set_item_prior_match_decision,
    upsert_duplicate_matches,
)
from src.adapters.db_postgres_submission_archive.items import (
    fetch_items_missing_embeddings,
    search_items,
    update_item_embeddings,
    update_item_fields,
)
from src.adapters.db_postgres_submission_archive.links import (
    decide_link,
    fetch_link_candidate_bodies,
    fetch_link_candidate_titles,
    fetch_manual_link_candidates,
    fetch_pending_links,
    manual_link_item,
    manual_unlink_item,
    update_link_results,
)
from src.adapters.db_postgres_submission_archive.reports import (
    count_export_rows,
    delete_report,
    fetch_export_rows,
    fetch_report,
    fetch_report_by_source_message,
    fetch_report_ids_by_type,
    fetch_reports,
    find_report_conflict,
    insert_report,
    insert_report_items,
    mark_prior_match_completed,
)
from src.domain.report_type import NEWS_REPORT_TYPES
from src.domain.submission_archive_config import (
    COVERAGE_EXCLUDED_REPORT_TYPE,
    COVERAGE_EXCLUDED_SECTION,
    is_coverage_excluded,
)

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

    def fetch_export_rows(
        self,
        *,
        date_from: Optional[date],
        date_to: Optional[date],
        report_types: Optional[Sequence[str]],
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_export_rows(
                cur,
                date_from=date_from,
                date_to=date_to,
                report_types=report_types,
                limit=limit,
            )

    def count_export_rows(
        self,
        *,
        date_from: Optional[date],
        date_to: Optional[date],
        report_types: Optional[Sequence[str]],
    ) -> tuple[int, int]:
        with self._adapter._cursor() as cur:
            return count_export_rows(
                cur,
                date_from=date_from,
                date_to=date_to,
                report_types=report_types,
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
        report_id: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        with self._adapter._cursor() as cur:
            return fetch_pending_links(
                cur,
                limit=limit,
                offset=offset,
                report_id=report_id,
            )

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

    def fetch_items_missing_embeddings(
        self,
        *,
        lookback_days: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_items_missing_embeddings(
                cur,
                lookback_days=lookback_days,
                limit=limit,
            )

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
        compiled_date: date,
        lookback_days: int,
    ) -> list[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_prior_submission_candidates(
                cur,
                compiled_date=compiled_date,
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

    def set_item_prior_match_decision(
        self,
        *,
        item_id: str,
        decision: Optional[str],
        actor_user_id: str,
    ) -> PriorMatchDecisionMutationResult:
        with self._adapter.transaction() as cur:
            return set_item_prior_match_decision(
                cur,
                item_id=item_id,
                decision=decision,
                actor_user_id=actor_user_id,
            )

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

__all__ = [
    "PRIOR_MATCH_REPORT_TYPES",
    "ItemFieldUpdateResult",
    "ManualLinkMutationResult",
    "PriorMatchDecisionMutationResult",
    "SubmissionArchiveNamespace",
    "count_export_rows",
    "decide_link",
    "delete_report",
    "dismiss_duplicate_matches",
    "fetch_archive_embeddings",
    "fetch_duplicate_badges",
    "fetch_duplicate_match_details",
    "fetch_export_rows",
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
    "set_item_prior_match_decision",
    "update_item_embeddings",
    "update_item_fields",
    "update_link_results",
    "upsert_duplicate_matches",
]
