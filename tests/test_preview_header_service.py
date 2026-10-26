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
        del offset
        rows = [
            row
            for row in self._adapter.rows
            if (report_type is None or row["report_type"] == report_type)
            and (date_from is None or row["report_date"] >= date_from)
            and (date_to is None or row["report_date"] <= date_to)
        ]
        # 与真实 adapter 一致：按 report_date desc 返回
        rows.sort(key=lambda row: row["report_date"], reverse=True)
        return rows[:limit], len(rows)


class FakeAdapter:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.submission_archive = FakeSubmissionArchive(self)


@pytest.fixture()
def rows() -> list[dict[str, Any]]:
    # 模拟真实归档：底部混入验收测试记录，必须被跳过。
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


def test_archived_report_of_that_day_wins(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
) -> None:
    """该报送日已有归档时，直接用它那一期的真实期号（历史班次口径）。"""
    _install(monkeypatch, rows)

    result = preview_header_service.build_preview_header(
        report_type="wanbao",
        business_date=date(2026, 10, 21),
    )

    assert result["source"] == "archived"
    assert result["lines"] == [WANBAO_TITLE, "总第4847期", "2026年10月21日"]
    assert result["issue_date"] == date(2026, 10, 20)
    assert result["notices"] == []


def test_archived_lookup_matches_title_and_issue_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同日但标题不符/期号不可解析的记录不算归档。"""
    dirty = [
        _report("wanbao", "2026-10-21", "2026-10-20", "总第9003期", "首都教育晚报(验收测试)"),
        _report("wanbao", "2026-10-21", "2026-10-20", "总第9004期(验收测试)", WANBAO_TITLE),
        _report("wanbao", "2026-10-20", "2026-10-19", "总第4846期", WANBAO_TITLE),
    ]
    _install(monkeypatch, dirty)

    result = preview_header_service.build_preview_header(
        report_type="wanbao",
        business_date=date(2026, 10, 21),
    )

    # 10/21 没有可用归档 → 按公式从 10/20 那期（取材 10/19）推算：天数差 1
    assert result["source"] == "computed"
    assert result["lines"][1] == "总第4847期"
    assert result["day_offset"] == 1


def test_wanbao_issue_uses_previous_day_as_issue_date(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
) -> None:
    """没有归档时：晚报取材日 = 报送日 − 1，日期行仍写报送日。"""
    _install(monkeypatch, rows)

    result = preview_header_service.build_preview_header(
        report_type="wanbao",
        business_date=date(2026, 10, 22),
    )

    assert result["source"] == "computed"
    assert result["lines"] == [WANBAO_TITLE, "总第4848期", "2026年10月22日"]
    assert result["issue_date"] == date(2026, 10, 21)
    assert result["day_offset"] == 1


def test_zongbao_uses_business_date_as_issue_date(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
) -> None:
    _install(monkeypatch, rows)

    result = preview_header_service.build_preview_header(
        report_type="zongbao",
        business_date=date(2026, 10, 21),
    )

    assert result["lines"] == [
        ZONGBAO_TITLE,
        "2026年第574期（总第3852期）",
        "2026年10月21日",
    ]


def test_historical_date_can_be_reconstructed_backwards(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
) -> None:
    """历史班次的目标日早于最近归档：天数差为负，按同一公式往回推。"""
    _install(monkeypatch, rows)

    result = preview_header_service.build_preview_header(
        report_type="wanbao",
        business_date=date(2026, 10, 18),
    )

    # 基准取材日 10/20，目标取材日 10/17 → 差 −3 → 4847 − 3 = 4844
    assert result["source"] == "computed"
    assert result["day_offset"] == -3
    assert result["lines"] == [WANBAO_TITLE, "总第4844期", "2026年10月18日"]


def test_monday_preview_skips_weekend_issues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monday_rows = [
        _report("wanbao", "2026-10-16", "2026-10-15", "总第4842期", WANBAO_TITLE),
    ]
    _install(monkeypatch, monday_rows)

    result = preview_header_service.build_preview_header(
        report_type="wanbao",
        business_date=date(2026, 10, 19),
    )

    assert result["lines"][1] == "总第4845期"
    assert result["day_offset"] == 3


def test_missing_baseline_reports_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [])

    result = preview_header_service.build_preview_header(
        report_type="wanbao",
        business_date=date(2026, 10, 22),
    )

    assert result["available"] is False
    assert result["lines"] == []
    assert result["notices"]


def test_notices_only_cover_missing_baseline(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
) -> None:
    """「同一期 / 基准过旧」两类提示已删除：正常路径不带任何提示。"""
    _install(monkeypatch, rows)

    for business_date in (date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 18)):
        for report_type in ("zongbao", "wanbao"):
            result = preview_header_service.build_preview_header(
                report_type=report_type,
                business_date=business_date,
            )
            assert result["notices"] == [], (report_type, business_date)


def test_unsupported_report_type_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [])

    with pytest.raises(ValueError):
        preview_header_service.build_preview_header(
            report_type="feedback",
            business_date=date(2026, 10, 22),
        )
