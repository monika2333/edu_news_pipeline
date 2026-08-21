from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

_DATE_PATTERN = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
_SECTION_PATTERN = re.compile(r"^【(.+?)】$")
_SYMBOL_ITEM_PATTERN = re.compile(r"^([★■▲])\s*(.+)$")
_NUMBER_ITEM_PATTERN = re.compile(
    r"^([一二三四五六七八九十百]+、)\s*(.+)$"
)
_SOURCE_GROUP_PATTERN = re.compile(r"（([^（）]*)）\s*$")
_URL_PATTERN = re.compile(r"https?://[^\s、）]+")
_LEADING_MARKER_PATTERN = re.compile(
    r"^\s*(?:[★■▲]|[一二三四五六七八九十百]+、)\s*"
)
_SUPPORTED_TITLE_LINES = frozenset(
    {
        "首都教育每日舆情综报",
        "首都教育舆情",
    }
)


class SubmissionArchiveParseError(ValueError):
    """Raised when pasted report text cannot be parsed safely."""


@dataclass(slots=True)
class ParsedSubmissionItem:
    section: Optional[str]
    marker: Optional[str]
    order_index: int
    title: str
    body: str
    source: Optional[str]
    urls: list[str] = field(default_factory=list)
    norm_title: str = ""
    norm_title_hash: str = ""


@dataclass(slots=True)
class ParsedSubmissionReport:
    title_line: str
    issue_no: Optional[str]
    report_date: date
    compiled_date: date
    detected_report_type: str
    items: list[ParsedSubmissionItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def looks_like_submission_report(value: str) -> bool:
    """Return whether the first non-empty line is a supported report title."""
    first_line = next(
        (line.strip() for line in (value or "").splitlines() if line.strip()),
        "",
    )
    return first_line in _SUPPORTED_TITLE_LINES


def normalize_submission_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = _LEADING_MARKER_PATTERN.sub("", normalized)
    return "".join(
        char
        for char in normalized
        if unicodedata.category(char)[0] not in {"P", "S", "Z", "C"}
    )


def normalized_title_hash(value: str) -> str:
    normalized = normalize_submission_text(value)
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def detect_report_type(title_line: str, issue_no: Optional[str]) -> str:
    if "综报" in title_line:
        return "zongbao"
    if issue_no is not None:
        return "wanbao"
    return "feedback"


def default_compiled_date(report_type: str, report_date: date) -> date:
    if report_type == "zongbao":
        return report_date
    return report_date - timedelta(days=1)


def _extract_source(body: str) -> tuple[str, Optional[str], list[str]]:
    match = _SOURCE_GROUP_PATTERN.search(body)
    if match is None:
        return body.strip(), None, []
    group = match.group(1)
    urls = _URL_PATTERN.findall(group)
    remaining = _URL_PATTERN.sub(" ", group)
    source_parts = [
        part
        for part in re.split(r"[、\s]+", remaining.strip())
        if part
    ]
    source = " ".join(source_parts) or None
    return body[: match.start()].rstrip(), source, urls


def _item_match(line: str) -> Optional[re.Match[str]]:
    return _SYMBOL_ITEM_PATTERN.match(line) or _NUMBER_ITEM_PATTERN.match(line)


def _parse_report_date(lines: list[str]) -> date:
    for line in lines:
        match = _DATE_PATTERN.search(line)
        if match:
            try:
                return date(*(int(value) for value in match.groups()))
            except ValueError as exc:
                raise SubmissionArchiveParseError(
                    "报告日期无效，请检查年月日"
                ) from exc
    raise SubmissionArchiveParseError("未找到报告日期，请检查文档日期行")


def _finish_item(
    items: list[ParsedSubmissionItem],
    *,
    section: Optional[str],
    marker: Optional[str],
    title: Optional[str],
    body_lines: list[str],
) -> None:
    if title is None:
        return
    body, source, urls = _extract_source("\n".join(body_lines).strip())
    norm_title = normalize_submission_text(title)
    items.append(
        ParsedSubmissionItem(
            section=section,
            marker=marker,
            order_index=len(items),
            title=title.strip(),
            body=body,
            source=source,
            urls=urls,
            norm_title=norm_title,
            norm_title_hash=normalized_title_hash(title),
        )
    )


def parse_submission_report(pasted_text: str) -> ParsedSubmissionReport:
    lines = [line.strip() for line in (pasted_text or "").splitlines()]
    nonempty_lines = [line for line in lines if line]
    if not nonempty_lines:
        raise SubmissionArchiveParseError("报告内容不能为空")

    title_line = nonempty_lines[0]
    first_content_index = next(
        (
            index
            for index, line in enumerate(nonempty_lines)
            if _SECTION_PATTERN.match(line) or _item_match(line)
        ),
        len(nonempty_lines),
    )
    header_lines = nonempty_lines[:first_content_index]
    report_date = _parse_report_date(header_lines)
    issue_no = next((line for line in header_lines if "期" in line), None)
    report_type = detect_report_type(title_line, issue_no)

    items: list[ParsedSubmissionItem] = []
    current_section: Optional[str] = None
    current_marker: Optional[str] = None
    current_title: Optional[str] = None
    body_lines: list[str] = []
    found_section = False

    for line in nonempty_lines:
        section_match = _SECTION_PATTERN.match(line)
        if section_match:
            _finish_item(
                items,
                section=current_section,
                marker=current_marker,
                title=current_title,
                body_lines=body_lines,
            )
            current_title = None
            current_marker = None
            body_lines = []
            current_section = section_match.group(1).strip()
            found_section = True
            continue

        item_match = _item_match(line)
        if item_match:
            _finish_item(
                items,
                section=current_section,
                marker=current_marker,
                title=current_title,
                body_lines=body_lines,
            )
            current_marker = item_match.group(1)
            current_title = item_match.group(2).strip()
            body_lines = []
            continue

        if current_title is not None:
            body_lines.append(line)

    _finish_item(
        items,
        section=current_section,
        marker=current_marker,
        title=current_title,
        body_lines=body_lines,
    )
    if not items:
        raise SubmissionArchiveParseError(
            "未识别到任何报告条目，请检查章节和条目标记格式"
        )

    warnings: list[str] = []
    if not found_section:
        warnings.append("未识别到章节，条目将以空章节保存")
    for item in items:
        if not item.body:
            warnings.append(f"第 {item.order_index + 1} 条正文为空")
        if not item.source:
            warnings.append(f"第 {item.order_index + 1} 条未识别到来源")

    return ParsedSubmissionReport(
        title_line=title_line,
        issue_no=issue_no,
        report_date=report_date,
        compiled_date=default_compiled_date(report_type, report_date),
        detected_report_type=report_type,
        items=items,
        warnings=warnings,
    )


__all__ = [
    "ParsedSubmissionItem",
    "ParsedSubmissionReport",
    "SubmissionArchiveParseError",
    "default_compiled_date",
    "detect_report_type",
    "looks_like_submission_report",
    "normalize_submission_text",
    "normalized_title_hash",
    "parse_submission_report",
]
