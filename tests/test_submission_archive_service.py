from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from src.console import submission_archive_service
from src.console.auth_service import ConsoleUser
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


def _editor() -> ConsoleUser:
    return ConsoleUser(
        method="test",
        user_id="editor-id",
        username="editor",
        display_name="值班编辑",
        role="duty_editor",
    )


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

    assert summary["matched"] == 1
    assert adapter.title_fetch_count == 1
    assert adapter.body_fetch_calls == [["article-1"]]
    assert adapter.link_results[0]["article_id"] == "article-1"
    assert adapter.link_results[0]["status"] == "matched"


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
    adapter.report["items"][0]["link_status"] = "matched"

    summary = submission_archive_processing.process_report_links("report-id")

    assert summary == {
        "matched": 0,
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


def test_fetch_prior_item_match_details_returns_matches() -> None:
    class DetailsSubmissionArchiveNamespace:
        def fetch_item_duplicate_match_details(
            self,
            item_id: str,
        ) -> list[dict[str, Any]]:
            assert item_id == "item-1"
            return [
                {
                    "prior_item_id": "prior-1",
                    "title": "更早条目",
                    "match_method": "article",
                }
            ]

    class DetailsAdapter:
        def __init__(self) -> None:
            self.submission_archive = DetailsSubmissionArchiveNamespace()

    result = submission_archive_service.fetch_prior_item_match_details(
        " item-1 ",
        adapter=DetailsAdapter(),
    )

    assert result["matches"][0]["prior_item_id"] == "prior-1"


@pytest.mark.parametrize(
    ("report_type", "completed_at", "expected_pending"),
    [
        ("feedback", None, True),
        ("feedback", "2026-09-03T12:00:00+08:00", False),
        ("zongbao", None, False),
    ],
)
def test_get_report_exposes_prior_match_pending(
    monkeypatch: pytest.MonkeyPatch,
    report_type: str,
    completed_at: str | None,
    expected_pending: bool,
) -> None:
    adapter = FakeSubmissionArchiveAdapter()
    adapter.report = {
        "id": "report-id",
        "report_type": report_type,
        "prior_match_completed_at": completed_at,
        "items": [],
    }
    monkeypatch.setattr(submission_archive_service, "get_adapter", lambda: adapter)

    report = submission_archive_service.get_report("report-id")

    assert report["prior_match_pending"] is expected_pending


def test_list_pending_links_passes_report_filter_and_keeps_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    pending_item = {
        "id": "item-1",
        "report_id": "report-1",
        "candidate_title": "系统候选标题",
        "candidate_body": "系统候选正文",
        "candidate_source": "北京日报",
        "candidate_url": "https://example.com/article-1",
        "report_type": "zongbao",
        "report_date": date(2026, 9, 5),
    }

    class PendingLinksNamespace:
        def fetch_pending_links(
            self,
            **kwargs: Any,
        ) -> tuple[list[dict[str, Any]], int]:
            captured.update(kwargs)
            return [pending_item], 3

    class PendingLinksAdapter:
        def __init__(self) -> None:
            self.submission_archive = PendingLinksNamespace()

    monkeypatch.setattr(
        submission_archive_service,
        "get_adapter",
        PendingLinksAdapter,
    )

    result = submission_archive_service.list_pending_links(
        limit=1,
        offset=2,
        report_id="report-1",
    )

    assert captured == {
        "limit": 1,
        "offset": 2,
        "report_id": "report-1",
    }
    assert result == {
        "items": [pending_item],
        "total": 3,
        "limit": 1,
        "offset": 2,
    }


def test_decide_prior_match_passes_business_user_and_returns_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    prior_match = {
        "status": "dismissed",
        "decidable": True,
        "decision": "not_submitted",
        "top_similarity": 0.87,
        "count": 2,
    }

    class DecisionNamespace:
        def set_item_prior_match_decision(
            self,
            **kwargs: Any,
        ) -> dict[str, Any]:
            captured.update(kwargs)
            return {"state": "updated", "prior_match": prior_match}

    class DecisionAdapter:
        def __init__(self) -> None:
            self.submission_archive = DecisionNamespace()

    monkeypatch.setattr(submission_archive_service, "get_adapter", DecisionAdapter)

    result = submission_archive_service.decide_prior_match(
        item_id=" item-1 ",
        decision="not_submitted",
        user=_editor(),
    )

    assert result == {"item_id": "item-1", "prior_match": prior_match}
    assert captured == {
        "item_id": "item-1",
        "decision": "not_submitted",
        "actor_user_id": "editor-id",
    }


@pytest.mark.parametrize(
    ("state", "error_type"),
    [
        ("not_found", submission_archive_service.SubmissionReportNotFoundError),
        ("not_decidable", ValueError),
    ],
)
def test_decide_prior_match_maps_adapter_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    error_type: type[Exception],
) -> None:
    class DecisionNamespace:
        def set_item_prior_match_decision(
            self,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            return {"state": state, "prior_match": None}

    class DecisionAdapter:
        def __init__(self) -> None:
            self.submission_archive = DecisionNamespace()

    monkeypatch.setattr(submission_archive_service, "get_adapter", DecisionAdapter)

    with pytest.raises(error_type):
        submission_archive_service.decide_prior_match(
            item_id="item-1",
            decision="submitted",
            user=_editor(),
        )


def test_decide_prior_match_rejects_unknown_decision() -> None:
    with pytest.raises(ValueError, match="不支持"):
        submission_archive_service.decide_prior_match(
            item_id="item-1",
            decision="maybe",
            user=_editor(),
        )


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


def test_search_link_candidates_normalizes_query_and_returns_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class CandidateNamespace:
        def fetch_manual_link_candidates(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {
                "item": {"id": "item-1"},
                "items": [{"article_id": "article-1"}],
                "window_start": date(2026, 7, 25),
                "window_end": date(2026, 8, 24),
                "has_more": False,
            }

    class CandidateAdapter:
        def __init__(self) -> None:
            self.submission_archive = CandidateNamespace()

    monkeypatch.setattr(
        submission_archive_service,
        "get_adapter",
        CandidateAdapter,
    )

    result = submission_archive_service.search_link_candidates(
        item_id="item-1",
        query="  招生  ",
        window_days=15,
        limit=20,
        offset=0,
    )

    assert captured["query"] == "招生"
    assert result["window_days"] == 15
    assert result["window_start"] == date(2026, 7, 25)
    assert "total" not in result


def test_search_link_candidates_rejects_blank_query() -> None:
    with pytest.raises(ValueError, match="q"):
        submission_archive_service.search_link_candidates(
            item_id="item-1",
            query="   ",
            window_days=15,
            limit=20,
            offset=0,
        )


def test_manual_link_passes_business_user_and_returns_updated_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    updated = {
        "article_id": "article-manual",
        "link_status": "matched",
        "best_candidate_article_id": "article-auto",
        "link_title_score": 0.7,
        "link_body_score": 0.6,
        "link_combined_score": 0.65,
    }

    class ManualNamespace:
        def manual_link_item(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {"state": "updated", "item": updated}

    class ManualAdapter:
        def __init__(self) -> None:
            self.submission_archive = ManualNamespace()

    monkeypatch.setattr(submission_archive_service, "get_adapter", ManualAdapter)

    result = submission_archive_service.manual_link_item(
        item_id="item-1",
        article_id=" article-manual ",
        user=_editor(),
    )

    assert result == updated
    assert captured == {
        "item_id": "item-1",
        "article_id": "article-manual",
        "actor_user_id": "editor-id",
    }


@pytest.mark.parametrize(
    ("state", "error_type"),
    [
        ("not_found", submission_archive_service.SubmissionReportNotFoundError),
        ("processing", submission_archive_service.SubmissionLinkProcessingError),
        ("article_not_found", ValueError),
    ],
)
def test_manual_link_maps_adapter_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    error_type: type[Exception],
) -> None:
    class ManualNamespace:
        def manual_link_item(self, **_kwargs: Any) -> dict[str, Any]:
            return {"state": state, "item": None}

    class ManualAdapter:
        def __init__(self) -> None:
            self.submission_archive = ManualNamespace()

    monkeypatch.setattr(submission_archive_service, "get_adapter", ManualAdapter)

    with pytest.raises(error_type):
        submission_archive_service.manual_link_item(
            item_id="item-1",
            article_id="article-1",
            user=_editor(),
        )


def test_manual_unlink_returns_updated_unmatched_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updated = {"article_id": None, "link_status": "unmatched"}

    class ManualNamespace:
        def manual_unlink_item(self, **kwargs: Any) -> dict[str, Any]:
            assert kwargs == {
                "item_id": "item-1",
                "actor_user_id": "editor-id",
            }
            return {"state": "updated", "item": updated}

    class ManualAdapter:
        def __init__(self) -> None:
            self.submission_archive = ManualNamespace()

    monkeypatch.setattr(submission_archive_service, "get_adapter", ManualAdapter)

    assert submission_archive_service.manual_unlink_item(
        item_id="item-1",
        user=_editor(),
    ) == updated


def test_update_item_fields_normalizes_and_recomputes_norm_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    updated = {"id": "item-1", "title": "新标题", "link_status": "matched"}

    class UpdateNamespace:
        def update_item_fields(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {"state": "updated", "item": updated}

    class UpdateAdapter:
        def __init__(self) -> None:
            self.submission_archive = UpdateNamespace()

    monkeypatch.setattr(submission_archive_service, "get_adapter", UpdateAdapter)

    result = submission_archive_service.update_item_fields(
        item_id="item-1",
        title="  新标题  ",
        body="  新正文  ",
        source="  北京日报  ",
        urls=[" https://example.com/a ", "  "],
    )

    assert result == updated
    assert captured["item_id"] == "item-1"
    assert captured["title"] == "新标题"
    assert captured["body"] == "新正文"
    assert captured["source"] == "北京日报"
    assert captured["urls"] == ["https://example.com/a"]
    assert captured["norm_title"]
    assert captured["norm_title_hash"]


def test_update_item_fields_rejects_blank_title() -> None:
    with pytest.raises(ValueError, match="标题"):
        submission_archive_service.update_item_fields(
            item_id="item-1",
            title="   ",
            body="",
            source=None,
            urls=[],
        )


def test_update_item_fields_rejects_non_http_urls() -> None:
    with pytest.raises(ValueError, match=r"HTTP\(S\)"):
        submission_archive_service.update_item_fields(
            item_id="item-1",
            title="条目",
            body="",
            source=None,
            urls=["javascript:alert(1)"],
        )


@pytest.mark.parametrize(
    ("state", "error_type"),
    [
        ("not_found", submission_archive_service.SubmissionReportNotFoundError),
        ("processing", submission_archive_service.SubmissionLinkProcessingError),
    ],
)
def test_update_item_fields_maps_adapter_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    error_type: type[Exception],
) -> None:
    class UpdateNamespace:
        def update_item_fields(self, **_kwargs: Any) -> dict[str, Any]:
            return {"state": state, "item": None}

    class UpdateAdapter:
        def __init__(self) -> None:
            self.submission_archive = UpdateNamespace()

    monkeypatch.setattr(submission_archive_service, "get_adapter", UpdateAdapter)

    with pytest.raises(error_type):
        submission_archive_service.update_item_fields(
            item_id="item-1",
            title="条目",
            body="",
            source=None,
            urls=[],
        )
