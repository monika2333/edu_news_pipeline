from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Literal, Optional

from src.config import get_settings
from src.domain.submission_archive_parser import (
    SUPPORTED_SUBMISSION_TITLES,
    SubmissionArchiveParseError,
    looks_like_submission_report,
)
from src.notifications.feishu import send_text_to_chat
from src.workers import log_error, log_info
from src.workers.submission_archive_ingest import (
    SubmissionReportConflictError,
    create_report_from_text,
)
from src.workers.submission_archive_processing import (
    launch_submission_report_processing,
)

if TYPE_CHECKING:
    from src.adapters.db_postgres_core import PostgresAdapter

WORKER = "feishu_archive_bot"
_REPORT_TYPE_LABELS = {
    "zongbao": "综报",
    "wanbao": "晚报",
    "feedback": "反馈",
}


@dataclass(frozen=True, slots=True)
class FeishuInboundMessage:
    event_id: str
    message_id: str
    chat_id: str
    chat_type: str
    sender_open_id: str
    sender_type: str
    message_type: str
    text: str


MessageResult = Literal[
    "ignored",
    "invalid_format",
    "parse_failed",
    "conflict",
    "duplicate",
    "created",
    "created_processing_failed",
    "failed",
]
ReplySender = Callable[..., bool]
ProcessingLauncher = Callable[[str], int]


def _report_label(report_type: object) -> str:
    value = str(report_type or "")
    return _REPORT_TYPE_LABELS.get(value, value or "未知")


def _safe_reply(
    chat_id: str,
    message: str,
    *,
    reply_sender: ReplySender,
) -> None:
    try:
        reply_sender(chat_id=chat_id, message=message)
    except Exception as exc:
        log_error(WORKER, f"reply:{chat_id}", exc)


def _success_message(result: dict[str, object]) -> str:
    report = result["report"]
    assert isinstance(report, dict)
    items = report.get("items") or []
    lines = [
        "存档成功",
        f"类型：{_report_label(report.get('report_type'))}",
        f"日期：{report.get('report_date')}",
        f"条目：{len(items)} 条",
        "系统正在自动回链。",
    ]
    warnings = [str(value) for value in result.get("warnings") or []]
    if warnings:
        lines.append("")
        lines.append("识别警告：")
        lines.extend(f"- {warning}" for warning in warnings[:10])
        if len(warnings) > 10:
            lines.append(f"- 其余 {len(warnings) - 10} 条请在控制台查看")
    return "\n".join(lines)


def _invalid_format_message() -> str:
    supported_titles = "\n".join(
        f"- {title}" for title in SUPPORTED_SUBMISSION_TITLES
    )
    return (
        "未存档：消息格式不符合要求。\n"
        "请直接发送完整报送稿，并确保首个非空行是以下标题之一：\n"
        f"{supported_titles}"
    )


def _handle_trusted_text_message(
    message: FeishuInboundMessage,
    *,
    adapter: Optional[PostgresAdapter] = None,
    reply_sender: ReplySender = send_text_to_chat,
    processing_launcher: ProcessingLauncher = launch_submission_report_processing,
) -> MessageResult:
    if not looks_like_submission_report(message.text):
        _safe_reply(
            message.chat_id,
            _invalid_format_message(),
            reply_sender=reply_sender,
        )
        return "invalid_format"
    if not message.message_id:
        error = ValueError("Missing Feishu message_id")
        log_error(WORKER, "message", error)
        _safe_reply(
            message.chat_id,
            "存档失败：未获取到飞书消息编号，请稍后重新发送。",
            reply_sender=reply_sender,
        )
        return "failed"

    try:
        result = create_report_from_text(
            message.text,
            ingest_source="feishu",
            source_message_id=message.message_id,
            source_sender_id=message.sender_open_id,
            adapter=adapter,
        )
    except SubmissionArchiveParseError as exc:
        _safe_reply(
            message.chat_id,
            f"识别到报送存档标题，但解析失败：{exc}",
            reply_sender=reply_sender,
        )
        return "parse_failed"
    except SubmissionReportConflictError as exc:
        existing = exc.report
        _safe_reply(
            message.chat_id,
            (
                "未重复保存：系统中已存在同日同类型存档。\n"
                f"类型：{_report_label(existing.get('report_type'))}\n"
                f"日期：{existing.get('report_date')}"
            ),
            reply_sender=reply_sender,
        )
        return "conflict"
    except Exception as exc:
        log_error(WORKER, f"ingest:{message.message_id}", exc)
        _safe_reply(
            message.chat_id,
            "存档失败，系统已记录错误，请稍后重试或使用控制台录入。",
            reply_sender=reply_sender,
        )
        return "failed"

    report = result["report"]
    assert isinstance(report, dict)
    report_id = str(report["id"])
    if not result.get("created", True):
        _safe_reply(
            message.chat_id,
            f"该飞书消息已经存档，不会重复保存。\n存档编号：{report_id}",
            reply_sender=reply_sender,
        )
        return "duplicate"

    try:
        processing_launcher(report_id)
    except Exception as exc:
        log_error(WORKER, f"launch:{report_id}", exc)
        _safe_reply(
            message.chat_id,
            (
                _success_message(result)
                + "\n\n自动回链启动失败，请稍后在控制台检查。"
            ),
            reply_sender=reply_sender,
        )
        return "created_processing_failed"

    _safe_reply(
        message.chat_id,
        _success_message(result),
        reply_sender=reply_sender,
    )
    log_info(WORKER, f"Archived Feishu message {message.message_id} as {report_id}")
    return "created"


def handle_inbound_message(
    message: FeishuInboundMessage,
    *,
    allowed_sender_ids: frozenset[str],
    adapter: Optional[PostgresAdapter] = None,
    reply_sender: ReplySender = send_text_to_chat,
    processing_launcher: ProcessingLauncher = launch_submission_report_processing,
) -> MessageResult:
    """Recognize and persist one trusted private-chat archive message."""
    if (
        message.chat_type != "p2p"
        or message.sender_type != "user"
        or message.message_type != "text"
        or message.sender_open_id not in allowed_sender_ids
    ):
        return "ignored"

    try:
        return _handle_trusted_text_message(
            message,
            adapter=adapter,
            reply_sender=reply_sender,
            processing_launcher=processing_launcher,
        )
    except Exception as exc:
        log_error(WORKER, f"handle:{message.message_id or message.chat_id}", exc)
        _safe_reply(
            message.chat_id,
            "存档失败，系统已记录错误，请稍后重试或使用控制台录入。",
            reply_sender=reply_sender,
        )
        return "failed"


def _attribute(value: object, name: str) -> object:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _message_from_event(data: object) -> FeishuInboundMessage:
    header = _attribute(data, "header")
    event = _attribute(data, "event")
    sender = _attribute(event, "sender")
    sender_id = _attribute(sender, "sender_id")
    raw_message = _attribute(event, "message")
    content = str(_attribute(raw_message, "content") or "")
    try:
        content_payload = json.loads(content)
    except json.JSONDecodeError:
        content_payload = {}
    return FeishuInboundMessage(
        event_id=str(_attribute(header, "event_id") or ""),
        message_id=str(_attribute(raw_message, "message_id") or ""),
        chat_id=str(_attribute(raw_message, "chat_id") or ""),
        chat_type=str(_attribute(raw_message, "chat_type") or ""),
        sender_open_id=str(_attribute(sender_id, "open_id") or ""),
        sender_type=str(_attribute(sender, "sender_type") or ""),
        message_type=str(_attribute(raw_message, "message_type") or ""),
        text=str(content_payload.get("text") or ""),
    )


def run() -> None:
    """Run the Feishu long-connection archive receiver until interrupted."""
    settings = get_settings()
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        raise RuntimeError("Set FEISHU_APP_ID and FEISHU_APP_SECRET first")
    allowed_sender_ids = frozenset(settings.feishu_archive_allowed_open_ids)
    if not allowed_sender_ids:
        raise RuntimeError(
            "Set FEISHU_ARCHIVE_ALLOWED_OPEN_IDS or an open_id FEISHU_RECEIVE_ID"
        )

    try:
        import lark_oapi as lark
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("Install dependencies from requirements.txt") from exc

    executor = ThreadPoolExecutor(
        max_workers=4,
        thread_name_prefix="feishu-archive",
    )

    def on_message_event(data: object) -> None:
        try:
            message = _message_from_event(data)
        except Exception as exc:
            log_error(WORKER, "decode_event", exc)
            return
        executor.submit(
            handle_inbound_message,
            message,
            allowed_sender_ids=allowed_sender_ids,
        )

    event_handler = (
        lark.EventDispatcherHandler.builder(
            "",
            "",
            lark.LogLevel.WARNING,
        )
        .register_p2_im_message_receive_v1(on_message_event)
        .build()
    )
    client = lark.ws.Client(
        settings.feishu_app_id,
        settings.feishu_app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.WARNING,
    )
    log_info(WORKER, "Starting Feishu archive long connection")
    try:
        client.start()
    finally:
        executor.shutdown(wait=True, cancel_futures=False)


__all__ = [
    "FeishuInboundMessage",
    "handle_inbound_message",
    "run",
]
