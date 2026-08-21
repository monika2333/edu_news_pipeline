from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence
from urllib.parse import urlparse

from src.adapters.db_postgres_core import get_adapter
from src.domain.report_type import SUBMISSION_DOC_TYPES as VALID_REPORT_TYPES
from src.domain.submission_archive_parser import (
    normalize_submission_text,
    normalized_title_hash,
    parse_submission_report,
)

if TYPE_CHECKING:
    from src.adapters.db_postgres_core import PostgresAdapter


class SubmissionReportConflictError(RuntimeError):
    """Raised when a report with the same type and date already exists."""

    def __init__(self, report: Mapping[str, Any]) -> None:
        self.report = dict(report)
        super().__init__("同一类型和日期的报告已存在，请取消或选择覆盖")


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
            raise ValueError(f"第 {index + 1} 条包含非 HTTP(S) URL")
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
    ingest_source: str = "console",
    source_message_id: Optional[str] = None,
    source_sender_id: Optional[str] = None,
    adapter: Optional[PostgresAdapter] = None,
) -> dict[str, Any]:
    """Validate and persist one submitted report without running link matching."""
    if report_type not in VALID_REPORT_TYPES:
        raise ValueError(f"不支持的报告类型: {report_type}")
    normalized_ingest_source = ingest_source.strip() or "console"
    normalized_message_id = (source_message_id or "").strip() or None
    normalized_sender_id = (source_sender_id or "").strip() or None
    target_adapter = adapter or get_adapter()

    if normalized_message_id:
        existing = target_adapter.submission_archive.fetch_report_by_source_message(
            ingest_source=normalized_ingest_source,
            source_message_id=normalized_message_id,
        )
        if existing:
            return {
                "created": False,
                "report": existing,
                "link_summary": {
                    "processing": sum(
                        item.get("link_status") == "processing"
                        for item in existing.get("items") or []
                    )
                },
            }

    conflict = target_adapter.submission_archive.find_report_conflict(
        report_type=report_type,
        report_date=report_date,
    )
    if conflict and not overwrite:
        raise SubmissionReportConflictError(conflict)

    prepared_items = _prepare_items(items)
    report = {
        "report_type": report_type,
        "report_date": report_date,
        "compiled_date": compiled_date,
        "issue_no": (issue_no or "").strip() or None,
        "title_line": (title_line or "").strip() or None,
        "pasted_text": pasted_text,
        "ingest_source": normalized_ingest_source,
        "source_message_id": normalized_message_id,
        "source_sender_id": normalized_sender_id,
    }
    if normalized_message_id:
        created, was_created = (
            target_adapter.submission_archive.create_report_idempotent(
                report=report,
                items=prepared_items,
                replace_report_id=str(conflict["id"]) if conflict else None,
            )
        )
    else:
        created = target_adapter.submission_archive.create_report(
            report=report,
            items=prepared_items,
            replace_report_id=str(conflict["id"]) if conflict else None,
        )
        was_created = True
    return {
        "created": was_created,
        "report": created,
        "link_summary": {
            "processing": sum(
                item.get("link_status") == "processing"
                for item in created.get("items") or []
            )
        },
    }


def create_report_from_text(
    pasted_text: str,
    *,
    ingest_source: str,
    source_message_id: str,
    source_sender_id: str,
    adapter: Optional[PostgresAdapter] = None,
) -> dict[str, Any]:
    """Parse and persist one external report message without overwriting."""
    parsed = parse_submission_report(pasted_text)
    result = create_report(
        report_type=parsed.detected_report_type,
        report_date=parsed.report_date,
        compiled_date=parsed.compiled_date,
        issue_no=parsed.issue_no,
        title_line=parsed.title_line,
        pasted_text=pasted_text,
        items=[asdict(item) for item in parsed.items],
        overwrite=False,
        ingest_source=ingest_source,
        source_message_id=source_message_id,
        source_sender_id=source_sender_id,
        adapter=adapter,
    )
    result["warnings"] = parsed.warnings
    return result


__all__ = [
    "SubmissionReportConflictError",
    "create_report",
    "create_report_from_text",
]
