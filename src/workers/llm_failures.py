from __future__ import annotations

from typing import Any, Optional

from src.adapters.llm_chat import (
    LLMEndpointBlockedError,
    LLMQuotaError,
    LLMRequestError,
)
from src.adapters.llm_endpoint import LLMEndpointKeyMissingError
from src.business_config import BusinessConfigError
from src.notifications.feishu import notify_llm_request_alert

# 发生在请求发出之前的配置类错误：缺少 API Key、接入点主机被白名单拦截、
# 业务配置缺步骤/接入点。它们与具体文章无关，重复请求同一批文章只会得到
# 同样的失败，因此和 HTTP 层的系统性错误同等对待。
ENDPOINT_CONFIG_ERRORS: tuple[type[Exception], ...] = (
    LLMEndpointKeyMissingError,
    LLMEndpointBlockedError,
    BusinessConfigError,
)


def is_systematic_llm_error(exc: Exception) -> bool:
    """Return True when ``exc`` is unrelated to the article being processed.

    Covers systematic :class:`LLMRequestError` values (code/config/endpoint
    capability problems), :class:`LLMQuotaError`, and the configuration errors
    raised before any HTTP request (missing API key, blocked host, broken
    business config). Transient errors (5xx/408/429/timeouts/network), article
    scope errors and non-LLM failures return False and keep the caller's
    existing count/fallback path.
    """

    if isinstance(exc, LLMRequestError):
        return exc.is_systematic
    if isinstance(exc, LLMQuotaError):
        return True
    return isinstance(exc, ENDPOINT_CONFIG_ERRORS)


class SystematicErrorAlert:
    """Send at most one LLM request-error alert per worker run.

    The throttle is an in-memory flag owned by one ``run()`` call. Concurrent
    failures are handled on the main thread (after ``future.result()``), so a
    plain attribute is enough; the cross-run cooldown still lives in the Feishu
    fingerprint state file.
    """

    def __init__(self, *, operation: str, worker: str) -> None:
        self.operation = operation
        self.worker = worker
        self.sent = False

    def notify(self, exc: Exception) -> None:
        # 额度错误已有独立的 LLMQuotaError 告警，不重复发，也不占用本轮的
        # 「一条请求错误告警」名额。
        if self.sent or isinstance(exc, LLMQuotaError):
            return
        self.sent = True
        _send_request_alert(exc, operation=self.operation, worker=self.worker)


def _send_request_alert(exc: Exception, *, operation: str, worker: str) -> None:
    if isinstance(exc, LLMRequestError):
        alert_fields: dict[str, Any] = {
            "operation": operation,
            "endpoint_label": exc.endpoint_label,
            "model": exc.model,
            "status_code": exc.status_code,
            "provider_code": exc.provider_code,
            "provider_message": exc.provider_message,
            "request_id": exc.request_id,
        }
    elif isinstance(exc, ENDPOINT_CONFIG_ERRORS):
        # 请求在发出前就失败，没有 HTTP 状态、request id，也拿不到已解析的
        # 接入点与模型：用异常类名当 provider code，消息里显示 unknown。
        alert_fields = {
            "operation": operation,
            "endpoint_label": None,
            "model": None,
            "status_code": None,
            "provider_code": type(exc).__name__,
            "provider_message": str(exc),
            "request_id": None,
        }
    else:  # pragma: no cover - notify() filters quota before reaching here
        return
    try:
        notify_llm_request_alert(**alert_fields)
    except Exception as alert_exc:  # 告警是旁路，失败不能打断步骤
        print(f"[{worker}] LLM request alert failed: {alert_exc}")


__all__ = [
    "ENDPOINT_CONFIG_ERRORS",
    "SystematicErrorAlert",
    "is_systematic_llm_error",
]
