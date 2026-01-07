from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import pytest

from src.console import manual_filter_service


class FakeAdapter:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        # Each row represents a join of manual_reviews with news_summaries fields
        self.rows = rows
        self.export_calls: List[Dict[str, Any]] = []
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
        target_type = self._normalized_report_type(report_type)
        filtered = [
            row
            for row in self.rows
            if row.get("status") == status and self._normalized_report_type(row.get("report_type")) == target_type
        ]
        if only_ready:
            filtered = [row for row in filtered if row.get("news_status") == "ready_for_export"]
        if region in ("internal", "external"):
            target = True if region == "internal" else False
            filtered = [row for row in filtered if row.get("is_beijing_related") is target]
        if sentiment in ("positive", "negative"):
            filtered = [row for row in filtered if (row.get("sentiment_label") or "").lower() == sentiment]
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

    def fetch_manual_pending_for_cluster(
        self,
        *,
        region: Optional[str] = None,
        sentiment: Optional[str] = None,
        fetch_limit: int = 5000,
        report_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rows, _ = self.fetch_manual_reviews(
            status="pending",
            limit=fetch_limit,
            offset=0,
            only_ready=True,
            region=region,
            sentiment=sentiment,
            report_type=report_type,
        )
        return rows

    def manual_review_status_counts(self, *, report_type: Optional[str] = None) -> Dict[str, int]:
        counts: Dict[str, int] = {"pending": 0, "selected": 0, "backup": 0, "discarded": 0, "exported": 0}
        target_type = self._normalized_report_type(report_type)
        for row in self.rows:
            if self._normalized_report_type(row.get("report_type")) != target_type:
                continue
            key = row.get("status") or "pending"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def manual_review_pending_count(self, *, report_type: Optional[str] = None) -> int:
        target_type = self._normalized_report_type(report_type)
        return sum(
            1
            for row in self.rows
            if (row.get("status") or "pending") == "pending"
            and self._normalized_report_type(row.get("report_type")) == target_type
        )

    def manual_review_max_rank(self, status: str, *, report_type: Optional[str] = None) -> float:
        target_type = self._normalized_report_type(report_type)
        ranks = [
            r.get("rank")
            for r in self.rows
            if r.get("status") == status
            and r.get("rank") is not None
            and self._normalized_report_type(r.get("report_type")) == target_type
        ]
        if not ranks:
            return 0.0
        try:
            return float(max(ranks))
        except Exception:
            return 0.0

    def update_manual_review_statuses(self, updates: Sequence[Mapping[str, Any]], *, report_type: Optional[str] = None) -> int:
        default_report_type = self._normalized_report_type(report_type)
        updated = 0
        for item in updates:
            aid = str(item.get("article_id") or "")
            target_type = self._normalized_report_type(item.get("report_type") or default_report_type)
            for row in self.rows:
                if str(row.get("article_id")) != aid:
                    continue
                row["status"] = item.get("status", row.get("status"))
                row["rank"] = item.get("rank", row.get("rank"))
                row["decided_by"] = item.get("decided_by") or row.get("decided_by")
                row["decided_at"] = item.get("decided_at") or row.get("decided_at")
                row["report_type"] = target_type
                updated += 1
                break
        return updated

    def reset_manual_reviews_to_pending(
        self,
        article_ids: Sequence[str],
        *,
        actor: Optional[str] = None,
        decided_at: Optional[Any] = None,
        report_type: Optional[str] = None,
    ) -> int:
        updates = []
        for aid in article_ids:
            updates.append(
                {
                    "article_id": aid,
                    "status": "pending",
                    "rank": None,
                    "report_type": report_type,
                    "decided_by": actor,
                    "decided_at": decided_at,
                }
            )
        return self.update_manual_review_statuses(updates, report_type=report_type)

    def update_manual_review_summaries(
        self,
        edits: Mapping[str, Mapping[str, Any]],
        *,
        actor: Optional[str] = None,
        decided_at: Optional[Any] = None,
        report_type: Optional[str] = None,
    ) -> int:
        updated = 0
        target_report_type = self._normalized_report_type(report_type)
        for aid, edit in edits.items():
            item_report_type = self._normalized_report_type(edit.get("report_type") or target_report_type)
            for row in self.rows:
                if str(row.get("article_id")) != str(aid):
                    continue
                if "summary" in edit:
                    row["manual_summary"] = edit.get("summary")
                if "notes" in edit:
                    row["manual_notes"] = edit.get("notes")
                if "score" in edit:
                    row["manual_score"] = edit.get("score")
                row["decided_by"] = actor or row.get("decided_by")
                row["decided_at"] = decided_at or row.get("decided_at")
                row["report_type"] = item_report_type
                updated += 1
                break
        return updated

    def fetch_manual_selected_for_export(self, *, report_type: Optional[str] = None) -> List[Dict[str, Any]]:
        rows, _ = self.fetch_manual_reviews(status="selected", limit=10_000, offset=0, report_type=report_type)
        return rows

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    @staticmethod
    def _article_hash(article_id: Optional[str], original_url: Optional[str], title: Optional[str]) -> str:
        return f"hash-{article_id or original_url or title}"

    def record_export(self, report_tag: str, exported, *, output_path: str):
        self.export_calls.append(
            {"tag": report_tag, "exported": list(exported), "output_path": output_path}
        )

    def record_manual_export(self, report_tag: str, exported, *, output_path: str):
        self.record_export(report_tag, exported, output_path=output_path)


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
            "publish_time_iso": "2025-01-01T00:00:00Z",
            "publish_time": None,
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
    from src.console import manual_filter_cluster, manual_filter_export, manual_filter_decisions
    monkeypatch.setattr(manual_filter_service, "get_adapter", lambda: adapter)
    monkeypatch.setattr(manual_filter_cluster, "get_adapter", lambda: adapter)
    monkeypatch.setattr(manual_filter_export, "get_adapter", lambda: adapter)
    monkeypatch.setattr(manual_filter_decisions, "get_adapter", lambda: adapter)
    return adapter


@pytest.fixture(autouse=True)
def override_export_meta_path(monkeypatch, tmp_path: Path):
    meta_path = tmp_path / "export_meta.json"
    # Patch EXPORT_META_PATH in all modules that use it
    from src.console import manual_filter_helpers, manual_filter_export
    monkeypatch.setattr(manual_filter_service, "EXPORT_META_PATH", meta_path)
    monkeypatch.setattr(manual_filter_helpers, "EXPORT_META_PATH", meta_path)
    monkeypatch.setattr(manual_filter_export, "EXPORT_META_PATH", meta_path)
    yield


def test_list_candidates_returns_pending_with_bonus(fake_adapter):
    result = manual_filter_service.list_candidates(limit=10, offset=0)
    assert result["total"] == 2
    assert len(result["items"]) == 2
    assert "教育政策" in result["items"][0]["bonus_keywords"]
    assert result["items"][0]["manual_status"] == "pending"


def test_bulk_decide_updates_states(fake_adapter):
    res = manual_filter_service.bulk_decide(
        selected_ids=["a1"],
        backup_ids=["a2"],
        discarded_ids=[],
        actor="tester",
    )
    assert res == {"selected": 1, "backup": 1, "discarded": 0, "pending": 0}
    status_map = {r["article_id"]: r["status"] for r in fake_adapter.rows}
    assert status_map == {"a1": "selected", "a2": "backup"}


def test_save_edits_and_review(fake_adapter):
    manual_filter_service.bulk_decide(selected_ids=["a1"], backup_ids=[], discarded_ids=[], actor=None)
    manual_filter_service.save_edits({"a1": {"summary": "edited"}}, actor="tester")
    review = manual_filter_service.list_review("selected", limit=10, offset=0)
    assert review["items"][0]["summary"] == "edited"
    assert review["items"][0]["bonus_keywords"]  # still present


def test_export_batch_writes_file_and_marks_exported(fake_adapter, tmp_path: Path):
    manual_filter_service.bulk_decide(selected_ids=["a1", "a2"], backup_ids=[], discarded_ids=[], actor=None)
    output_file = tmp_path / "out.txt"
    res = manual_filter_service.export_batch(report_tag="test", output_path=str(output_file))
    assert res["count"] == 2
    assert Path(res["output_path"]).exists()
    exported_status = {r["article_id"]: r["status"] for r in fake_adapter.rows}
    assert exported_status == {"a1": "exported", "a2": "exported"}
    assert fake_adapter.export_calls, "record_export should be invoked"


def test_report_type_filters_and_meta(fake_adapter, tmp_path: Path):
    manual_filter_service.bulk_decide(selected_ids=["a1"], backup_ids=[], discarded_ids=[], actor=None, report_type="zongbao")
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

    zb_output = tmp_path / "zb.txt"
    wb_output = tmp_path / "wb.txt"
    manual_filter_service.export_batch(
        report_tag="zb",
        output_path=str(zb_output),
        report_type="zongbao",
        template="zongbao",
    )
    manual_filter_service.export_batch(
        report_tag="wb",
        output_path=str(wb_output),
        report_type="wanbao",
        template="wanbao",
    )
    meta = json.loads(manual_filter_service.EXPORT_META_PATH.read_text(encoding="utf-8"))
    assert "zongbao" in meta and "wanbao" in meta
    assert "zongbao" in meta["zongbao"] and "wanbao" in meta["wanbao"]


def test_reset_to_pending_and_discarded_listing(fake_adapter):
    manual_filter_service.bulk_decide(selected_ids=[], backup_ids=[], discarded_ids=["a1", "a2"], actor=None)
    discarded = manual_filter_service.list_discarded(limit=10, offset=0)
    assert discarded["total"] == 2
    updated = manual_filter_service.reset_to_pending(["a1"])
    assert updated == 1
    status_map = {r["article_id"]: r["status"] for r in fake_adapter.rows}
    assert status_map["a1"] == "pending"
