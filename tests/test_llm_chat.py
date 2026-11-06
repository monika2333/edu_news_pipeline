from __future__ import annotations

import gzip
import json
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Iterator

import pytest

from src.adapters import llm_chat
from src.adapters.llm_chat import (
    LLM_FAILURE_QUOTA,
    LLM_FAILURE_TRANSIENT,
    LLM_SCOPE_ARTICLE,
    LLM_SCOPE_SYSTEMATIC,
    LLMQuotaError,
    LLMRequestError,
    LLMWallClockTimeout,
    apply_reasoning_config,
    build_llm_request_error,
    classify_llm_failure,
    extract_message_text,
    is_llm_quota_response,
    is_retryable_llm_status,
    parse_llm_error_body,
    post_chat_completion,
    raise_for_llm_quota_error,
)
from src.config import get_settings


def test_apply_reasoning_config_uses_reasoning_field():
    settings = replace(
        get_settings(),
        llm_reasoning_effort="high",
        llm_reasoning_max_tokens=None,
        llm_reasoning_exclude=True,
    )
    payload = {}

    apply_reasoning_config(
        payload,
        settings=settings,
        enabled=True,
    )

    assert payload == {
        "reasoning": {"enabled": True, "effort": "high", "exclude": True}
    }


def test_apply_reasoning_config_does_nothing_when_disabled():
    settings = get_settings()
    payload = {}

    apply_reasoning_config(
        payload,
        settings=settings,
        enabled=False,
    )

    assert payload == {}


def test_extract_message_text_reads_message_reasoning():
    choice = {"message": {"content": "", "reasoning": "42"}}

    assert extract_message_text(choice) == "42"


def test_extract_message_text_reads_legacy_reasoning_content():
    choice = {"message": {"content": ""}, "reasoning_content": ["4", "2"]}

    assert extract_message_text(choice) == "4 2"


def test_is_llm_quota_response_detects_billing_and_balance_errors():
    assert is_llm_quota_response(402, "Payment required")
    assert is_llm_quota_response(429, "insufficient credits for this request")
    assert is_llm_quota_response(403, "账户余额不足，请充值")


def test_is_llm_quota_response_covers_deepseek_and_glm_official_shapes():
    # DeepSeek 余额不足返回 402
    assert is_llm_quota_response(402, "Insufficient Balance")
    # GLM 返回 429 + 中文余额文案
    assert is_llm_quota_response(429, "余额不足或无可用资源包，请充值后重试")
    assert is_llm_quota_response(429, "该模型余额不足或无可用资源包")


def test_is_llm_quota_response_ignores_plain_rate_limit():
    assert not is_llm_quota_response(429, "rate limit exceeded, retry later")


def test_raise_for_llm_quota_error_sends_alert_once_per_cooldown(monkeypatch, tmp_path):
    calls = []
    settings = SimpleNamespace(
        llm_quota_alert_enabled=True,
        llm_quota_alert_cooldown_seconds=21600,
        llm_quota_alert_state_path=tmp_path / "state.json",
    )

    def fake_notify(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(llm_chat, "get_settings", lambda: settings)
    monkeypatch.setattr("src.notifications.feishu.notify_llm_quota_alert", fake_notify)
    monkeypatch.setattr(llm_chat.time, "time", lambda: 1000.0)

    with pytest.raises(LLMQuotaError) as first:
        raise_for_llm_quota_error(
            status_code=429,
            response_text="insufficient credits for this request",
            operation="score",
            model="model-a",
        )

    with pytest.raises(LLMQuotaError):
        raise_for_llm_quota_error(
            status_code=429,
            response_text="insufficient credits for this request",
            operation="summarize",
            model="model-a",
        )

    assert first.value.operation == "score"
    assert len(calls) == 1
    assert calls[0]["operation"] == "score"
    assert settings.llm_quota_alert_state_path.exists()


def test_raise_for_llm_quota_error_allows_normal_429():
    raise_for_llm_quota_error(
        status_code=429,
        response_text="rate limit exceeded",
        operation="score",
        model="model-a",
    )


class _StreamingResponse:
    encoding = "utf-8"

    def __init__(self, status_code: int, chunks, headers: dict | None = None) -> None:
        self.status_code = status_code
        self.raw = _RawStream(chunks)
        self.headers = headers or {}
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _RawStream:
    def __init__(self, chunks) -> None:
        self._chunks = iter(chunks)

    def read1(self, amount: int, *, decode_content: bool) -> bytes:
        assert amount == 8192
        assert decode_content is True
        return next(self._chunks, b"")


_GZIP_RESPONSE = {
    "choices": [{"message": {"content": "gzip-ok"}}],
    "provider": "local-test",
}


class _LocalLLMHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length:
            self.rfile.read(content_length)

        if self.path == "/keepalive":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "200")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for _ in range(200):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if self.path == "/gzip":
            body = gzip.compress(json.dumps(_GZIP_RESPONSE).encode("utf-8"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_error(404)

    def log_message(self, format: str, *args) -> None:
        return


@contextmanager
def _local_llm_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalLLMHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_post_chat_completion_stops_keepalive_stream_at_wall_clock_budget(
) -> None:
    with _local_llm_server() as base_url:
        started_at = time.monotonic()
        with pytest.raises(LLMWallClockTimeout):
            post_chat_completion(
                f"{base_url}/keepalive",
                payload={"model": "model-a", "messages": []},
                headers={"Authorization": "Bearer test"},
                timeout=1,
                budget=0.12,
                retries=1,
                retryable_statuses={429, 500},
                operation="test_keepalive",
                model="model-a",
                allowed_hosts={"127.0.0.1"},
            )
        elapsed = time.monotonic() - started_at

    assert 0.07 <= elapsed < 1.0


def test_post_chat_completion_decodes_gzip_response_body() -> None:
    with _local_llm_server() as base_url:
        from urllib.parse import urlsplit

        host = urlsplit(base_url).hostname
        result = post_chat_completion(
            f"{base_url}/gzip",
            payload={"model": "model-a", "messages": []},
            headers={},
            timeout=1,
            budget=1,
            retries=1,
            retryable_statuses=set(),
            operation="test_gzip",
            model="model-a",
            allowed_hosts={host},
        )

    assert result == _GZIP_RESPONSE


def test_post_chat_completion_blocks_hosts_outside_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_post(*args, **kwargs):
        raise AssertionError("请求不应发出")

    monkeypatch.setattr(llm_chat.requests, "post", fail_post)

    with pytest.raises(llm_chat.LLMEndpointBlockedError, match="evil.example.test"):
        post_chat_completion(
            "https://evil.example.test/chat/completions",
            payload={"model": "model-a"},
            headers={},
            timeout=1,
            budget=1,
            retries=3,
            retryable_statuses={500},
            operation="test_blocked_host",
            model="model-a",
        )


def test_post_chat_completion_never_follows_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        return _StreamingResponse(
            302,
            [b""],
            headers={"Location": "https://attacker.example.test/steal"},
        )

    monkeypatch.setattr(llm_chat.requests, "post", fake_post)

    with pytest.raises(RuntimeError, match="接入点返回重定向，按安全策略未跟随"):
        post_chat_completion(
            "https://llm.example.test/chat/completions",
            payload={"model": "model-a", "messages": []},
            headers={},
            timeout=1,
            budget=1,
            retries=3,
            retryable_statuses={500},
            operation="test_redirect",
            model="model-a",
            allowed_hosts={"llm.example.test"},
        )

    assert len(calls) == 1
    assert calls[0]["allow_redirects"] is False


def test_post_chat_completion_stops_retries_when_backoff_uses_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        return _StreamingResponse(503, [b"service unavailable"])

    monkeypatch.setattr(llm_chat.requests, "post", fake_post)

    started_at = time.monotonic()
    with pytest.raises(LLMWallClockTimeout):
        post_chat_completion(
            "https://llm.example.test/chat/completions",
            payload={"model": "model-a", "messages": []},
            headers={},
            timeout=1,
            budget=0.08,
            retries=10,
            retryable_statuses={503},
            operation="test_retries",
            model="model-a",
            backoff_initial=0.03,
            allowed_hosts={"llm.example.test"},
        )
    elapsed = time.monotonic() - started_at

    assert len(calls) == 2
    assert 0.06 <= elapsed < 0.5
    assert all(call["stream"] is True for call in calls)
    assert all("stream" not in call["json"] for call in calls)


def test_post_chat_completion_returns_complete_json_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"choices": [{"message": {"content": "ok"}}], "provider": "test"}
    response = _StreamingResponse(200, [json.dumps(expected).encode("utf-8")])
    monkeypatch.setattr(llm_chat.requests, "post", lambda *args, **kwargs: response)

    result = post_chat_completion(
        "https://llm.example.test/chat/completions",
        payload={"model": "model-a"},
        headers={},
        timeout=1,
        budget=1,
        retries=1,
        retryable_statuses=set(),
        operation="test_success",
        model="model-a",
        allowed_hosts={"llm.example.test"},
    )

    assert result == expected


_DEEPSEEK_JSON_SCHEMA_ERROR = json.dumps(
    {
        "error": {
            "message": "This response_format type is unavailable now (request_id: req-123)",
            "type": "invalid_request_error",
            "param": None,
            "code": "invalid_request_error",
        }
    }
)
_RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}


def _fake_status_post(
    status_code: int,
    body: str,
    calls: list[dict],
):
    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        return _StreamingResponse(status_code, [body.encode("utf-8")])

    return fake_post


def test_parse_llm_error_body_extracts_request_id_from_message() -> None:
    detail = parse_llm_error_body(_DEEPSEEK_JSON_SCHEMA_ERROR)

    assert detail.parsed is True
    assert detail.provider_code == "invalid_request_error"
    assert detail.provider_message == "This response_format type is unavailable now"
    assert detail.request_id == "req-123"


def test_parse_llm_error_body_reads_top_level_request_id() -> None:
    detail = parse_llm_error_body(
        json.dumps({"message": "unknown model", "request_id": "top-level-1"})
    )

    assert detail.provider_message == "unknown model"
    assert detail.request_id == "top-level-1"


def test_parse_llm_error_body_keeps_non_json_text() -> None:
    detail = parse_llm_error_body("<html>502 Bad Gateway</html>")

    assert detail.parsed is False
    assert detail.provider_code is None
    assert detail.provider_message == "<html>502 Bad Gateway</html>"
    assert detail.raw_text == "<html>502 Bad Gateway</html>"


def test_parse_llm_error_body_truncates_huge_non_json_text() -> None:
    detail = parse_llm_error_body("x" * 5000)

    assert len(detail.raw_text) == 600
    assert detail.raw_text.endswith("...")


def test_parse_llm_error_body_truncates_huge_provider_message() -> None:
    detail = parse_llm_error_body(
        json.dumps({"error": {"message": "y" * 5000, "code": "invalid_request_error"}})
    )

    assert detail.provider_code == "invalid_request_error"
    assert detail.provider_message is not None
    assert len(detail.provider_message) == 600
    assert detail.provider_message.endswith("...")


def test_classify_llm_failure_json_schema_400_is_systematic() -> None:
    assert (
        classify_llm_failure(400, _DEEPSEEK_JSON_SCHEMA_ERROR)
        == LLM_SCOPE_SYSTEMATIC
    )


def test_classify_llm_failure_marks_context_and_size_errors_as_article() -> None:
    context = json.dumps(
        {"error": {"message": "This model's maximum context length is 8192 tokens"}}
    )
    too_large = json.dumps({"error": {"message": "Request entity too large"}})

    assert classify_llm_failure(400, context) == LLM_SCOPE_ARTICLE
    assert classify_llm_failure(413, too_large) == LLM_SCOPE_ARTICLE
    # 400/413 但没有长度/体积标记时仍是系统性错误
    assert classify_llm_failure(413, "unexpected failure") == LLM_SCOPE_SYSTEMATIC
    assert classify_llm_failure(400, "bad payload format") == LLM_SCOPE_SYSTEMATIC


def test_classify_llm_failure_other_4xx_are_systematic() -> None:
    for status_code in (401, 403, 404, 422):
        assert (
            classify_llm_failure(status_code, "invalid request")
            == LLM_SCOPE_SYSTEMATIC
        )


def test_classify_llm_failure_keeps_retryable_statuses_transient() -> None:
    assert classify_llm_failure(429, "rate limit exceeded") == LLM_FAILURE_TRANSIENT
    assert classify_llm_failure(500, "internal error") == LLM_FAILURE_TRANSIENT
    assert classify_llm_failure(408, "timeout") == LLM_FAILURE_TRANSIENT


def test_is_retryable_llm_status_covers_only_408_429_and_5xx() -> None:
    assert all(is_retryable_llm_status(code) for code in (408, 429, 500, 503))
    # 409/425 没有任何调用方放进 retryable_statuses，留在可重试集合里只会得到
    # 「retryable=True 但从不重试」的假语义，因此按不可重试处理。
    assert all(
        not is_retryable_llm_status(code)
        for code in (400, 401, 403, 404, 409, 422, 425)
    )


def test_classify_llm_failure_treats_409_and_425_as_systematic() -> None:
    assert classify_llm_failure(409, "conflict") == LLM_SCOPE_SYSTEMATIC
    assert classify_llm_failure(425, "too early") == LLM_SCOPE_SYSTEMATIC


def test_classify_llm_failure_prioritises_quota() -> None:
    assert (
        classify_llm_failure(400, "insufficient credits, maximum context length 8192")
        == LLM_FAILURE_QUOTA
    )
    assert classify_llm_failure(429, "余额不足，请充值") == LLM_FAILURE_QUOTA


def test_build_llm_request_error_describes_status_code_endpoint_model_and_id() -> None:
    error = build_llm_request_error(
        status_code=400,
        response_text=_DEEPSEEK_JSON_SCHEMA_ERROR,
        operation="beijing_gate",
        model="deepseek-flash",
        endpoint_label="DeepSeek",
    )

    assert error.scope == LLM_SCOPE_SYSTEMATIC
    assert error.retryable is False
    assert error.provider_code == "invalid_request_error"
    assert error.request_id == "req-123"
    described = str(error)
    for fragment in (
        "status=400",
        "scope=systematic",
        "retryable=no",
        "endpoint=DeepSeek",
        "model=deepseek-flash",
        "provider_code=invalid_request_error",
        "request_id=req-123",
        "This response_format type is unavailable now",
    ):
        assert fragment in described


def test_build_llm_request_error_marks_retryable_status() -> None:
    error = build_llm_request_error(
        status_code=503,
        response_text="upstream unavailable",
        operation="summarize",
        model="model-a",
    )

    assert error.retryable is True
    assert error.is_systematic is False
    assert error.is_article is False


def test_post_chat_completion_does_not_retry_400_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        llm_chat.requests,
        "post",
        _fake_status_post(400, _DEEPSEEK_JSON_SCHEMA_ERROR, calls),
    )

    with pytest.raises(LLMRequestError) as exc_info:
        post_chat_completion(
            "https://llm.example.test/chat/completions",
            payload={"model": "deepseek-flash"},
            headers={},
            timeout=1,
            budget=10,
            retries=3,
            retryable_statuses=_RETRYABLE_STATUSES,
            operation="beijing_gate",
            model="deepseek-flash",
            endpoint_label="DeepSeek",
            backoff_initial=0.0,
            allowed_hosts={"llm.example.test"},
        )

    assert len(calls) == 1
    error = exc_info.value
    assert error.status_code == 400
    assert error.scope == LLM_SCOPE_SYSTEMATIC
    assert error.retryable is False
    assert error.request_id == "req-123"
    assert error.endpoint_label == "DeepSeek"


def test_post_chat_completion_never_retries_400_even_if_listed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        llm_chat.requests,
        "post",
        _fake_status_post(400, "bad request", calls),
    )

    with pytest.raises(LLMRequestError):
        post_chat_completion(
            "https://llm.example.test/chat/completions",
            payload={"model": "model-a"},
            headers={},
            timeout=1,
            budget=10,
            retries=3,
            retryable_statuses={400},
            operation="test_canonical_guard",
            model="model-a",
            backoff_initial=0.0,
            allowed_hosts={"llm.example.test"},
        )

    assert len(calls) == 1


def test_post_chat_completion_retries_500_until_attempts_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        llm_chat.requests,
        "post",
        _fake_status_post(500, "upstream internal error", calls),
    )

    with pytest.raises(LLMRequestError) as exc_info:
        post_chat_completion(
            "https://llm.example.test/chat/completions",
            payload={"model": "model-a"},
            headers={},
            timeout=1,
            budget=10,
            retries=3,
            retryable_statuses={429, 500},
            operation="summarize",
            model="model-a",
            backoff_initial=0.0,
            allowed_hosts={"llm.example.test"},
        )

    assert len(calls) == 3
    assert exc_info.value.status_code == 500
    assert exc_info.value.retryable is True


def test_post_chat_completion_retries_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        llm_chat.requests,
        "post",
        _fake_status_post(429, "rate limit exceeded", calls),
    )

    with pytest.raises(LLMRequestError) as exc_info:
        post_chat_completion(
            "https://llm.example.test/chat/completions",
            payload={"model": "model-a"},
            headers={},
            timeout=1,
            budget=10,
            retries=2,
            retryable_statuses={429},
            operation="score",
            model="model-a",
            backoff_initial=0.0,
            allowed_hosts={"llm.example.test"},
        )

    assert len(calls) == 2
    assert exc_info.value.status_code == 429
    assert exc_info.value.retryable is True


def test_post_chat_completion_quota_error_wins_over_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    body = json.dumps({"error": {"message": "余额不足，请充值"}}, ensure_ascii=False)
    monkeypatch.setattr(
        llm_chat.requests,
        "post",
        _fake_status_post(429, body, calls),
    )
    monkeypatch.setattr(
        llm_chat,
        "get_settings",
        lambda: SimpleNamespace(llm_quota_alert_enabled=False),
    )

    with pytest.raises(LLMQuotaError):
        post_chat_completion(
            "https://llm.example.test/chat/completions",
            payload={"model": "model-a"},
            headers={},
            timeout=1,
            budget=10,
            retries=3,
            retryable_statuses={429},
            operation="score",
            model="model-a",
            backoff_initial=0.0,
            allowed_hosts={"llm.example.test"},
        )

    assert len(calls) == 1


def test_llm_quota_error_survives_context_manager_traceback_assignment() -> None:
    """RuntimeError 子类经过 contextlib 管理的 with 块时不能被 frozen 挡住。"""

    from contextlib import contextmanager

    @contextmanager
    def _guard():
        yield

    error = LLMQuotaError(
        operation="score",
        model="model-a",
        status_code=402,
        response_text="Insufficient Balance",
    )
    with pytest.raises(LLMQuotaError):
        with _guard():
            raise error
