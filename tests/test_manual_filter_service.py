from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import pytest

from src.console import manual_filter_service


class FakeSubmissionArchiveNamespace:
    def fetch_duplicate_badges(
        self,
        article_ids: Sequence[str],
    ) -> dict[str, dict[str, Any]]:
        del article_ids
        return {}


class FakeManualReviewsNamespace:
    def __init__(self, adapter: FakeAdapter) -> None:
        self._adapter = adapter

    def fetch(self, **kwargs: Any) -> Tuple[List[Dict[str, Any]], int]:
        return self._adapter._fetch(**kwargs)

    def fetch_pending_for_cluster(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self._adapter._fetch_pending_for_cluster(**kwargs)

    def fetch_clusters(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self._adapter._fetch_clusters(**kwargs)

    def search_candidates(self, **kwargs: Any) -> Tuple[List[Dict[str, Any]], int]:
        return self._adapter._search_candidates(**kwargs)

    def status_counts(self, *, report_type: Optional[str] = None) -> Dict[str, int]:
        return self._adapter._status_counts(report_type=report_type)


class FakeAdapter:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        # Each row represents a join of manual_reviews with news_summaries fields
        self.rows = rows
        self.manual_reviews = FakeManualReviewsNamespace(self)
        self.submission_archive = FakeSubmissionArchiveNamespace()
        for row in self.rows:
            if not row.get("report_type"):
                row["report_type"] = "zongbao"

    @staticmethod
    def _normalized_report_type(value: Optional[str]) -> str:
        normalized = (value or "zongbao").strip().lower()
        return normalized if normalized in ("zongbao", "wanbao") else "zongbao"

    # ------------------------------------------------------------------
    # Manual review helpers
    # ------------------------------------------------------------------
    def _fetch(
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
        target_type = (
            self._normalized_report_type(report_type)
            if report_type is not None
            else None
        )
        filtered = [
            row
            for row in self.rows
            if row.get("status") == status
            and (
                target_type is None
                or self._normalized_report_type(row.get("report_type"))
                == target_type
            )
        ]
        if only_ready:
            filtered = [row for row in filtered if row.get("news_status") == "ready_for_export"]
        if region in ("internal", "external"):
            target = True if region == "internal" else False
            filtered = [row for row in filtered if row.get("is_beijing_related") is target]
        if sentiment in ("positive", "negative"):
            filtered = [row for row in filtered if (row.get("sentiment_label") or "").lower() == sentiment]
        if duty_unprocessed_only:
            filtered = [row for row in filtered if not row.get("duty_processed")]
        normalized_query = (query or "").strip().lower()
        if normalized_query:
            filtered = [
                row
                for row in filtered
                if normalized_query
                in " ".join(
                    [
                        str(row.get("title") or "").lower(),
                        str(row.get("llm_summary") or "").lower(),
                        str(row.get("content_markdown") or "").lower(),
                    ]
                )
            ]
        filtered.sort(
            key=lambda r: (
                r.get("rank") is None,
                r.get("rank") or 0,
                -(r.get("score") or 0),
                r.get("publish_time_iso") or "",
                r.get("article_id") or "",
            )
        )
        total = len(filtered)
        return filtered[offset : offset + limit], total

    def _fetch_pending_for_cluster(
        self,
        *,
        region: Optional[str] = None,
        sentiment: Optional[str] = None,
        fetch_limit: int = 5000,
        report_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rows, _ = self._fetch(
            status="pending",
            limit=fetch_limit,
            offset=0,
            only_ready=True,
            region=region,
            sentiment=sentiment,
            report_type=None,
        )
        return rows

    @staticmethod
    def _bucket_key_for_row(row: Mapping[str, Any]) -> str:
        region = "internal" if row.get("is_beijing_related") else "external"
        sentiment = "negative" if (row.get("sentiment_label") or "").lower() == "negative" else "positive"
        return f"{region}_{sentiment}"

    def _fetch_clusters(
        self,
        *,
        bucket_key: Optional[str] = None,
        duty_unprocessed_only: bool = False,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for row in self.rows:
            row_bucket = self._bucket_key_for_row(row)
            if bucket_key and row_bucket != bucket_key:
                continue
            if row.get("status") != "pending" or row.get("news_status") != "ready_for_export":
                continue
            if duty_unprocessed_only and row.get("duty_processed"):
                continue
            cluster_row = dict(row)
            cluster_row["bucket_key"] = row_bucket
            cluster_row["cluster_id"] = row.get("cluster_id") or f"{row_bucket}-{row.get('article_id')}"
            cluster_row["manual_rank"] = row.get("rank")
            rows.append(cluster_row)
        return rows

    @staticmethod
    def _created_local_date(row: Mapping[str, Any]) -> Optional[date]:
        created_at = row.get("created_at")
        if created_at is None:
            return None
        try:
            value = str(created_at).replace("Z", "+00:00")
            return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Shanghai")).date()
        except ValueError:
            return None

    def _search_candidates(
        self,
        *,
        query: Optional[str] = None,
        created_before: Optional[date] = None,
        limit: int,
        offset: int,
        region: Optional[str] = None,
        sentiment: Optional[str] = None,
        report_type: Optional[str] = None,
        duty_unprocessed_only: bool = False,
    ) -> Tuple[List[Dict[str, Any]], int]:
        rows, _ = self._fetch(
            status="pending",
            limit=10_000,
            offset=0,
            only_ready=True,
            region=region,
            sentiment=sentiment,
            report_type=report_type,
            duty_unprocessed_only=duty_unprocessed_only,
        )
        normalized_query = (query or "").strip().lower()
        filtered = list(rows)
        if normalized_query:
            filtered = [
                row
                for row in filtered
                if normalized_query in " ".join(
                    [
                        str(row.get("title") or "").lower(),
                        str(row.get("llm_summary") or "").lower(),
                        str(row.get("content_markdown") or "").lower(),
                    ]
                )
            ]
        if created_before:
            filtered = [
                row
                for row in filtered
                if self._created_local_date(row) is not None and self._created_local_date(row) < created_before
            ]
        total = len(filtered)
        return filtered[offset : offset + limit], total

    def _status_counts(self, *, report_type: Optional[str] = None) -> Dict[str, int]:
        counts: Dict[str, int] = {"pending": 0, "selected": 0, "backup": 0, "discarded": 0, "exported": 0}
        target_type = self._normalized_report_type(report_type)
        for row in self.rows:
            status = row.get("status") or "pending"
            if (
                status in {"selected", "backup", "exported"}
                and self._normalized_report_type(row.get("report_type"))
                != target_type
            ):
                continue
            counts[status] = counts.get(status, 0) + 1
        return counts

    def manual_review_pending_count(self, *, report_type: Optional[str] = None) -> int:
        target_type = (
            self._normalized_report_type(report_type)
            if report_type is not None
            else None
        )
        return sum(
            1
            for row in self.rows
            if (row.get("status") or "pending") == "pending"
            and (
                target_type is None
                or self._normalized_report_type(row.get("report_type"))
                == target_type
            )
        )

@pytest.fixture()
def fake_adapter(monkeypatch):
    rows = [
        {
            "article_id": "a1",
            "title": "Internal Positive",
            "llm_summary": "llm",
            "manual_summary": None,
            "rank": None,
            "score": 90,
            "news_status": "ready_for_export",
            "status": "pending",
            "source": "src",
            "publish_time_iso": None,
            "publish_time": None,
            "created_at": "2025-01-01T00:00:00Z",
            "sentiment_label": "positive",
            "sentiment_confidence": 0.9,
            "is_beijing_related": True,
            "external_importance_score": 80,
            "decided_by": None,
            "decided_at": None,
            "content_markdown": "body",
            "url": "http://example.com/a1",
            "score_details": {"matched_rules": [{"label": "教育政策"}, {"rule_id": "rule-x"}]},
        },
        {
            "article_id": "a2",
            "title": "External Positive",
            "llm_summary": "llm2",
            "manual_summary": None,
            "rank": None,
            "score": 70,
            "news_status": "ready_for_export",
            "status": "pending",
            "source": "src2",
            "publish_time_iso": "2025-01-02T00:00:00Z",
            "publish_time": None,
            "created_at": "2025-01-02T00:00:00Z",
            "sentiment_label": "negative",
            "sentiment_confidence": 0.8,
            "is_beijing_related": False,
            "external_importance_score": 60,
            "decided_by": None,
            "decided_at": None,
            "content_markdown": "body2",
            "url": "http://example.com/a2",
            "score_details": {"matched_rules": []},
        },
    ]
    adapter = FakeAdapter(rows)
    # Patch get_adapter in all modules that use it
    from src.console import (
        manual_filter_cluster,
        manual_filter_query_service,
    )

    monkeypatch.setattr(manual_filter_cluster, "get_adapter", lambda: adapter)
    monkeypatch.setattr(manual_filter_query_service, "get_adapter", lambda: adapter)
    return adapter


def test_list_candidates_returns_pending_with_bonus(fake_adapter):
    result = manual_filter_service.list_candidates(limit=10, offset=0)
    assert result["total"] == 2
    assert len(result["items"]) == 2
    assert "教育政策" in result["items"][0]["bonus_keywords"]
    assert result["items"][0]["manual_status"] == "pending"


def test_candidates_ignore_report_type_for_pending_pool(fake_adapter):
    fake_adapter.rows[0]["report_type"] = "wanbao"

    zongbao = manual_filter_service.list_candidates(
        limit=10,
        offset=0,
        report_type="zongbao",
    )
    wanbao = manual_filter_service.list_candidates(
        limit=10,
        offset=0,
        report_type="wanbao",
    )

    assert [item["article_id"] for item in zongbao["items"]] == ["a1", "a2"]
    assert wanbao == zongbao


def test_discarded_list_ignores_report_type(fake_adapter):
    fake_adapter.rows[0].update(status="discarded", report_type="wanbao")
    fake_adapter.rows[1].update(status="discarded", report_type="zongbao")

    zongbao = manual_filter_service.list_discarded(
        limit=10,
        offset=0,
        report_type="zongbao",
    )
    wanbao = manual_filter_service.list_discarded(
        limit=10,
        offset=0,
        report_type="wanbao",
    )

    assert {item["article_id"] for item in zongbao["items"]} == {"a1", "a2"}
    assert wanbao == zongbao


def test_discarded_list_searches_shared_pool_and_paginates_matches(fake_adapter):
    fake_adapter.rows[0].update(status="discarded", report_type="wanbao")
    fake_adapter.rows[1].update(
        status="discarded",
        report_type="zongbao",
        content_markdown="Internal follow-up body",
    )

    result = manual_filter_service.list_discarded(
        limit=1,
        offset=1,
        report_type="zongbao",
        q="  Internal  ",
    )

    assert result["total"] == 2
    assert len(result["items"]) == 1
    assert result["limit"] == 1
    assert result["offset"] == 1


def test_pending_wrong_report_type_still_enters_clustering(fake_adapter):
    from src.console import manual_filter_cluster

    fake_adapter.rows[0]["report_type"] = "wanbao"

    records = manual_filter_cluster._collect_pending(
        None,
        None,
        adapter=fake_adapter,
    )

    assert {item["article_id"] for item in records} == {"a1", "a2"}


def test_status_counts_share_pending_and_discarded_across_reports(fake_adapter):
    fake_adapter.rows[0].update(status="pending", report_type="wanbao")
    fake_adapter.rows[1].update(status="discarded", report_type="zongbao")
    fake_adapter.rows.extend(
        [
            {
                **fake_adapter.rows[0],
                "article_id": "zongbao-selected",
                "status": "selected",
                "report_type": "zongbao",
            },
            {
                **fake_adapter.rows[0],
                "article_id": "wanbao-selected",
                "status": "selected",
                "report_type": "wanbao",
            },
        ]
    )

    zongbao = manual_filter_service.status_counts("zongbao")
    wanbao = manual_filter_service.status_counts("wanbao")

    assert zongbao["pending"] == wanbao["pending"] == 1
    assert zongbao["discarded"] == wanbao["discarded"] == 1
    assert zongbao["selected"] == wanbao["selected"] == 1


def test_list_candidates_serializes_current_score_feedback(fake_adapter):
    fake_adapter.rows[0].update(
        {
            "score_feedback_type": "too_high",
            "score_feedback_score_value": 80,
            "score_feedback_notes": "分数偏高",
            "score_feedback_submitted_by": "editor",
            "score_feedback_submitted_by_user_id": "editor-id",
            "score_feedback_submitted_by_display_name": "值班编辑",
            "score_feedback_updated_at": "2026-07-22T10:00:00Z",
        }
    )

    result = manual_filter_service.list_candidates(limit=10, offset=0)

    assert result["items"][0]["score_feedback"] == {
        "feedback_type": "too_high",
        "score_value": 80,
        "notes": "分数偏高",
        "submitted_by": "editor",
        "submitted_by_user_id": "editor-id",
        "submitted_by_display_name": "值班编辑",
        "updated_at": "2026-07-22T10:00:00Z",
    }
    assert result["items"][1]["score_feedback"] is None


def test_list_review_serializes_manual_summary_and_bonus_keywords(fake_adapter):
    fake_adapter.rows[0].update(
        status="selected",
        manual_summary="edited",
        rank=1.0,
    )
    review = manual_filter_service.list_review("selected", limit=10, offset=0)
    assert review["items"][0]["summary"] == "edited"
    assert review["items"][0]["bonus_keywords"]  # still present


def test_list_review_filters_by_report_type(fake_adapter):
    fake_adapter.rows[0].update(
        status="selected",
        rank=1.0,
        report_type="zongbao",
    )
    fake_adapter.rows.append(
        {
            "article_id": "a3",
            "title": "Wanbao Only",
            "llm_summary": "wb",
            "manual_summary": None,
            "rank": 1,
            "score": 50,
            "news_status": "ready_for_export",
            "status": "selected",
            "source": "src3",
            "publish_time_iso": "2025-01-03T00:00:00Z",
            "publish_time": None,
            "sentiment_label": "negative",
            "sentiment_confidence": 0.7,
            "is_beijing_related": False,
            "external_importance_score": 40,
            "decided_by": None,
            "decided_at": None,
            "content_markdown": "body3",
            "url": "http://example.com/a3",
            "score_details": {"matched_rules": []},
            "report_type": "wanbao",
        }
    )
    zb_review = manual_filter_service.list_review("selected", limit=10, offset=0, report_type="zongbao")
    wb_review = manual_filter_service.list_review("selected", limit=10, offset=0, report_type="wanbao")
    assert [item["article_id"] for item in zb_review["items"]] == ["a1"]
    assert [item["article_id"] for item in wb_review["items"]] == ["a3"]


def test_list_discarded_returns_discarded_rows(fake_adapter):
    for row in fake_adapter.rows:
        row["status"] = "discarded"
    discarded = manual_filter_service.list_discarded(limit=10, offset=0)
    assert discarded["total"] == 2


def test_list_candidates_search_mode_returns_flat_items(fake_adapter):
    result = manual_filter_service.list_candidates(
        limit=10,
        offset=0,
        region="internal",
        sentiment="positive",
        cluster=True,
        q="Internal",
    )
    assert result["view_mode"] == "search"
    assert result["total"] == 1
    assert "clusters" not in result
    assert [item["article_id"] for item in result["items"]] == ["a1"]
    assert "content_markdown" not in result["items"][0]


def test_list_candidates_cluster_mode_returns_item_total(fake_adapter):
    fake_adapter.rows[0]["cluster_id"] = "internal_positive-0"
    fake_adapter.rows.extend(
        [
            {
                "article_id": "a3",
                "title": "Internal Positive Similar",
                "llm_summary": "llm3",
                "manual_summary": None,
                "rank": None,
                "score": 85,
                "news_status": "ready_for_export",
                "status": "pending",
                "source": "src3",
                "publish_time_iso": "2025-01-03T00:00:00Z",
                "publish_time": None,
                "sentiment_label": "positive",
                "sentiment_confidence": 0.7,
                "is_beijing_related": True,
                "external_importance_score": 75,
                "decided_by": None,
                "decided_at": None,
                "content_markdown": "body3",
                "url": "http://example.com/a3",
                "score_details": {"matched_rules": []},
                "cluster_id": "internal_positive-0",
            },
            {
                "article_id": "a4",
                "title": "Internal Positive Other",
                "llm_summary": "llm4",
                "manual_summary": None,
                "rank": None,
                "score": 65,
                "news_status": "ready_for_export",
                "status": "pending",
                "source": "src4",
                "publish_time_iso": "2025-01-04T00:00:00Z",
                "publish_time": None,
                "sentiment_label": "positive",
                "sentiment_confidence": 0.7,
                "is_beijing_related": True,
                "external_importance_score": 55,
                "decided_by": None,
                "decided_at": None,
                "content_markdown": "body4",
                "url": "http://example.com/a4",
                "score_details": {"matched_rules": []},
                "cluster_id": "internal_positive-1",
            },
        ]
    )

    result = manual_filter_service.list_candidates(
        limit=10,
        offset=0,
        region="internal",
        sentiment="positive",
        cluster=True,
    )

    assert result["total"] == 2
    assert result["item_total"] == 3
    assert sum(cluster["size"] for cluster in result["clusters"]) == 3
    assert all(
        "content_markdown" not in item
        for cluster in result["clusters"]
        for item in cluster["items"]
    )


def test_duty_unprocessed_filter_keeps_flat_search_and_cluster_counts_consistent(
    fake_adapter,
):
    base = dict(fake_adapter.rows[0])
    fake_adapter.rows[:] = [
        {
            **base,
            "article_id": "processed-mixed",
            "title": "Processed higher rank",
            "score": 100,
            "external_importance_score": 100,
            "cluster_id": "mixed",
            "duty_processed": True,
        },
        {
            **base,
            "article_id": "unprocessed-mixed",
            "title": "Unprocessed representative",
            "score": 70,
            "external_importance_score": 70,
            "cluster_id": "mixed",
            "duty_processed": False,
        },
        {
            **base,
            "article_id": "processed-only-1",
            "title": "Processed only one",
            "cluster_id": "fully-processed",
            "duty_processed": True,
        },
        {
            **base,
            "article_id": "processed-only-2",
            "title": "Processed only two",
            "cluster_id": "fully-processed",
            "duty_processed": True,
        },
    ]

    flat = manual_filter_service.list_candidates(
        limit=10,
        offset=0,
        duty_unprocessed_only=True,
    )
    clustered = manual_filter_service.list_candidates(
        limit=10,
        offset=0,
        cluster=True,
        duty_unprocessed_only=True,
    )
    searched = manual_filter_service.list_candidates(
        limit=10,
        offset=0,
        q="Unprocessed",
        duty_unprocessed_only=True,
    )

    assert flat["total"] == clustered["item_total"] == searched["total"] == 1
    assert [item["article_id"] for item in flat["items"]] == ["unprocessed-mixed"]
    assert [item["article_id"] for item in searched["items"]] == ["unprocessed-mixed"]
    assert clustered["total"] == 1
    assert clustered["clusters"][0]["cluster_id"] == "mixed"
    assert clustered["clusters"][0]["size"] == 1
    assert clustered["clusters"][0]["representative_title"] == "Unprocessed representative"


def test_duty_unprocessed_filter_defaults_to_false(fake_adapter):
    fake_adapter.rows[0]["duty_processed"] = True

    result = manual_filter_service.list_candidates(limit=10, offset=0)

    assert result["total"] == 2
    assert {item["article_id"] for item in result["items"]} == {"a1", "a2"}


def test_list_candidates_search_mode_uses_shanghai_calendar_day(fake_adapter):
    fake_adapter.rows.append(
        {
            "article_id": "a3",
            "title": "Boundary Case",
            "llm_summary": "boundary",
            "manual_summary": None,
            "rank": None,
            "score": 55,
            "news_status": "ready_for_export",
            "status": "pending",
            "source": "src3",
            "publish_time_iso": "2024-12-31T16:30:00Z",
            "publish_time": None,
            "created_at": "2024-12-31T16:30:00Z",
            "sentiment_label": "positive",
            "sentiment_confidence": 0.6,
            "is_beijing_related": True,
            "external_importance_score": 45,
            "decided_by": None,
            "decided_at": None,
            "content_markdown": "body3",
            "url": "http://example.com/a3",
            "score_details": {"matched_rules": []},
        }
    )
    result = manual_filter_service.list_candidates(
        limit=10,
        offset=0,
        region="internal",
        sentiment="positive",
        created_before=date(2025, 1, 1),
    )
    assert result["view_mode"] == "search"
    assert [item["article_id"] for item in result["items"]] == []
