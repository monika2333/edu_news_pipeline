from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import pytest

from src.console.services import manual_filter


class FakeAdapter:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        # Each row represents a join of manual_reviews with news_summaries fields
        self.rows = rows
        self.export_calls: List[Dict[str, Any]] = []

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
    ) -> Tuple[List[Dict[str, Any]], int]:
        filtered = [row for row in self.rows if row.get("status") == status]
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
    ) -> List[Dict[str, Any]]:
        rows, _ = self.fetch_manual_reviews(
            status="pending",
            limit=fetch_limit,
            offset=0,
            only_ready=True,
            region=region,
            sentiment=sentiment,
        )
        return rows

    def manual_review_status_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {"pending": 0, "selected": 0, "backup": 0, "discarded": 0, "exported": 0}
        for row in self.rows:
            key = row.get("status") or "pending"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def manual_review_pending_count(self) -> int:
        return sum(1 for row in self.rows if (row.get("status") or "pending") == "pending")

    def manual_review_max_rank(self, status: str) -> float:
        ranks = [r.get("rank") for r in self.rows if r.get("status") == status and r.get("rank") is not None]
        if not ranks:
            return 0.0
        try:
            return float(max(ranks))
        except Exception:
            return 0.0

    def update_manual_review_statuses(self, updates: Sequence[Mapping[str, Any]]) -> int:
        updated = 0
        for item in updates:
            aid = str(item.get("article_id") or "")
            for row in self.rows:
                if str(row.get("article_id")) != aid:
                    continue
                row["status"] = item.get("status", row.get("status"))
                row["rank"] = item.get("rank", row.get("rank"))
                row["decided_by"] = item.get("decided_by") or row.get("decided_by")
                row["decided_at"] = item.get("decided_at") or row.get("decided_at")
                updated += 1
                break
        return updated

    def reset_manual_reviews_to_pending(
        self,
        article_ids: Sequence[str],
        *,
        actor: Optional[str] = None,
        decided_at: Optional[Any] = None,
    ) -> int:
        updates = []
        for aid in article_ids:
            updates.append(
                {
                    "article_id": aid,
                    "status": "pending",
                    "rank": None,
                    "decided_by": actor,
                    "decided_at": decided_at,
                }
            )
        return self.update_manual_review_statuses(updates)

    def update_manual_review_summaries(
        self,
        edits: Mapping[str, Mapping[str, Any]],
        *,
        actor: Optional[str] = None,
        decided_at: Optional[Any] = None,
    ) -> int:
        updated = 0
        for aid, edit in edits.items():
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
                updated += 1
                break
        return updated

    def fetch_manual_selected_for_export(self) -> List[Dict[str, Any]]:
        rows, _ = self.fetch_manual_reviews(status="selected", limit=10_000, offset=0)
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
            "sentiment_label": "positive",
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
    monkeypatch.setattr(manual_filter, "get_adapter", lambda: adapter)
    return adapter


def test_list_candidates_returns_pending_with_bonus(fake_adapter):
    result = manual_filter.list_candidates(limit=10, offset=0)
    assert result["total"] == 2
    assert len(result["items"]) == 2
    assert "教育政策" in result["items"][0]["bonus_keywords"]
    assert result["items"][0]["manual_status"] == "pending"


def test_bulk_decide_updates_states(fake_adapter):
    res = manual_filter.bulk_decide(
        selected_ids=["a1"],
        backup_ids=["a2"],
        discarded_ids=[],
        actor="tester",
    )
    assert res == {"selected": 1, "backup": 1, "discarded": 0, "pending": 0}
    status_map = {r["article_id"]: r["status"] for r in fake_adapter.rows}
    assert status_map == {"a1": "selected", "a2": "backup"}


def test_save_edits_and_review(fake_adapter):
    manual_filter.bulk_decide(selected_ids=["a1"], backup_ids=[], discarded_ids=[], actor=None)
    manual_filter.save_edits({"a1": {"summary": "edited"}}, actor="tester")
    review = manual_filter.list_review("selected", limit=10, offset=0)
    assert review["items"][0]["summary"] == "edited"
    assert review["items"][0]["bonus_keywords"]  # still present


def test_export_batch_writes_file_and_marks_exported(fake_adapter, tmp_path: Path):
    manual_filter.bulk_decide(selected_ids=["a1", "a2"], backup_ids=[], discarded_ids=[], actor=None)
    output_file = tmp_path / "out.txt"
    res = manual_filter.export_batch(report_tag="test", output_path=str(output_file))
    assert res["count"] == 2
    assert Path(res["output_path"]).exists()
    exported_status = {r["article_id"]: r["status"] for r in fake_adapter.rows}
    assert exported_status == {"a1": "exported", "a2": "exported"}
    assert fake_adapter.export_calls, "record_export should be invoked"


def test_reset_to_pending_and_discarded_listing(fake_adapter):
    manual_filter.bulk_decide(selected_ids=[], backup_ids=[], discarded_ids=["a1", "a2"], actor=None)
    discarded = manual_filter.list_discarded(limit=10, offset=0)
    assert discarded["total"] == 2
    updated = manual_filter.reset_to_pending(["a1"])
    assert updated == 1
    status_map = {r["article_id"]: r["status"] for r in fake_adapter.rows}
    assert status_map["a1"] == "pending"
