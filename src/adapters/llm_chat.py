from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional
from urllib.parse import urlsplit

import requests

from src.config import Settings, get_settings

_QUOTA_ALERT_LOCK = threading.Lock()
_QUOTA_TEXT_LIMIT = 500
_QUOTA_KEYWORDS = (
    "insufficient_quota",
    "insufficient quota",
    "insufficient credits",
    "insufficient credit",
    "insufficient funds",
    "credit balance",
    "not enough credits",
    "not enough balance",
    "out of credits",
    "no credits",
    "balance",
    "billing",
    "payment_required",
    "payment required",
    "payment",
    "额度不足",
    "余额不足",
    "账户余额",
    "欠费",
)
_QUOTA_CHECK_STATUSES = {400, 401, 403, 429}

# 失败归属分四类，判定顺序即优先级：额度/计费 > 临时错误 > 文章级 > 系统性。
# 只有 systematic 与 article 会写进 LLMRequestError.scope；quota 与 transient
# 由调用方按各自路径处理（额度告警 / 保持临时错误语义）。
LLM_FAILURE_QUOTA = "quota"
LLM_FAILURE_TRANSIENT = "transient"
LLM_SCOPE_SYSTEMATIC = "systematic"
LLM_SCOPE_ARTICLE = "article"
LLM_REQUEST_SCOPES = frozenset({LLM_SCOPE_SYSTEMATIC, LLM_SCOPE_ARTICLE})

# 值得重试的状态码：408（请求超时）、429（限流）与全部 5xx。其余 4xx
# （400/401/403/404/409/422/425…）是确定性请求错误，重试只会把一次故障放大
# 成 N 次；409/425 也不在重试集合里——没有任何调用方把它们放进
# retryable_statuses，若留在集合里只会得到「retryable=True 但从不重试」的
# 假语义，反而让 geo_classify 把它们当临时错误去走兜底。
_RETRYABLE_STATUS_CODES = frozenset({408, 429})

# 只有 400/413 且正文指向「这一篇的上下文/长度/体积超限」才算文章级错误。
_ARTICLE_SCOPE_STATUS_CODES = frozenset({400, 413})
_ARTICLE_SCOPE_MARKERS = (
    "context length",
    "maximum context",
    "max context",
    "too many tokens",
    "token limit",
    "prompt is too long",
    "request entity too large",
    "payload too large",
    "content too large",
    "string too long",
)
_PROVIDER_TEXT_LIMIT = 600
_REQUEST_ID_PATTERN = re.compile(
    r"[（(]?\s*request[_ ]?id\s*[:：]\s*([A-Za-z0-9._:\-]+)\s*[)）]?",
    re.IGNORECASE,
)


# 注意：这两个异常故意不用 frozen dataclass。它们是 RuntimeError 子类，
# 经过 contextlib 管理的 with 块（例如 worker_session）时，contextlib 会做
# Python 级 `exc.__traceback__ = ...` 赋值，frozen 会抛 FrozenInstanceError
# 把真正的错误吞掉。
@dataclass
class LLMQuotaError(RuntimeError):
    operation: str
    model: str
    status_code: int
    response_text: str
    endpoint_label: Optional[str] = None

    def __post_init__(self) -> None:
        RuntimeError.__init__(
            self,
            f"LLM quota or billing error during {self.operation} "
            f"(model={self.model}, status={self.status_code}): {self.response_text}",
        )


@dataclass(frozen=True)
class LLMProviderErrorDetail:
    """Parse result of a provider error body.

    ``provider_message`` 已去掉 ``(request_id: ...)`` 片段（请求 id 单独放在
    ``request_id``），这样同一种错误在不同请求之间保持同一文案。
    """

    provider_code: Optional[str] = None
    provider_message: Optional[str] = None
    request_id: Optional[str] = None
    raw_text: str = ""
    parsed: bool = False


@dataclass
class LLMRequestError(RuntimeError):
    """A typed terminal LLM HTTP failure.

    ``scope`` 区分两类归属：

    - ``systematic``：代码、配置、接入点能力（如不支持 ``response_format``
      的类型）、模型名或鉴权问题。这类错误与具体文章无关，重复请求必然失败。
    - ``article``：这一篇文章的数据本身超限（上下文/长度/体积类 400/413）。

    ``retryable`` 是状态码层面的临时性判定（408/429/5xx 为 True）。
    临时错误仍按原有重试/计数/兜底路径处理，不要用 ``scope`` 判断。
    """

    operation: str
    model: str
    status_code: int
    scope: str
    retryable: bool = False
    endpoint_label: Optional[str] = None
    provider_code: Optional[str] = None
    provider_message: Optional[str] = None
    request_id: Optional[str] = None
    raw_response_text: str = ""

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.describe())

    def describe(self) -> str:
        return (
            f"LLM request failed during {self.operation} "
            f"(status={self.status_code}, scope={self.scope}, "
            f"retryable={'yes' if self.retryable else 'no'}, "
            f"endpoint={self.endpoint_label or '-'}, model={self.model}, "
            f"provider_code={self.provider_code or '-'}, "
            f"request_id={self.request_id or '-'}): "
            f"{self.provider_message or '(no provider message)'}"
        )

    @property
    def is_systematic(self) -> bool:
        return not self.retryable and self.scope == LLM_SCOPE_SYSTEMATIC

    @property
    def is_article(self) -> bool:
        return not self.retryable and self.scope == LLM_SCOPE_ARTICLE


class LLMEndpointBlockedError(RuntimeError):
    """Raised when the target host is not on the LLM_ALLOWED_HOSTS allowlist."""


class LLMWallClockTimeout(requests.Timeout):
    """Raised when one business call exhausts its total LLM time budget."""

    def __init__(self, *, operation: str, budget: float) -> None:
        self.operation = operation
        self.budget = budget
        super().__init__(
            f"LLM wall-clock budget exhausted during {self.operation} "
            f"(budget={self.budget:g}s)",
        )


class _StopRetry(Exception):
    def __init__(self, error: Exception) -> None:
        super().__init__(str(error))
        self.error = error


def ensure_url_host_allowed(
    url: str,
    *,
    allowed_hosts: Optional[Collection[str]] = None,
) -> str:
    """Block requests to hosts outside ``LLM_ALLOWED_HOSTS`` before sending.

    The allowlist keeps console-editable endpoint URLs from becoming a way to
    exfiltrate API keys to arbitrary servers or probe internal networks.
    """

    if allowed_hosts is None:
        allowed_hosts = get_settings().llm_allowed_hosts
    host = urlsplit(url).hostname
    normalized_host = (host or "").strip().lower()
    normalized_allowed = {
        item.strip().lower() for item in allowed_hosts if item and item.strip()
    }
    if not normalized_host or normalized_host not in normalized_allowed:
        raise LLMEndpointBlockedError(
            f"接入点主机 {normalized_host or '(缺失)'} 不在 LLM_ALLOWED_HOSTS "
            f"白名单内，已按安全策略阻止请求：{url}"
        )
    return normalized_host


def post_chat_completion(
    url: str,
    *,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: float,
    budget: float,
    retries: int,
    retryable_statuses: Collection[int],
    operation: str,
    model: str,
    deadline: Optional[float] = None,
    backoff_initial: float = 1.0,
    backoff_multiplier: float = 2.0,
    backoff_max: float = 8.0,
    advance_backoff_on_exception: bool = True,
    retry_non_retryable_statuses: bool = False,
    response_validator: Optional[Callable[[dict[str, Any]], None]] = None,
    non_retryable_exceptions: tuple[type[Exception], ...] = (),
    http_error_factory: Optional[Callable[[int, str], Exception]] = None,
    attempt_callback: Optional[Callable[[int], None]] = None,
    endpoint_label: Optional[str] = None,
    allowed_hosts: Optional[Collection[str]] = None,
) -> dict[str, Any]:
    """POST one chat task with retries bounded by a shared wall-clock deadline.

    ``timeout`` remains the per-request socket timeout. ``budget`` limits the
    complete business call, including response reads, retries, and backoff.
    Callers with outer semantic retries may pass the same absolute ``deadline``
    to every invocation. The optional callback receives each HTTP attempt number.

    Non-2xx responses raise a typed :class:`LLMRequestError` (unless
    ``http_error_factory`` is supplied). Only statuses in
    ``retryable_statuses`` that are also retryable by nature (408/429 or 5xx)
    are retried; 400/401/403/404/409/422/425 fail after a single request.
    Passing ``retry_non_retryable_statuses=True`` restores the legacy
    retry-everything behaviour and should be treated as an explicit,
    documented exception.
    """

    ensure_url_host_allowed(url, allowed_hosts=allowed_hosts)
    resolved_deadline = deadline if deadline is not None else time.monotonic() + budget
    attempts = max(1, retries)
    backoff = max(0.0, backoff_initial)
    last_error: Optional[Exception] = None

    for attempt in range(1, attempts + 1):
        request_timeout = _remaining_request_timeout(
            deadline=resolved_deadline,
            timeout=timeout,
            operation=operation,
            budget=budget,
        )
        if attempt_callback is not None:
            attempt_callback(attempt)

        response: Optional[requests.Response] = None
        status_failure = False
        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=request_timeout,
                stream=True,
                allow_redirects=False,
            )
            if 300 <= response.status_code < 400:
                raise _StopRetry(
                    RuntimeError(
                        "接入点返回重定向，按安全策略未跟随"
                        f"（HTTP {response.status_code}）"
                    )
                )
            raw_body = _read_response_body(
                response,
                deadline=resolved_deadline,
                operation=operation,
                budget=budget,
            )
            if response.status_code == 200:
                data = json.loads(raw_body)
                if not isinstance(data, dict):
                    raise ValueError("LLM response body must be a JSON object")
                if response_validator is not None:
                    response_validator(data)
                _raise_if_deadline_exceeded(
                    deadline=resolved_deadline,
                    operation=operation,
                    budget=budget,
                )
                return data

            response_text = _decode_response_body(response, raw_body)
            _raise_if_deadline_exceeded(
                deadline=resolved_deadline,
                operation=operation,
                budget=budget,
            )
            raise_for_llm_quota_error(
                status_code=response.status_code,
                response_text=response_text,
                operation=operation,
                model=model,
                endpoint_label=endpoint_label,
            )
            if http_error_factory is not None:
                http_error = http_error_factory(
                    response.status_code, response_text
                )
            else:
                http_error = build_llm_request_error(
                    status_code=response.status_code,
                    response_text=response_text,
                    operation=operation,
                    model=model,
                    endpoint_label=endpoint_label,
                )
            status_retryable = is_retryable_llm_status(
                response.status_code
            ) and response.status_code in retryable_statuses
            if not status_retryable and not retry_non_retryable_statuses:
                raise _StopRetry(http_error)
            last_error = http_error
            status_failure = True
        except _StopRetry as exc:
            raise exc.error
        except (LLMQuotaError, LLMWallClockTimeout):
            raise
        except Exception as exc:
            if isinstance(exc, non_retryable_exceptions):
                raise
            _raise_if_deadline_exceeded(
                deadline=resolved_deadline,
                operation=operation,
                budget=budget,
                cause=exc,
            )
            last_error = exc
        finally:
            if response is not None:
                response.close()

        if attempt >= attempts:
            break

        remaining = resolved_deadline - time.monotonic()
        if remaining <= 0:
            raise LLMWallClockTimeout(operation=operation, budget=budget)
        time.sleep(min(backoff, remaining))
        _raise_if_deadline_exceeded(
            deadline=resolved_deadline,
            operation=operation,
            budget=budget,
        )
        if status_failure or advance_backoff_on_exception:
            backoff = min(backoff * backoff_multiplier, backoff_max)

    raise last_error or RuntimeError(f"LLM call failed during {operation}")


def _remaining_request_timeout(
    *,
    deadline: float,
    timeout: float,
    operation: str,
    budget: float,
) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LLMWallClockTimeout(operation=operation, budget=budget)
    return min(float(timeout), remaining)


def _read_response_body(
    response: requests.Response,
    *,
    deadline: float,
    operation: str,
    budget: float,
) -> bytes:
    body = bytearray()
    _raise_if_deadline_exceeded(
        deadline=deadline,
        operation=operation,
        budget=budget,
    )
    while True:
        chunk = response.raw.read1(8192, decode_content=True)
        if not chunk:
            break
        _raise_if_deadline_exceeded(
            deadline=deadline,
            operation=operation,
            budget=budget,
        )
        body.extend(chunk)
        _raise_if_deadline_exceeded(
            deadline=deadline,
            operation=operation,
            budget=budget,
        )
    _raise_if_deadline_exceeded(
        deadline=deadline,
        operation=operation,
        budget=budget,
    )
    return bytes(body)


def _decode_response_body(response: requests.Response, body: bytes) -> str:
    return body.decode(response.encoding or "utf-8", errors="replace")


def _raise_if_deadline_exceeded(
    *,
    deadline: float,
    operation: str,
    budget: float,
    cause: Optional[Exception] = None,
) -> None:
    if time.monotonic() < deadline:
        return
    error = LLMWallClockTimeout(operation=operation, budget=budget)
    if cause is None:
        raise error
    raise error from cause


def build_headers(
    *,
    api_key: str,
    referer: Optional[str],
    title: Optional[str],
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title
    return headers


def apply_reasoning_config(
    payload: MutableMapping[str, Any],
    *,
    settings: Settings,
    enabled: bool,
) -> None:
    if not enabled:
        return

    effort = (settings.llm_reasoning_effort or "").strip().lower()
    max_tokens = settings.llm_reasoning_max_tokens
    exclude = settings.llm_reasoning_exclude

    reasoning: dict[str, Any] = {"enabled": True}
    if effort:
        reasoning["effort"] = effort
    if max_tokens is not None and max_tokens > 0:
        reasoning["max_tokens"] = max_tokens
    if exclude:
        reasoning["exclude"] = True
    payload["reasoning"] = reasoning


def extract_message_text(choice: Mapping[str, Any]) -> str:
    message = choice.get("message")
    if isinstance(message, Mapping):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        reasoning = message.get("reasoning")
        if isinstance(reasoning, str) and reasoning.strip():
            return reasoning.strip()

    reasoning_content = choice.get("reasoning_content")
    if isinstance(reasoning_content, str) and reasoning_content.strip():
        return reasoning_content.strip()
    if isinstance(reasoning_content, list):
        flattened = " ".join(str(part).strip() for part in reasoning_content if part)
        if flattened.strip():
            return flattened.strip()

    return ""


def is_llm_quota_response(status_code: int, response_text: str) -> bool:
    if status_code == 402:
        return True
    if status_code not in _QUOTA_CHECK_STATUSES:
        return False
    lowered = response_text.lower()
    return any(keyword in lowered for keyword in _QUOTA_KEYWORDS)


def is_retryable_llm_status(status_code: int) -> bool:
    """Return True only for 408/429 and every 5xx.

    Everything else — notably 400/401/403/404/409/422/425 — is a deterministic
    request error: retrying it only multiplies the failing requests.
    """

    return status_code in _RETRYABLE_STATUS_CODES or 500 <= status_code <= 599


def parse_llm_error_body(response_text: str) -> LLMProviderErrorDetail:
    """Extract provider code/message/request_id; never raises on malformed bodies.

    Handles ``{"error": {"message", "code", "request_id"}}``, a top-level
    ``message``/``code``/``request_id``, an ``error`` string, and providers that
    only embed the request id in the message as ``(request_id: xxx)``. A body
    that is not JSON keeps a truncated copy of the raw text as the message.
    """

    raw_text = _truncate_response(response_text or "", _PROVIDER_TEXT_LIMIT)
    payload: Any = None
    text = (response_text or "").strip()
    if text:
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            payload = None

    provider_code: Optional[str] = None
    provider_message: Optional[str] = None
    request_id: Optional[str] = None
    if isinstance(payload, Mapping):
        error_block = payload.get("error")
        if isinstance(error_block, Mapping):
            provider_code = _string_or_none(error_block.get("code"))
            provider_message = _string_or_none(error_block.get("message"))
            request_id = _string_or_none(error_block.get("request_id"))
        elif isinstance(error_block, str):
            provider_message = error_block
        if provider_code is None:
            provider_code = _string_or_none(payload.get("code"))
        if provider_message is None:
            provider_message = _string_or_none(payload.get("message"))
        if provider_message is None:
            provider_message = _string_or_none(payload.get("error_description"))
        if request_id is None:
            request_id = _string_or_none(payload.get("request_id"))

    if request_id is None:
        request_id = _extract_request_id(provider_message) or _extract_request_id(
            raw_text
        )
    cleaned_message = _strip_request_id(provider_message) or raw_text
    # 日志与飞书指纹都直接用 provider_message，这里必须同样截断，避免超大
    # 错误体（HTML 网关页等）原样进入日志。
    provider_message = (
        _truncate_response(cleaned_message, _PROVIDER_TEXT_LIMIT)
        if cleaned_message
        else None
    )

    return LLMProviderErrorDetail(
        provider_code=provider_code,
        provider_message=provider_message,
        request_id=request_id,
        raw_text=raw_text,
        parsed=isinstance(payload, Mapping),
    )


def classify_llm_failure(status_code: int, response_text: str) -> str:
    """Classify a non-2xx LLM response.

    Order matters: quota/billing keywords win over everything (an HTTP 400 may
    still be a billing problem), then retryable statuses stay transient, then
    400/413 with context/length/size markers are article-scoped, and every other
    status is systematic.
    """

    if is_llm_quota_response(status_code, response_text):
        return LLM_FAILURE_QUOTA
    if is_retryable_llm_status(status_code):
        return LLM_FAILURE_TRANSIENT
    if status_code in _ARTICLE_SCOPE_STATUS_CODES:
        detail = parse_llm_error_body(response_text)
        haystack = " ".join(
            part for part in (detail.provider_message, detail.raw_text) if part
        ).lower()
        if any(marker in haystack for marker in _ARTICLE_SCOPE_MARKERS):
            return LLM_SCOPE_ARTICLE
    return LLM_SCOPE_SYSTEMATIC


def build_llm_request_error(
    *,
    status_code: int,
    response_text: str,
    operation: str,
    model: str,
    endpoint_label: Optional[str] = None,
) -> LLMRequestError:
    """Build the typed error raised for a non-2xx HTTP response."""

    detail = parse_llm_error_body(response_text)
    kind = classify_llm_failure(status_code, response_text)
    scope = LLM_SCOPE_ARTICLE if kind == LLM_SCOPE_ARTICLE else LLM_SCOPE_SYSTEMATIC
    return LLMRequestError(
        operation=operation,
        model=model,
        status_code=status_code,
        scope=scope,
        retryable=is_retryable_llm_status(status_code),
        endpoint_label=endpoint_label,
        provider_code=detail.provider_code,
        provider_message=detail.provider_message,
        request_id=detail.request_id,
        raw_response_text=detail.raw_text,
    )


def _string_or_none(value: Any) -> Optional[str]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).strip()
    return text or None


def _extract_request_id(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    match = _REQUEST_ID_PATTERN.search(value)
    if not match:
        return None
    return match.group(1).strip() or None


def _strip_request_id(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = " ".join(_REQUEST_ID_PATTERN.sub(" ", value).split())
    return cleaned or None


def raise_for_llm_quota_error(
    *,
    status_code: int,
    response_text: str,
    operation: str,
    model: str,
    endpoint_label: Optional[str] = None,
) -> None:
    if not is_llm_quota_response(status_code, response_text):
        return

    error = LLMQuotaError(
        operation=operation,
        model=model,
        status_code=status_code,
        response_text=_truncate_response(response_text, _QUOTA_TEXT_LIMIT),
        endpoint_label=endpoint_label,
    )
    _maybe_send_quota_alert(error)
    raise error


def _truncate_response(value: str, limit: int) -> str:
    cleaned = " ".join((value or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)] + "..."


def _maybe_send_quota_alert(error: LLMQuotaError) -> None:
    settings = get_settings()
    if not settings.llm_quota_alert_enabled:
        return

    with _QUOTA_ALERT_LOCK:
        state_path = settings.llm_quota_alert_state_path
        now = time.time()
        if not _quota_alert_due(
            state_path,
            now=now,
            cooldown_seconds=settings.llm_quota_alert_cooldown_seconds,
        ):
            return

        try:
            from src.notifications.feishu import (
                FeishuConfigError,
                FeishuRequestError,
                notify_llm_quota_alert,
            )

            notify_llm_quota_alert(
                operation=(
                    f"{error.operation}（接入点：{error.endpoint_label}）"
                    if error.endpoint_label
                    else error.operation
                ),
                model=error.model,
                status_code=error.status_code,
                response_text=error.response_text,
            )
        except FeishuConfigError as exc:
            print(f"[llm_alert] Feishu notification skipped: {exc}")
        except FeishuRequestError as exc:
            print(f"[llm_alert] Feishu notification failed: {exc}")
        except Exception as exc:
            print(f"[llm_alert] Feishu notification unexpected error: {exc}")
        else:
            _write_quota_alert_state(state_path, now=now)


def _quota_alert_due(state_path: Path, *, now: float, cooldown_seconds: int) -> bool:
    last_alert_at = _read_last_quota_alert_at(state_path)
    if last_alert_at is None:
        return True
    return now - last_alert_at >= max(0, cooldown_seconds)


def _read_last_quota_alert_at(state_path: Path) -> Optional[float]:
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get("last_alert_at") if isinstance(data, Mapping) else None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _write_quota_alert_state(state_path: Path, *, now: float) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({"last_alert_at": now}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"[llm_alert] failed to write alert state: {exc}")


__all__ = [
    "LLM_FAILURE_QUOTA",
    "LLM_FAILURE_TRANSIENT",
    "LLM_REQUEST_SCOPES",
    "LLM_SCOPE_ARTICLE",
    "LLM_SCOPE_SYSTEMATIC",
    "LLMEndpointBlockedError",
    "LLMProviderErrorDetail",
    "LLMQuotaError",
    "LLMRequestError",
    "LLMWallClockTimeout",
    "apply_reasoning_config",
    "build_headers",
    "build_llm_request_error",
    "classify_llm_failure",
    "ensure_url_host_allowed",
    "extract_message_text",
    "is_llm_quota_response",
    "is_retryable_llm_status",
    "parse_llm_error_body",
    "post_chat_completion",
    "raise_for_llm_quota_error",
]
