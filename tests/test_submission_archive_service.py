from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from src.console import submission_archive_service


class FakeSubmissionArchiveAdapter:
    def __init__(self, *, conflict: dict[str, Any] | None = None) -> None:
        self.conflict = conflict
        self.link_results: list[dict[str, Any]] = []
        self.created = False

    def find_submitted_report_conflict(self, **kwargs: Any) -> dict[str, Any] | None:
        return self.conflict

    def create_submitted_report(
        self,
        *,
        report: dict[str, Any],
        items: list[dict[str, Any]],
        replace_report_id: str | None,
    ) -> dict[str, Any]:
        self.created = True
        return {
            "id": "report-id",
            **report,
            "items": [
                {"id": f"item-{index}", **item}
                for index, item in enumerate(items)
            ],
            "item_count": len(items),
        }

    def fetch_submission_link_candidates(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "article_id": "article-1",
                "title": "学校发布招生新规",
                "body": "学校发布招生新规正文",
            }
        ]

    def update_submission_link_results(
        self,
        results: list[dict[str, Any]],
    ) -> None:
        self.link_results = results

    def fetch_submitted_report(self, report_id: str) -> dict[str, Any]:
        return {"id": report_id, "items": []}


def test_create_report_normalizes_and_links_items(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = FakeSubmissionArchiveAdapter()
    monkeypatch.setattr(submission_archive_service, "get_adapter", lambda: adapter)

    result = submission_archive_service.create_report(
        report_type="zongbao",
        report_date=date(2026, 7, 28),
        compiled_date=date(2026, 7, 28),
        issue_no="第1期",
        title_line="首都教育每日舆情综报",
        pasted_text="原始全文",
        items=[
            {
                "title": "学校发布招生新规！",
                "body": "学校发布招生新规正文",
                "source": "北京日报",
                "urls": [],
            }
        ],
        overwrite=False,
    )

    assert adapter.created is True
    assert result["link_summary"]["exact"] == 1
    assert adapter.link_results[0]["article_id"] == "article-1"
    assert adapter.link_results[0]["status"] == "exact"


def test_create_report_requires_explicit_overwrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeSubmissionArchiveAdapter(
        conflict={"id": "existing", "report_type": "wanbao"}
    )
    monkeypatch.setattr(submission_archive_service, "get_adapter", lambda: adapter)

    with pytest.raises(submission_archive_service.SubmissionReportConflictError):
        submission_archive_service.create_report(
            report_type="wanbao",
            report_date=date(2026, 7, 28),
            compiled_date=date(2026, 7, 27),
            issue_no="总第1期",
            title_line="首都教育舆情",
            pasted_text="原始全文",
            items=[{"title": "条目", "body": "正文"}],
            overwrite=False,
        )

    assert adapter.created is False


def test_attach_duplicate_badges_uses_one_batch_lookup() -> None:
    class BadgeAdapter:
        def fetch_submission_duplicate_badges(
            self,
            article_ids: list[str],
        ) -> dict[str, dict[str, Any]]:
            assert article_ids == ["a", "b"]
            return {"a": {"has_confirmed": True, "matches": []}}

    items = [{"article_id": "a"}, {"article_id": "b"}]
    submission_archive_service.attach_duplicate_badges(
        items,
        adapter=BadgeAdapter(),
    )

    assert items[0]["submission_duplicate"]["has_confirmed"] is True
    assert items[1]["submission_duplicate"] is None


def test_create_report_rejects_non_http_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeSubmissionArchiveAdapter()
    monkeypatch.setattr(submission_archive_service, "get_adapter", lambda: adapter)

    with pytest.raises(ValueError, match=r"HTTP\(S\)"):
        submission_archive_service.create_report(
            report_type="feedback",
            report_date=date(2026, 7, 28),
            compiled_date=date(2026, 7, 27),
            issue_no=None,
            title_line="首都教育舆情",
            pasted_text="原始全文",
            items=[
                {
                    "title": "条目",
                    "body": "正文",
                    "urls": ["javascript:alert(1)"],
                }
            ],
            overwrite=False,
        )
