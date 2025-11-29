from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pytest

from src.console.services import manual_filter


class FakeCursor:
    def __init__(self, adapter: "FakeAdapter") -> None:
        self.adapter = adapter
        self._results: List[Dict[str, Any]] = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query: str, params: Sequence[Any] = ()):
        q = query.lower()
        if "count(*)" in q:
            if "manual_status = 'pending'" in q:
                filtered = [
                    row
                    for row in self.adapter.rows
                    if row["status"] == "ready_for_export" and row["manual_status"] == "pending"
                ]
                self._results = [{"total": len(filtered)}]
            elif "group by manual_status" in q:
                counts: Dict[str, int] = {}
                for row in self.adapter.rows:
                    key = row.get("manual_status") or "pending"
                    counts[key] = counts.get(key, 0) + 1
                self._results = [{"manual_status": k, "total": v} for k, v in counts.items()]
            else:
                self._results = [{"total": 0}]
            return
        if "from news_summaries" in q and "manual_status = 'pending'" in q and "limit" in q:
            limit, offset = params
            candidates = [
                row
                for row in self.adapter.rows
                if row["status"] == "ready_for_export" and row["manual_status"] == "pending"
            ]
            candidates.sort(key=lambda r: (-(r.get("score") or 0), r.get("article_id")))
            self._results = candidates[offset : offset + limit]
            return
        if "from news_summaries" in q and "manual_status = 'approved'" in q:
            approved = [
                row
                for row in self.adapter.rows
                if row["manual_status"] == "approved"
            ]
            approved.sort(key=lambda r: (-(r.get("score") or 0), r.get("article_id")))
            self._results = approved
            return
        self._results = []

    def executemany(self, query: str, params_seq: Iterable[Sequence[Any]]):
        q = query.lower()
        self.rowcount = 0
        if "set manual_status = %s" in q and "manual_summary" in q:
            # bulk_decide
            for status, summary, score, notes, actor, decided_at, aid in params_seq:
                updated = self.adapter._update_row(
                    aid,
                    manual_status=status,
                    manual_summary=summary if summary is not None else None,
                    manual_score=score if score is not None else None,
                    manual_notes=notes if notes is not None else None,
                    manual_decided_by=actor if actor is not None else None,
                    manual_decided_at=decided_at,
                )
                if updated:
                    self.rowcount += 1
        elif "set manual_status = 'pending'" in q:
            # reset_to_pending
            for actor, decided_at, aid in params_seq:
                updated = self.adapter._update_row(
                    aid,
                    manual_status="pending",
                    manual_decided_by=actor if actor is not None else None,
                    manual_decided_at=decided_at,
                )
                if updated:
                    self.rowcount += 1

    def fetchall(self):
        return list(self._results)

    def fetchone(self):
        return self._results[0] if self._results else None


class FakeAdapter:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows
        self.export_calls: List[Dict[str, Any]] = []

    def _cursor(self):
        return FakeCursor(self)

    @staticmethod
    def _article_hash(article_id: Optional[str], original_url: Optional[str], title: Optional[str]) -> str:
        return f"hash-{article_id or original_url or title}"

    def record_export(self, report_tag: str, exported, *, output_path: str):
        self.export_calls.append(
            {"tag": report_tag, "exported": list(exported), "output_path": output_path}
        )

    def _update_row(self, article_id: str, **updates: Any) -> bool:
        for row in self.rows:
            if str(row.get("article_id")) == str(article_id):
                row.update({k: v for k, v in updates.items() if v is not None or k == "manual_status"})
                return True
        return False


@pytest.fixture()
def fake_adapter(monkeypatch):
    rows = [
        {
            "article_id": "a1",
            "title": "Internal Positive",
            "llm_summary": "llm",
            "manual_summary": None,
            "score": 90,
            "manual_score": None,
            "status": "ready_for_export",
            "manual_status": "pending",
            "source": "src",
            "publish_time_iso": "2025-01-01T00:00:00Z",
            "publish_time": None,
            "sentiment_label": "positive",
            "sentiment_confidence": 0.9,
            "is_beijing_related": True,
            "external_importance_score": 80,
            "manual_notes": None,
            "manual_decided_by": None,
            "manual_decided_at": None,
            "content_markdown": "body",
            "url": "http://example.com/a1",
        },
        {
            "article_id": "a2",
            "title": "External Positive",
            "llm_summary": "llm2",
            "manual_summary": None,
            "score": 70,
            "manual_score": None,
            "status": "ready_for_export",
            "manual_status": "pending",
            "source": "src2",
            "publish_time_iso": "2025-01-02T00:00:00Z",
            "publish_time": None,
            "sentiment_label": "positive",
            "sentiment_confidence": 0.8,
            "is_beijing_related": False,
            "external_importance_score": 60,
            "manual_notes": None,
            "manual_decided_by": None,
            "manual_decided_at": None,
            "content_markdown": "body2",
            "url": "http://example.com/a2",
        },
    ]
    adapter = FakeAdapter(rows)
    monkeypatch.setattr(manual_filter, "get_adapter", lambda: adapter)
    return adapter


def test_list_candidates_returns_pending(fake_adapter):
    result = manual_filter.list_candidates(limit=10, offset=0)
    assert result["total"] == 2
    assert len(result["items"]) == 2
    assert result["items"][0]["summary"] == "llm"


def test_bulk_decide_updates_status_and_edits(fake_adapter):
    edits = {"a1": {"summary": "edited", "score": 88.0, "notes": "keep"}}
    res = manual_filter.bulk_decide(approved_ids=["a1"], discarded_ids=["a2"], edits=edits, actor="tester")
    assert res == {"approved": 1, "discarded": 1}
    row_a1 = next(r for r in fake_adapter.rows if r["article_id"] == "a1")
    row_a2 = next(r for r in fake_adapter.rows if r["article_id"] == "a2")
    assert row_a1["manual_status"] == "approved"
    assert row_a1["manual_summary"] == "edited"
    assert row_a1["manual_score"] == 88.0
    assert row_a1["manual_notes"] == "keep"
    assert row_a1["manual_decided_by"] == "tester"
    assert row_a2["manual_status"] == "discarded"


def test_export_batch_writes_file_and_marks_exported(fake_adapter, tmp_path: Path):
    # mark approved first
    manual_filter.bulk_decide(approved_ids=["a1", "a2"], discarded_ids=[], edits=None, actor=None)
    output_file = tmp_path / "out.txt"
    res = manual_filter.export_batch(report_tag="test", output_path=str(output_file))
    assert res["count"] == 2
    assert Path(res["output_path"]).exists()
    exported_status = {r["article_id"]: r["manual_status"] for r in fake_adapter.rows}
    assert exported_status == {"a1": "exported", "a2": "exported"}
    assert fake_adapter.export_calls, "record_export should be invoked"


def test_reset_to_pending(fake_adapter):
    manual_filter.bulk_decide(approved_ids=["a1"], discarded_ids=["a2"], edits=None, actor=None)
    updated = manual_filter.reset_to_pending(["a1", "a2"])
    assert updated == 2
    statuses = {r["article_id"]: r["manual_status"] for r in fake_adapter.rows}
    assert statuses == {"a1": "pending", "a2": "pending"}
