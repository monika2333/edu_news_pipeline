from __future__ import annotations

from typing import Any, Optional

from src.console import admin_summary_service


class FakeAdminSummaryAdapter:
    def __init__(self) -> None:
        self.review_query: dict[str, Any] = {}

    def fetch_duty_shift(self, shift_id: str) -> dict[str, str]:
        return {"id": shift_id}

    def fetch_shift_review_items(
        self,
        *,
        shift_id: str,
        decision: Optional[str],
        report_type: Optional[str],
        limit: int,
        offset: int,
        mismatch_only: bool,
        include_admin_state: bool,
    ) -> tuple[list[dict[str, Any]], int]:
        self.review_query = {
            "shift_id": shift_id,
            "decision": decision,
            "report_type": report_type,
            "limit": limit,
            "offset": offset,
            "mismatch_only": mismatch_only,
            "include_admin_state": include_admin_state,
        }
        return [
            {
                "article_id": "article-1",
                "decision": "backup",
                "report_type": "wanbao",
                "version": 2,
                "title": "测试新闻",
                "llm_summary": "机器摘要",
                "score_details": {},
                "admin_status": "selected",
                "admin_report_type": "zongbao",
                "admin_version": 4,
            }
        ], 1


def test_shift_results_requests_and_returns_admin_mismatch_state(
    monkeypatch,
) -> None:
    adapter = FakeAdminSummaryAdapter()
    monkeypatch.setattr(admin_summary_service, "get_adapter", lambda: adapter)

    result = admin_summary_service.list_shift_results(
        shift_id="shift-1",
        decision=None,
        report_type=None,
        mismatch_only=True,
        limit=200,
        offset=0,
    )

    assert adapter.review_query["mismatch_only"] is True
    assert adapter.review_query["include_admin_state"] is True
    assert result["items"][0]["admin_status"] == "selected"
    assert result["items"][0]["admin_report_type"] == "zongbao"
