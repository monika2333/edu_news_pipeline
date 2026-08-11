from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from src.console import submission_archive_service
from src.workers import submission_archive_processing


class FakeSubmissionArchiveNamespace:
    def __init__(self, adapter: FakeSubmissionArchiveAdapter) -> None:
        self._adapter = adapter

    def find_report_conflict(self, **kwargs: Any) -> dict[str, Any] | None:
        del kwargs
        return self._adapter.conflict

    def create_report(
        self,
        *,
        report: dict[str, Any],
        items: list[dict[str, Any]],
        replace_report_id: str | None,
    ) -> dict[str, Any]:
        self._adapter.created = True
        self._adapter.report = {
            "id": "report-id",
            **report,
            "items": [
                {
                    "id": f"item-{index}",
                    "link_status": "processing",
                    **item,
                }
                for index, item in enumerate(items)
            ],
            "item_count": len(items),
        }
        return self._adapter.report

    def fetch_link_candidate_titles(
        self,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        self._adapter.title_fetch_count += 1
        return self._adapter.candidate_titles

    def fetch_link_candidate_bodies(
        self,
        *,
        article_ids: list[str],
    ) -> list[dict[str, Any]]:
        self._adapter.body_fetch_calls.append(article_ids)
        return [
            {
                "article_id": article_id,
                "body": self._adapter.candidate_bodies.get(article_id, ""),
            }
            for article_id in article_ids
        ]

    def update_link_results(
        self,
        results: list[dict[str, Any]],
    ) -> None:
        self._adapter.link_results = results

    def fetch_report(self, report_id: str) -> dict[str, Any]:
        assert self._adapter.report is not None
        assert report_id == self._adapter.report["id"]
        return self._adapter.report


class FakeSubmissionArchiveAdapter:
    def __init__(self, *, conflict: dict[str, Any] | None = None) -> None:
        self.conflict = conflict
        self.link_results: list[dict[str, Any]] = []
        self.report: dict[str, Any] | None = None
        self.candidate_titles = [
            {
                "article_id": "article-1",
                "title": "学校发布招生新规",
            }
        ]
        self.candidate_bodies = {
            "article-1": "学校发布招生新规正文",
        }
        self.title_fetch_count = 0
        self.body_fetch_calls: list[list[str]] = []
        self.created = False
        self.submission_archive = FakeSubmissionArchiveNamespace(self)


def test_create_report_saves_before_processing_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeSubmissionArchiveAdapter()
    monkeypatch.setattr(submission_archive_service, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        submission_archive_processing,
        "get_adapter",
        lambda: adapter,
    )

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
    assert result["link_summary"] == {"processing": 1}
    assert adapter.link_results == []

    summary = submission_archive_processing.process_report_links("report-id")

    assert summary["exact"] == 1
    assert adapter.title_fetch_count == 1
    assert adapter.body_fetch_calls == [["article-1"]]
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


def test_process_report_links_does_not_overwrite_finished_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeSubmissionArchiveAdapter()
    monkeypatch.setattr(submission_archive_service, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        submission_archive_processing,
        "get_adapter",
        lambda: adapter,
    )
    submission_archive_service.create_report(
        report_type="zongbao",
        report_date=date(2026, 7, 28),
        compiled_date=date(2026, 7, 28),
        issue_no=None,
        title_line=None,
        pasted_text="原始全文",
        items=[{"title": "已人工确认条目", "body": "正文"}],
        overwrite=False,
    )
    assert adapter.report is not None
    adapter.report["items"][0]["link_status"] = "manual"

    summary = submission_archive_processing.process_report_links("report-id")

    assert summary == {
        "exact": 0,
        "fuzzy": 0,
        "pending": 0,
        "unmatched": 0,
    }
    assert adapter.title_fetch_count == 0
    assert adapter.body_fetch_calls == []
    assert adapter.link_results == []


def test_process_report_links_fetches_one_body_batch_for_all_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeSubmissionArchiveAdapter()
    adapter.candidate_titles = [
        {
            "article_id": f"article-{index}",
            "title": f"候选新闻标题 {index}",
        }
        for index in range(30)
    ]
    adapter.candidate_bodies = {
        f"article-{index}": f"候选新闻正文 {index}"
        for index in range(30)
    }
    monkeypatch.setattr(submission_archive_service, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        submission_archive_processing,
        "get_adapter",
        lambda: adapter,
    )
    submission_archive_service.create_report(
        report_type="zongbao",
        report_date=date(2026, 7, 28),
        compiled_date=date(2026, 7, 28),
        issue_no=None,
        title_line=None,
        pasted_text="原始全文",
        items=[
            {"title": "候选新闻标题 1", "body": "候选新闻正文 1"},
            {"title": "另一条不同内容", "body": "另一条正文"},
        ],
        overwrite=False,
    )

    submission_archive_processing.process_report_links("report-id")

    assert adapter.title_fetch_count == 1
    assert len(adapter.body_fetch_calls) == 1
    assert len(adapter.body_fetch_calls[0]) <= 21


def test_attach_duplicate_badges_uses_one_batch_lookup() -> None:
    class BadgeSubmissionArchiveNamespace:
        def fetch_duplicate_badges(
            self,
            article_ids: list[str],
        ) -> dict[str, dict[str, Any]]:
            assert article_ids == ["a", "b"]
            return {"a": {"has_confirmed": True, "matches": []}}

    class BadgeAdapter:
        def __init__(self) -> None:
            self.submission_archive = BadgeSubmissionArchiveNamespace()

    items = [{"article_id": "a"}, {"article_id": "b"}]
    submission_archive_service.attach_duplicate_badges(
        items,
        adapter=BadgeAdapter(),
    )

    assert items[0]["submission_duplicate"]["has_confirmed"] is True
    assert items[1]["submission_duplicate"] is None


def test_fetch_duplicate_details_requires_article_id() -> None:
    with pytest.raises(ValueError, match="article_id"):
        submission_archive_service.fetch_duplicate_details("  ")


def test_fetch_duplicate_details_returns_matches() -> None:
    class DetailsSubmissionArchiveNamespace:
        def fetch_duplicate_match_details(
            self,
            article_id: str,
        ) -> list[dict[str, Any]]:
            assert article_id == "article-1"
            return [{"title": "条目", "body": "报送稿正文"}]

    class DetailsAdapter:
        def __init__(self) -> None:
            self.submission_archive = DetailsSubmissionArchiveNamespace()

    result = submission_archive_service.fetch_duplicate_details(
        " article-1 ",
        adapter=DetailsAdapter(),
    )

    assert result == {"matches": [{"title": "条目", "body": "报送稿正文"}]}


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
