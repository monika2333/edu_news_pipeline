from __future__ import annotations

import pytest

from src.adapters.llm_chat import (
    LLM_SCOPE_ARTICLE,
    LLM_SCOPE_SYSTEMATIC,
    LLMEndpointBlockedError,
    LLMQuotaError,
    LLMRequestError,
)
from src.adapters.llm_endpoint import LLMEndpointKeyMissingError
from src.business_config import BusinessConfigError
from src.workers import llm_failures


def _systematic_request_error() -> LLMRequestError:
    return LLMRequestError(
        operation="external_filter:internal_positive",
        model="deepseek-flash",
        status_code=400,
        scope=LLM_SCOPE_SYSTEMATIC,
        retryable=False,
        endpoint_label="DeepSeek",
        provider_code="invalid_request_error",
        provider_message="This response_format type is unavailable now",
        request_id="req-systematic-1",
    )


def _article_request_error() -> LLMRequestError:
    return LLMRequestError(
        operation="external_filter:external_positive",
        model="model-a",
        status_code=400,
        scope=LLM_SCOPE_ARTICLE,
        retryable=False,
        provider_message="This model's maximum context length is 8192 tokens",
    )


def _retryable_request_error() -> LLMRequestError:
    return LLMRequestError(
        operation="summarize",
        model="model-a",
        status_code=503,
        scope=LLM_SCOPE_SYSTEMATIC,
        retryable=True,
        provider_message="upstream unavailable",
    )


_SYSTEMATIC_ERRORS = [
    _systematic_request_error(),
    LLMQuotaError(
        operation="summarize",
        model="model-a",
        status_code=402,
        response_text="Insufficient Balance",
    ),
    LLMEndpointKeyMissingError("Missing LLM API key for endpoint 'DeepSeek'"),
    LLMEndpointBlockedError("接入点主机不在 LLM_ALLOWED_HOSTS 白名单内"),
    BusinessConfigError("缺少模型配置步骤：beijing_gate"),
]

_NON_SYSTEMATIC_ERRORS = [
    _article_request_error(),
    _retryable_request_error(),
    RuntimeError("model returned empty response"),
    TimeoutError("read timed out"),
]


@pytest.mark.parametrize(
    "error",
    _SYSTEMATIC_ERRORS,
    ids=["request", "quota", "missing_key", "blocked_host", "business_config"],
)
def test_is_systematic_llm_error_detects_systematic_failures(error: Exception) -> None:
    assert llm_failures.is_systematic_llm_error(error) is True


@pytest.mark.parametrize(
    "error",
    _NON_SYSTEMATIC_ERRORS,
    ids=["article_scope", "retryable", "runtime", "timeout"],
)
def test_is_systematic_llm_error_rejects_article_and_transient_failures(
    error: Exception,
) -> None:
    assert llm_failures.is_systematic_llm_error(error) is False


def test_systematic_error_alert_sends_at_most_once_per_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alerts: list[dict[str, object]] = []
    monkeypatch.setattr(
        llm_failures,
        "notify_llm_request_alert",
        lambda **kwargs: alerts.append(kwargs) or True,
    )
    alert = llm_failures.SystematicErrorAlert(
        operation="external_filter",
        worker="external_filter",
    )

    alert.notify(_systematic_request_error())
    alert.notify(_systematic_request_error())
    alert.notify(BusinessConfigError("broken"))

    assert len(alerts) == 1
    assert alerts[0]["operation"] == "external_filter"


def test_systematic_error_alert_skips_quota_without_using_the_run_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alerts: list[dict[str, object]] = []
    monkeypatch.setattr(
        llm_failures,
        "notify_llm_request_alert",
        lambda **kwargs: alerts.append(kwargs) or True,
    )
    alert = llm_failures.SystematicErrorAlert(
        operation="summarize",
        worker="summarize",
    )

    alert.notify(
        LLMQuotaError(
            operation="summarize",
            model="model-a",
            status_code=402,
            response_text="Insufficient Balance",
        )
    )
    alert.notify(_systematic_request_error())

    assert len(alerts) == 1
    assert alerts[0]["operation"] == "summarize"


def test_systematic_error_alert_swallows_delivery_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def broken_alert(**kwargs: object) -> bool:
        raise RuntimeError("feishu down")

    monkeypatch.setattr(llm_failures, "notify_llm_request_alert", broken_alert)
    alert = llm_failures.SystematicErrorAlert(
        operation="summarize",
        worker="summarize",
    )

    alert.notify(BusinessConfigError("broken"))

    assert "LLM request alert failed: feishu down" in capsys.readouterr().out
