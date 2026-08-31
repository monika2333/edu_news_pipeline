from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional

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


@dataclass(frozen=True)
class LLMQuotaError(RuntimeError):
    operation: str
    model: str
    status_code: int
    response_text: str

    def __post_init__(self) -> None:
        RuntimeError.__init__(
            self,
            f"LLM quota or billing error during {self.operation} "
            f"(model={self.model}, status={self.status_code}): {self.response_text}",
        )


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
    retry_non_retryable_statuses: bool = True,
    response_validator: Optional[Callable[[dict[str, Any]], None]] = None,
    non_retryable_exceptions: tuple[type[Exception], ...] = (),
    http_error_factory: Optional[Callable[[int, str], Exception]] = None,
    attempt_callback: Optional[Callable[[int], None]] = None,
) -> dict[str, Any]:
    """POST one chat task with retries bounded by a shared wall-clock deadline.

    ``timeout`` remains the per-request socket timeout. ``budget`` limits the
    complete business call, including response reads, retries, and backoff.
    Callers with outer semantic retries may pass the same absolute ``deadline``
    to every invocation. The optional callback receives each HTTP attempt number.
    """

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
            )
            error_factory = http_error_factory or _default_http_error
            http_error = error_factory(response.status_code, response_text)
            if (
                response.status_code not in retryable_statuses
                and not retry_non_retryable_statuses
            ):
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
    # A one-byte chunk ensures provider keepalives are yielded immediately
    # instead of being buffered until a larger application chunk is full.
    for chunk in response.iter_content(chunk_size=1):
        _raise_if_deadline_exceeded(
            deadline=deadline,
            operation=operation,
            budget=budget,
        )
        if chunk:
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


def _default_http_error(status_code: int, response_text: str) -> Exception:
    return RuntimeError(f"API {status_code}: {response_text[:160]}")


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


def raise_for_llm_quota_error(
    *,
    status_code: int,
    response_text: str,
    operation: str,
    model: str,
) -> None:
    if not is_llm_quota_response(status_code, response_text):
        return

    error = LLMQuotaError(
        operation=operation,
        model=model,
        status_code=status_code,
        response_text=_truncate_response(response_text, _QUOTA_TEXT_LIMIT),
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
                operation=error.operation,
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
    "LLMQuotaError",
    "LLMWallClockTimeout",
    "apply_reasoning_config",
    "build_headers",
    "extract_message_text",
    "is_llm_quota_response",
    "post_chat_completion",
    "raise_for_llm_quota_error",
]
