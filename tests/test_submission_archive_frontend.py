from __future__ import annotations

from pathlib import Path


def test_status_poll_updates_components_without_rerendering_report() -> None:
    source = Path(
        "src/console/web_static/js/submission_archive/browser.js"
    ).read_text(encoding="utf-8")
    poll_body = source.split(
        "async function pollReportStatus(id) {",
        maxsplit=1,
    )[1].split(
        "async function selectReport(id, pushUrl = true) {",
        maxsplit=1,
    )[0]

    assert "updateReportStatusComponents(id, items)" in poll_body
    assert "selectReport(" not in poll_body
    assert "loadReportList(" not in poll_body
