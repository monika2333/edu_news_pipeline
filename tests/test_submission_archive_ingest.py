from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from src.workers import submission_archive_ingest


class FakeSubmissionArchiveNamespace:
    def __init__(self) -> None:
        self.existing: dict[str, Any] | None = None
        self.conflict: dict[str, Any] | None = None
        self.created_report: dict[str, Any] | None = None

    def fetch_report_by_source_message(self, **_kwargs: Any) -> dict[str, Any] | None:
        return self.existing

    def find_report_conflict(self, **_kwargs: Any) -> dict[str, Any] | None:
        return self.conflict

    def create_report_idempotent(
        self,
        *,
        report: dict[str, Any],
        items: list[dict[str, Any]],
        replace_report_id: str | None,
    ) -> tuple[dict[str, Any], bool]:
        assert replace_report_id is None
        self.created_report = {
            "id": "report-new",
            **report,
            "items": [
                {"id": "item-1", "link_status": "processing", **items[0]}
            ],
        }
        return self.created_report, True


class FakeAdapter:
    def __init__(self) -> None:
        self.submission_archive = FakeSubmissionArchiveNamespace()


REPORT_TEXT = """首都教育舆情
总第1期
2026年8月21日
【舆情速览】
一、测试条目
正文（北京日报）"""


def test_create_report_from_text_persists_feishu_audit_fields() -> None:
    adapter = FakeAdapter()

    result = submission_archive_ingest.create_report_from_text(
        REPORT_TEXT,
        ingest_source="feishu",
        source_message_id="om_1",
        source_sender_id="ou_owner",
        adapter=adapter,
    )

    assert result["created"] is True
    assert result["warnings"] == []
    assert adapter.submission_archive.created_report is not None
    report = adapter.submission_archive.created_report
    assert report["ingest_source"] == "feishu"
    assert report["source_message_id"] == "om_1"
    assert report["source_sender_id"] == "ou_owner"
    assert report["report_date"] == date(2026, 8, 21)


def test_duplicate_source_message_returns_existing_before_date_conflict() -> None:
    adapter = FakeAdapter()
    adapter.submission_archive.existing = {
        "id": "report-existing",
        "items": [{"link_status": "matched"}],
    }
    adapter.submission_archive.conflict = {"id": "other-conflict"}

    result = submission_archive_ingest.create_report_from_text(
        REPORT_TEXT,
        ingest_source="feishu",
        source_message_id="om_1",
        source_sender_id="ou_owner",
        adapter=adapter,
    )

    assert result["created"] is False
    assert result["report"]["id"] == "report-existing"
    assert adapter.submission_archive.created_report is None


def test_distinct_source_message_never_overwrites_same_date_report() -> None:
    adapter = FakeAdapter()
    adapter.submission_archive.conflict = {
        "id": "report-existing",
        "report_type": "wanbao",
        "report_date": date(2026, 8, 21),
    }

    with pytest.raises(submission_archive_ingest.SubmissionReportConflictError):
        submission_archive_ingest.create_report_from_text(
            REPORT_TEXT,
            ingest_source="feishu",
            source_message_id="om_2",
            source_sender_id="ou_owner",
            adapter=adapter,
        )
