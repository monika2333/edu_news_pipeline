from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlparse

from src.adapters.db_postgres_core import get_adapter
from src.console.auth_service import ConsoleUser
from src.domain.report_type import SUBMISSION_DOC_TYPES as VALID_REPORT_TYPES
from src.domain.submission_archive_parser import (
    normalize_submission_text,
    normalized_title_hash,
    parse_submission_report,
)


class SubmissionReportConflictError(RuntimeError):
    """Raised when a report with the same type and date already exists."""

    def __init__(self, report: Mapping[str, Any]) -> None:
        self.report = dict(report)
        super().__init__(
            "同一类型和日期的报告已存在，请取消或选择覆盖"
        )


class SubmissionReportNotFoundError(ValueError):
    """Raised when a submitted report or item does not exist."""


def _require_business_user_id(user: ConsoleUser) -> str:
    if not user.user_id:
        raise PermissionError("该操作需要数据库用户账号登录")
    return user.user_id


def _serialize_parse_result(pasted_text: str) -> dict[str, Any]:
    parsed = parse_submission_report(pasted_text)
    return {
        "title_line": parsed.title_line,
        "issue_no": parsed.issue_no,
        "report_date": parsed.report_date,
        "compiled_date": parsed.compiled_date,
        "detected_report_type": parsed.detected_report_type,
        "items": [asdict(item) for item in parsed.items],
        "warnings": parsed.warnings,
    }


def parse_report(pasted_text: str) -> dict[str, Any]:
    return _serialize_parse_result(pasted_text)


def _prepare_items(
    items: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        title = str(item.get("title") or "").strip()
        if not title:
            raise ValueError(f"第 {index + 1} 条标题不能为空")
        body = str(item.get("body") or "").strip()
        urls = [
            str(url).strip()
            for url in item.get("urls") or []
            if str(url).strip()
        ]
        invalid_urls = [
            url
            for url in urls
            if urlparse(url).scheme not in {"http", "https"}
        ]
        if invalid_urls:
            raise ValueError(
                f"第 {index + 1} 条包含非 HTTP(S) URL"
            )
        prepared.append(
            {
                "section": str(item.get("section") or "").strip() or None,
                "marker": str(item.get("marker") or "").strip() or None,
                "order_index": index,
                "title": title,
                "body": body,
                "source": str(item.get("source") or "").strip() or None,
                "urls": urls,
                "norm_title": normalize_submission_text(title),
                "norm_title_hash": normalized_title_hash(title),
            }
        )
    if not prepared:
        raise ValueError("报告至少需要一个条目")
    return prepared


def create_report(
    *,
    report_type: str,
    report_date: date,
    compiled_date: date,
    issue_no: Optional[str],
    title_line: Optional[str],
    pasted_text: str,
    items: Sequence[Mapping[str, Any]],
    overwrite: bool,
) -> dict[str, Any]:
    if report_type not in VALID_REPORT_TYPES:
        raise ValueError(f"不支持的报告类型: {report_type}")
    adapter = get_adapter()
    conflict = adapter.submission_archive.find_report_conflict(
        report_type=report_type,
        report_date=report_date,
    )
    if conflict and not overwrite:
        raise SubmissionReportConflictError(conflict)
    prepared_items = _prepare_items(items)
    created = adapter.submission_archive.create_report(
        report={
            "report_type": report_type,
            "report_date": report_date,
            "compiled_date": compiled_date,
            "issue_no": (issue_no or "").strip() or None,
            "title_line": (title_line or "").strip() or None,
            "pasted_text": pasted_text,
        },
        items=prepared_items,
        replace_report_id=str(conflict["id"]) if conflict else None,
    )
    return {
        "report": created,
        "link_summary": {"processing": len(created.get("items") or [])},
    }


def list_reports(
    *,
    report_type: Optional[str],
    date_from: Optional[date],
    date_to: Optional[date],
    limit: int,
    offset: int,
) -> dict[str, Any]:
    if report_type and report_type not in VALID_REPORT_TYPES:
        raise ValueError(f"不支持的报告类型: {report_type}")
    rows, total = get_adapter().submission_archive.fetch_reports(
        report_type=report_type,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return {
        "items": rows,
        "total": total,
        "limit": max(1, min(limit, 200)),
        "offset": max(0, offset),
    }


def get_report(report_id: str) -> dict[str, Any]:
    report = get_adapter().submission_archive.fetch_report(report_id)
    if not report:
        raise SubmissionReportNotFoundError("未找到这份存档报告")
    return report


def delete_report(report_id: str) -> None:
    if not get_adapter().submission_archive.delete_report(report_id):
        raise SubmissionReportNotFoundError("未找到这份存档报告")


def reparse_report(report_id: str) -> dict[str, Any]:
    adapter = get_adapter()
    report = adapter.submission_archive.fetch_report(report_id)
    if not report:
        raise SubmissionReportNotFoundError("未找到这份存档报告")
    parsed = parse_submission_report(str(report["pasted_text"]))
    items = _prepare_items([asdict(item) for item in parsed.items])
    rebuilt = adapter.submission_archive.replace_report_items(
        report_id=report_id,
        items=items,
    )
    report["items"] = rebuilt
    report["item_count"] = len(rebuilt)
    return {
        "report": adapter.submission_archive.fetch_report(report_id),
        "link_summary": {"processing": len(rebuilt)},
        "warnings": parsed.warnings,
    }


def list_pending_links(*, limit: int, offset: int) -> dict[str, Any]:
    rows, total = get_adapter().submission_archive.fetch_pending_links(
        limit=limit,
        offset=offset,
    )
    return {
        "items": rows,
        "total": total,
        "limit": max(1, min(limit, 200)),
        "offset": max(0, offset),
    }


def decide_link(
    *,
    item_id: str,
    accepted: bool,
    user: ConsoleUser,
) -> dict[str, Any]:
    updated = get_adapter().submission_archive.decide_link(
        item_id=item_id,
        accepted=accepted,
        actor_user_id=_require_business_user_id(user),
    )
    if not updated:
        raise SubmissionReportNotFoundError(
            "待确认条目不存在或已被其他人处理"
        )
    return updated


def search_archive(*, query: str, limit: int) -> dict[str, Any]:
    normalized_query = (query or "").strip()
    if not normalized_query:
        return {"items": [], "query": "", "total": 0}
    rows = get_adapter().submission_archive.search_report_items(
        query=normalized_query,
        limit=limit,
    )
    return {
        "items": rows,
        "query": normalized_query,
        "total": len(rows),
    }


def attach_duplicate_badges(
    items: list[dict[str, Any]],
    *,
    adapter: Any = None,
) -> None:
    article_ids = [str(item.get("article_id") or "") for item in items]
    target_adapter = adapter or get_adapter()
    badges = target_adapter.submission_archive.fetch_duplicate_badges(article_ids)
    for item in items:
        item["submission_duplicate"] = badges.get(
            str(item.get("article_id") or "")
        )


def dismiss_duplicates(
    *,
    article_id: str,
    user: ConsoleUser,
) -> int:
    normalized_article_id = (article_id or "").strip()
    if not normalized_article_id:
        raise ValueError("article_id 不能为空")
    return get_adapter().submission_archive.dismiss_duplicate_matches(
        article_id=normalized_article_id,
        actor_user_id=_require_business_user_id(user),
    )


__all__ = [
    "SubmissionReportConflictError",
    "SubmissionReportNotFoundError",
    "attach_duplicate_badges",
    "create_report",
    "decide_link",
    "delete_report",
    "dismiss_duplicates",
    "get_report",
    "list_pending_links",
    "list_reports",
    "parse_report",
    "reparse_report",
    "search_archive",
]
