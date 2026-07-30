from __future__ import annotations

from typing import Any

import pytest

from src.workers import submission_archive_processing


def test_launch_submission_report_processing_uses_child_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeProcess:
        pid = 4321

    def fake_popen(
        command: list[str],
        **kwargs: Any,
    ) -> FakeProcess:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(
        submission_archive_processing.subprocess,
        "Popen",
        fake_popen,
    )

    pid = submission_archive_processing.launch_submission_report_processing(
        "report-id"
    )

    assert pid == 4321
    assert captured["command"][-2:] == [
        "src.workers.submission_archive_processing",
        "report-id",
    ]
    assert captured["kwargs"]["cwd"] == (
        submission_archive_processing._REPO_ROOT
    )


def test_process_submission_report_only_processes_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {
        "exact": 1,
        "fuzzy": 2,
        "pending": 3,
        "unmatched": 4,
    }
    monkeypatch.setattr(
        submission_archive_processing,
        "process_report_links",
        lambda report_id: expected,
    )

    result = submission_archive_processing.process_submission_report(
        "report-id"
    )

    assert result == expected
    assert "embedded" not in result
