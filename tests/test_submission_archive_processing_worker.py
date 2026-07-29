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
