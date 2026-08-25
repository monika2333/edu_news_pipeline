from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from src.workers import feishu_archive_bot
from src.workers.feishu_archive_bot import FeishuInboundMessage
from src.workers.submission_archive_ingest import SubmissionReportConflictError


REPORT_TEXT = """首都教育舆情
总第1期
2026年8月21日
【舆情速览】
一、测试条目
正文（北京日报）"""


def _message(**overrides: str) -> FeishuInboundMessage:
    values = {
        "event_id": "event-1",
        "message_id": "om_1",
        "chat_id": "oc_1",
        "chat_type": "p2p",
        "sender_open_id": "ou_owner",
        "sender_type": "user",
        "message_type": "text",
        "text": REPORT_TEXT,
    }
    values.update(overrides)
    return FeishuInboundMessage(**values)


def _result(*, created: bool = True, warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "created": created,
        "report": {
            "id": "report-1",
            "report_type": "wanbao",
            "report_date": date(2026, 8, 21),
            "items": [{"id": "item-1"}],
        },
        "warnings": warnings or [],
    }


def test_trusted_private_text_with_invalid_format_gets_guidance() -> None:
    replies: list[dict[str, str]] = []

    status = feishu_archive_bot.handle_inbound_message(
        _message(text="今天辛苦了"),
        allowed_sender_ids=frozenset({"ou_owner"}),
        reply_sender=lambda **kwargs: replies.append(kwargs) or True,
    )

    assert status == "invalid_format"
    assert "未存档：消息格式不符合要求" in replies[0]["message"]
    assert "首都教育每日舆情综报" in replies[0]["message"]
    assert "首都教育舆情" in replies[0]["message"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"sender_open_id": "ou_other"},
        {"chat_type": "group"},
        {"message_type": "image"},
    ],
)
def test_untrusted_or_unsupported_messages_are_ignored(
    overrides: dict[str, str],
) -> None:
    status = feishu_archive_bot.handle_inbound_message(
        _message(**overrides),
        allowed_sender_ids=frozenset({"ou_owner"}),
    )

    assert status == "ignored"


def test_supported_report_is_saved_and_processing_is_launched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    replies: list[dict[str, str]] = []
    launched: list[str] = []

    def fake_create(text: str, **kwargs: Any) -> dict[str, Any]:
        captured["text"] = text
        captured.update(kwargs)
        return _result(warnings=["第 1 条未识别到来源"])

    monkeypatch.setattr(feishu_archive_bot, "create_report_from_text", fake_create)

    status = feishu_archive_bot.handle_inbound_message(
        _message(),
        allowed_sender_ids=frozenset({"ou_owner"}),
        reply_sender=lambda **kwargs: replies.append(kwargs) or True,
        processing_launcher=lambda report_id: launched.append(report_id) or 123,
    )

    assert status == "created"
    assert captured["source_message_id"] == "om_1"
    assert captured["source_sender_id"] == "ou_owner"
    assert captured["ingest_source"] == "feishu"
    assert launched == ["report-1"]
    assert "存档成功" in replies[0]["message"]
    assert "识别警告" in replies[0]["message"]


def test_parse_failure_replies_without_saving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies: list[dict[str, str]] = []

    def fail_parse(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        from src.domain.submission_archive_parser import SubmissionArchiveParseError

        raise SubmissionArchiveParseError("未找到报告日期")

    monkeypatch.setattr(feishu_archive_bot, "create_report_from_text", fail_parse)

    status = feishu_archive_bot.handle_inbound_message(
        _message(text="首都教育舆情\n一、条目\n正文"),
        allowed_sender_ids=frozenset({"ou_owner"}),
        reply_sender=lambda **kwargs: replies.append(kwargs) or True,
    )

    assert status == "parse_failed"
    assert "解析失败" in replies[0]["message"]


def test_missing_message_id_replies_with_failure() -> None:
    replies: list[dict[str, str]] = []

    status = feishu_archive_bot.handle_inbound_message(
        _message(message_id=""),
        allowed_sender_ids=frozenset({"ou_owner"}),
        reply_sender=lambda **kwargs: replies.append(kwargs) or True,
    )

    assert status == "failed"
    assert "未获取到飞书消息编号" in replies[0]["message"]


def test_unexpected_handler_failure_replies_with_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies: list[dict[str, str]] = []

    def fail_recognition(_text: str) -> bool:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(
        feishu_archive_bot,
        "looks_like_submission_report",
        fail_recognition,
    )

    status = feishu_archive_bot.handle_inbound_message(
        _message(),
        allowed_sender_ids=frozenset({"ou_owner"}),
        reply_sender=lambda **kwargs: replies.append(kwargs) or True,
    )

    assert status == "failed"
    assert "存档失败" in replies[0]["message"]


def test_same_date_conflict_is_not_overwritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies: list[dict[str, str]] = []

    def fail_conflict(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise SubmissionReportConflictError(
            {
                "id": "existing",
                "report_type": "wanbao",
                "report_date": date(2026, 8, 21),
            }
        )

    monkeypatch.setattr(feishu_archive_bot, "create_report_from_text", fail_conflict)

    status = feishu_archive_bot.handle_inbound_message(
        _message(message_id="om_2"),
        allowed_sender_ids=frozenset({"ou_owner"}),
        reply_sender=lambda **kwargs: replies.append(kwargs) or True,
    )

    assert status == "conflict"
    assert "未重复保存" in replies[0]["message"]


def test_retried_message_is_not_saved_or_processed_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies: list[dict[str, str]] = []
    launched: list[str] = []
    monkeypatch.setattr(
        feishu_archive_bot,
        "create_report_from_text",
        lambda *_args, **_kwargs: _result(created=False),
    )

    status = feishu_archive_bot.handle_inbound_message(
        _message(),
        allowed_sender_ids=frozenset({"ou_owner"}),
        reply_sender=lambda **kwargs: replies.append(kwargs) or True,
        processing_launcher=lambda report_id: launched.append(report_id) or 123,
    )

    assert status == "duplicate"
    assert launched == []
    assert "不会重复保存" in replies[0]["message"]
