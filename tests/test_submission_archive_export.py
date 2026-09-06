from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any, Optional, Sequence

import pytest

from src.adapters import db_postgres_submission_archive
from src.console import submission_archive_service
from src.console.submission_archive_export import (
    CSV_HEADERS,
    MAX_EXPORT_ROWS,
    REPORT_TYPE_LABELS,
    build_content_disposition,
    build_csv_bytes,
    build_export_filenames,
)
from src.domain.report_type import SUBMISSION_DOC_TYPES


class FakeCursor:
    def __init__(
        self,
        *,
        rows: Optional[list[dict[str, Any]]] = None,
        row: Optional[dict[str, Any]] = None,
    ) -> None:
        self.rows = rows or []
        self.row = row
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.calls.append((query, params))

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows

    def fetchone(self) -> Optional[dict[str, Any]]:
        return self.row


def _row(
    report_type: str = "zongbao",
    *,
    body: str = "正文",
    issue_no: Optional[str] = "总第1期",
    section: Optional[str] = "重点关注",
    source: Optional[str] = "测试来源",
    urls: Optional[list[str]] = None,
) -> dict[str, Any]:
    return {
        "report_type": report_type,
        "report_date": date(2026, 1, 2),
        "compiled_date": date(2026, 1, 1),
        "issue_no": issue_no,
        "order_index": 3,
        "section": section,
        "title": "测试标题",
        "body": body,
        "source": source,
        "urls": ["https://example.com/a"] if urls is None else urls,
    }


def _read_csv(content: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))


def test_csv_has_bom_exact_headers_and_complete_report_type_labels() -> None:
    content = build_csv_bytes(
        [_row("zongbao"), _row("wanbao"), _row("feedback")]
    )

    assert content.startswith(b"\xef\xbb\xbf")
    parsed = _read_csv(content)
    assert parsed[0] == list(CSV_HEADERS)
    assert [row[0] for row in parsed[1:]] == ["综报", "晚报", "反馈"]
    assert set(REPORT_TYPE_LABELS) == SUBMISSION_DOC_TYPES


def test_csv_round_trip_normalizes_body_newlines_and_preserves_csv_text() -> None:
    body = '第一段\r\n含逗号,和英文双引号"\r第三段\n结束'

    parsed = _read_csv(build_csv_bytes([_row(body=body)]))

    assert parsed[1][7] == '第一段\n含逗号,和英文双引号"\n第三段\n结束'


def test_csv_outputs_empty_values_and_joins_urls_with_one_space() -> None:
    parsed = _read_csv(
        build_csv_bytes(
            [
                _row(
                    issue_no=None,
                    section=None,
                    source=None,
                    urls=["https://example.com/a", "https://example.com/b"],
                ),
                _row(urls=[]),
            ]
        )
    )

    assert parsed[1][3] == ""
    assert parsed[1][5] == ""
    assert parsed[1][8] == ""
    assert parsed[1][9] == "https://example.com/a https://example.com/b"
    assert parsed[2][9] == ""


def test_empty_csv_contains_only_the_header() -> None:
    assert _read_csv(build_csv_bytes([])) == [list(CSV_HEADERS)]


@pytest.mark.parametrize(
    ("date_from", "date_to", "expected"),
    [
        (
            date(2026, 1, 1),
            date(2026, 3, 31),
            ("报送存档_20260101-20260331.csv", "submission_archive_20260101-20260331.csv"),
        ),
        (
            date(2026, 1, 1),
            None,
            ("报送存档_20260101-.csv", "submission_archive_20260101-.csv"),
        ),
        (
            None,
            date(2026, 3, 31),
            ("报送存档_-20260331.csv", "submission_archive_-20260331.csv"),
        ),
        (None, None, ("报送存档_全部.csv", "submission_archive_all.csv")),
    ],
)
def test_export_filename_date_ranges(
    date_from: Optional[date],
    date_to: Optional[date],
    expected: tuple[str, str],
) -> None:
    assert build_export_filenames(date_from, date_to) == expected
    disposition = build_content_disposition(date_from, date_to)
    assert f'filename="{expected[1]}"' in disposition
    assert "filename*=UTF-8''" in disposition


def test_fetch_export_rows_uses_parameterized_filters_and_stable_sort() -> None:
    cursor = FakeCursor(rows=[_row()])

    rows = db_postgres_submission_archive.fetch_export_rows(
        cursor,
        date_from=date(2026, 1, 1),
        date_to=date(2026, 3, 31),
        report_types=("zongbao", "wanbao"),
        limit=MAX_EXPORT_ROWS + 1,
    )

    assert rows == [_row()]
    query, params = cursor.calls[0]
    normalized_sql = " ".join(query.split())
    assert "r.report_date >= %s" in normalized_sql
    assert "r.report_date <= %s" in normalized_sql
    assert "r.report_type = any(%s)" in normalized_sql
    assert (
        "order by r.report_date, r.report_type, i.order_index, i.id"
        in normalized_sql
    )
    assert params == (
        date(2026, 1, 1),
        date(2026, 3, 31),
        ["zongbao", "wanbao"],
        MAX_EXPORT_ROWS + 1,
    )


def test_count_export_rows_reuses_export_filter_semantics() -> None:
    cursor = FakeCursor(row={"report_count": 2, "item_count": 7})

    counts = db_postgres_submission_archive.count_export_rows(
        cursor,
        date_from=date(2026, 1, 1),
        date_to=None,
        report_types=("feedback",),
    )

    assert counts == (2, 7)
    query, params = cursor.calls[0]
    normalized_sql = " ".join(query.split())
    assert "count(distinct r.id) as report_count" in normalized_sql
    assert "count(i.id) as item_count" in normalized_sql
    assert "r.report_date >= %s" in normalized_sql
    assert "r.report_type = any(%s)" in normalized_sql
    assert params == (date(2026, 1, 1), ["feedback"])


class ExportNamespace:
    def __init__(self, rows: Sequence[dict[str, Any]]) -> None:
        self.rows = list(rows)
        self.fetch_calls: list[dict[str, Any]] = []
        self.count_calls: list[dict[str, Any]] = []

    def fetch_export_rows(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.fetch_calls.append(kwargs)
        return self.rows

    def count_export_rows(self, **kwargs: Any) -> tuple[int, int]:
        self.count_calls.append(kwargs)
        return 2, len(self.rows)


class ExportAdapter:
    def __init__(self, rows: Sequence[dict[str, Any]]) -> None:
        self.submission_archive = ExportNamespace(rows)


def test_service_treats_missing_and_empty_report_types_as_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ExportAdapter([])
    monkeypatch.setattr(submission_archive_service, "get_adapter", lambda: adapter)

    submission_archive_service.export_items(
        date_from=None,
        date_to=None,
        report_types=None,
    )
    submission_archive_service.export_items(
        date_from=None,
        date_to=None,
        report_types=[""],
    )

    assert [call["report_types"] for call in adapter.submission_archive.fetch_calls] == [
        None,
        None,
    ]


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "date_from": date(2026, 4, 1),
            "date_to": date(2026, 3, 31),
            "report_types": None,
        },
        {"date_from": None, "date_to": None, "report_types": ["invalid"]},
    ],
)
def test_service_rejects_invalid_export_filters(
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, Any],
) -> None:
    adapter = ExportAdapter([])
    monkeypatch.setattr(submission_archive_service, "get_adapter", lambda: adapter)

    with pytest.raises(ValueError):
        submission_archive_service.export_items(**kwargs)

    assert adapter.submission_archive.fetch_calls == []


def test_service_rejects_export_above_row_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ExportAdapter([{}] * (MAX_EXPORT_ROWS + 1))
    monkeypatch.setattr(submission_archive_service, "get_adapter", lambda: adapter)

    with pytest.raises(ValueError, match="请收窄日期范围"):
        submission_archive_service.export_items(
            date_from=None,
            date_to=None,
            report_types=None,
        )

    assert adapter.submission_archive.fetch_calls[0]["limit"] == MAX_EXPORT_ROWS + 1


def test_preview_matches_export_item_count_and_exposes_backend_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_row("zongbao"), _row("wanbao")]
    adapter = ExportAdapter(rows)
    monkeypatch.setattr(submission_archive_service, "get_adapter", lambda: adapter)

    preview = submission_archive_service.preview_export(
        date_from=date(2026, 1, 1),
        date_to=date(2026, 3, 31),
        report_types=["zongbao", "wanbao"],
    )
    content, _disposition = submission_archive_service.export_items(
        date_from=date(2026, 1, 1),
        date_to=date(2026, 3, 31),
        report_types=["zongbao", "wanbao"],
    )

    assert preview == {
        "report_count": 2,
        "item_count": len(_read_csv(content)) - 1,
        "max_rows": MAX_EXPORT_ROWS,
    }
    assert adapter.submission_archive.count_calls[0]["report_types"] == (
        "zongbao",
        "wanbao",
    )
