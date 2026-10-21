from __future__ import annotations

from datetime import date
from typing import Any, Optional

import pytest

from src.console import preview_header_service

ZONGBAO_TITLE = "首都教育每日舆情综报"
WANBAO_TITLE = "首都教育舆情"


def _report(
    report_type: str,
    report_date: str,
    compiled_date: str,
    issue_no: Optional[str],
    title_line: Optional[str],
) -> dict[str, Any]:
    return {
        "report_type": report_type,
        "report_date": date.fromisoformat(report_date),
        "compiled_date": date.fromisoformat(compiled_date),
        "issue_no": issue_no,
        "title_line": title_line,
    }


class FakeSubmissionArchive:
    def __init__(self, adapter: "FakeAdapter") -> None:
        self._adapter = adapter

    def fetch_reports(
        self,
        *,
        report_type: Optional[str],
        date_from: Optional[date],
        date_to: Optional[date],
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        del date_from, date_to, offset
        rows = [
            row
            for row in self._adapter.rows
            if report_type is None or row["report_type"] == report_type
        ]
        return rows[:limit], len(rows)


class FakeAdapter:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.submission_archive = FakeSubmissionArchive(self)


@pytest.fixture()
def rows() -> list[dict[str, Any]]:
    # 模拟真实归档：新数据在前（fetch_reports 按 report_date desc 返回），
    # 底部混入验收测试记录，必须被跳过。
    return [
        _report("wanbao", "2026-10-21", "2026-10-20", "总第4847期", WANBAO_TITLE),
        _report("wanbao", "2026-10-20", "2026-10-19", "总第4846期", WANBAO_TITLE),
        _report("wanbao", "2026-09-02", "2026-09-02", "总第9002期(验收测试)", "首都教育晚报(验收测试)"),
        _report("zongbao", "2026-10-20", "2026-10-20", "2026年第573期（总第3851期）", ZONGBAO_TITLE),
        _report("zongbao", "2026-10-19", "2026-10-19", "2026年第572期（总第3850期）", ZONGBAO_TITLE),
    ]


def _install(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]) -> None:
    adapter = FakeAdapter(rows)
    monkeypatch.setattr(preview_header_service, "get_adapter", lambda: adapter)


def test_admin_wanbao_preview_uses_previous_day_as_issue_date(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
) -> None:
    _install(monkeypatch, rows)

    result = preview_header_service.build_preview_header(
        report_type="wanbao",
        viewer="admin",
        today=date(2026, 10, 22),
    )

    # 10/22 报送的那一期取材日是 10/21；基准取材日 10/20 → +1
    assert result["lines"] == [
        WANBAO_TITLE,
        "总第4848期",
        "2026年10月22日",
    ]
    assert result["issue_date"] == date(2026, 10, 21)
    assert result["day_offset"] == 1
    assert result["notices"] == []


def test_duty_wanbao_preview_uses_today_as_issue_date(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
) -> None:
    _install(monkeypatch, rows)

    result = preview_header_service.build_preview_header(
        report_type="wanbao",
        viewer="duty",
        today=date(2026, 10, 21),
    )

    # 值班编辑 10/21 晚上做稿，日期行写制作日 10/21；基准取材日 10/20 → +1
    assert result["lines"] == [
        WANBAO_TITLE,
        "总第4848期",
        "2026年10月21日",
    ]
    assert result["issue_date"] == date(2026, 10, 21)


def test_monday_preview_skips_weekend_issues(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
) -> None:
    # 基准停在周五 10/16 的 4842（取材日 10/15），周一 10/19 报送
    monday_rows = [
        _report("wanbao", "2026-10-16", "2026-10-15", "总第4842期", WANBAO_TITLE),
    ]
    _install(monkeypatch, monday_rows)

    result = preview_header_service.build_preview_header(
        report_type="wanbao",
        viewer="admin",
        today=date(2026, 10, 19),
    )

    assert result["lines"][1] == "总第4845期"
    assert result["day_offset"] == 3


def test_zongbao_preview_uses_today_for_both_roles(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
) -> None:
    _install(monkeypatch, rows)

    admin = preview_header_service.build_preview_header(
        report_type="zongbao",
        viewer="admin",
        today=date(2026, 10, 21),
    )
    duty = preview_header_service.build_preview_header(
        report_type="zongbao",
        viewer="duty",
        today=date(2026, 10, 21),
    )

    assert admin["lines"] == duty["lines"] == [
        ZONGBAO_TITLE,
        "2026年第574期（总第3852期）",
        "2026年10月21日",
    ]
    assert admin["issue_date"] == date(2026, 10, 21)


def test_test_data_and_wrong_titles_are_skipped(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
) -> None:
    # 最新两条是脏数据，基准必须落到 10/21 的 4847
    dirty_rows = [
        _report("wanbao", "2026-10-25", "2026-10-25", "总第9003期", "首都教育晚报(验收测试)"),
        _report("wanbao", "2026-10-24", "2026-10-24", "总第9004期(验收测试)", WANBAO_TITLE),
        *rows,
    ]
    _install(monkeypatch, dirty_rows)

    result = preview_header_service.build_preview_header(
        report_type="wanbao",
        viewer="admin",
        today=date(2026, 10, 22),
    )

    assert result["baseline"]["issue_no"] == "总第4847期"
    assert result["lines"][1] == "总第4848期"


def test_missing_baseline_reports_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [])

    result = preview_header_service.build_preview_header(
        report_type="wanbao",
        viewer="admin",
        today=date(2026, 10, 22),
    )

    assert result["available"] is False
    assert result["lines"] == []
    assert result["notices"]


def test_same_day_issue_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        [_report("wanbao", "2026-10-21", "2026-10-20", "总第4847期", WANBAO_TITLE)],
    )

    # 10/21 上午已报送过 10/21 那期（取材日 10/20），下午再点预览
    result = preview_header_service.build_preview_header(
        report_type="wanbao",
        viewer="admin",
        today=date(2026, 10, 21),
    )

    assert result["day_offset"] == 0
    assert result["lines"][1] == "总第4847期"
    assert any("同一期" in notice for notice in result["notices"])


def test_stale_baseline_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        [_report("wanbao", "2026-09-30", "2026-09-29", "总第4830期", WANBAO_TITLE)],
    )

    result = preview_header_service.build_preview_header(
        report_type="wanbao",
        viewer="admin",
        today=date(2026, 10, 22),
    )

    assert any("未录入新存档" in notice for notice in result["notices"])


def test_unsupported_report_type_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [])

    with pytest.raises(ValueError):
        preview_header_service.build_preview_header(
            report_type="feedback",
            viewer="admin",
            today=date(2026, 10, 22),
        )
