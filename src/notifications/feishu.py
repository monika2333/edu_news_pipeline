from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Optional

import requests

from src.config import get_settings

_FEISHU_API_ROOT = "https://open.feishu.cn/open-apis"
_TOKEN_URL = f"{_FEISHU_API_ROOT}/auth/v3/tenant_access_token/internal/"
_MESSAGE_URL = f"{_FEISHU_API_ROOT}/im/v1/messages"
_DEFAULT_TIMEOUT = 10
_WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
_REQUEST_ALERT_STATE_LIMIT = 50
_REQUEST_ID_TEXT_PATTERN = re.compile(
    r"[（(]?\s*request[_ ]?id\s*[:：]\s*[A-Za-z0-9._:\-]+\s*[)）]?",
    re.IGNORECASE,
)


class FeishuConfigError(RuntimeError):
    """Raised when Feishu credentials are missing or invalid."""


class FeishuRequestError(RuntimeError):
    """Raised when Feishu HTTP calls fail or return errors."""


@dataclass(frozen=True)
class _FeishuConfig:
    app_id: str
    app_secret: str
    receive_id: str
    receive_id_type: str


@dataclass
class _TokenCache:
    token: str
    expires_at: datetime

    def is_valid(self) -> bool:
        return datetime.now(timezone.utc) < self.expires_at


_token_cache: Optional[_TokenCache] = None
_token_lock = threading.Lock()
_request_alert_lock = threading.Lock()


def notify_llm_quota_alert(
    *,
    operation: str,
    model: str,
    status_code: int,
    response_text: str,
) -> bool:
    """Send a Feishu text notification for LLM quota or billing failures."""
    config = _load_config()
    occurred_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    message = _render_llm_quota_alert_message(
        operation=operation,
        model=model,
        status_code=status_code,
        response_text=response_text,
        occurred_at=occurred_at,
    )
    _send_text_message(config, message)
    return True


def notify_llm_request_alert(
    *,
    operation: str,
    endpoint_label: Optional[str],
    model: Optional[str] = None,
    status_code: Optional[int] = None,
    provider_code: Optional[str] = None,
    provider_message: Optional[str] = None,
    request_id: Optional[str] = None,
) -> bool:
    """Send a Feishu alert for a systematic (non-retryable) LLM request failure.

    Covers failures that never produced a provider response — missing API key,
    endpoint host blocked by the allowlist, broken business config — by allowing
    ``status_code`` to be None (rendered as "no HTTP response") and
    ``endpoint_label``/``model`` to be unknown.

    Alerts are throttled per fingerprint — status + provider code + provider
    message + endpoint label + model — for
    ``LLM_REQUEST_ALERT_COOLDOWN_SECONDS``. The throttle state lives in
    ``LLM_REQUEST_ALERT_STATE_PATH`` so separate scheduled runs share one
    window. Returns True only when a message was actually sent; delivery
    problems are printed and swallowed because an alert outage must never break
    the pipeline.
    """

    settings = get_settings()
    if not settings.llm_request_alert_enabled:
        return False

    fingerprint = _request_alert_fingerprint(
        status_code=status_code,
        provider_code=provider_code,
        provider_message=provider_message,
        endpoint_label=endpoint_label,
        model=model,
    )
    occurred_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    message = _render_llm_request_alert_message(
        operation=operation,
        endpoint_label=endpoint_label,
        model=model,
        status_code=status_code,
        provider_code=provider_code,
        provider_message=provider_message,
        request_id=request_id,
        occurred_at=occurred_at,
    )

    with _request_alert_lock:
        now = time.time()
        state_path = settings.llm_request_alert_state_path
        entries = _read_request_alert_state(state_path)
        last_alert_at = entries.get(fingerprint)
        cooldown = max(0, settings.llm_request_alert_cooldown_seconds)
        if last_alert_at is not None and now - last_alert_at < cooldown:
            return False
        try:
            config = _load_config()
            _send_text_message(config, message)
        except FeishuConfigError as exc:
            print(f"[llm_alert] Feishu request alert skipped: {exc}")
            return False
        except FeishuRequestError as exc:
            print(f"[llm_alert] Feishu request alert failed: {exc}")
            return False
        except Exception as exc:  # 告警是旁路，任何异常都不能中断流水线
            print(f"[llm_alert] Feishu request alert unexpected error: {exc}")
            return False
        entries[fingerprint] = now
        _write_request_alert_state(state_path, entries)
        return True


def notify_console_registration(
    *,
    username: str,
    display_name: str,
    preferred_weekday: int,
) -> bool:
    """Notify the configured recipient after an editor self-registers."""
    if preferred_weekday not in range(7):
        raise ValueError("preferred_weekday must be between 0 and 6")
    config = _load_config()
    occurred_at = datetime.now(timezone.utc).astimezone().isoformat(
        timespec="seconds"
    )
    message = _render_console_registration_message(
        username=username,
        display_name=display_name,
        preferred_weekday=preferred_weekday,
        occurred_at=occurred_at,
    )
    _send_text_message(config, message)
    return True


def send_text_to_chat(*, chat_id: str, message: str) -> bool:
    """Send a text reply to a Feishu chat using the configured application."""
    normalized_chat_id = chat_id.strip()
    if not normalized_chat_id:
        raise ValueError("chat_id must not be empty")
    settings = get_settings()
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        raise FeishuConfigError(
            "Feishu credentials missing. Set FEISHU_APP_ID and FEISHU_APP_SECRET."
        )
    config = _FeishuConfig(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        receive_id=normalized_chat_id,
        receive_id_type="chat_id",
    )
    _send_text_message(config, message)
    return True


def is_configured() -> bool:
    """Return True if Feishu credentials are present."""
    try:
        _load_config()
    except FeishuConfigError:
        return False
    return True


def _render_llm_quota_alert_message(
    *,
    operation: str,
    model: str,
    status_code: int,
    response_text: str,
    occurred_at: str,
) -> str:
    lines = [
        "Edu News Pipeline - LLM quota/billing alert",
        f"Problem: LLM provider reported a quota, balance, billing, or payment issue.",
        f"Operation: {operation}",
        f"Model: {model}",
        f"HTTP status: {status_code}",
        f"Occurred at: {occurred_at}",
        "",
        f"Provider response: {_truncate(response_text, 600)}",
        "",
        "Action: check the LLM account balance, billing status, quota, and API key.",
    ]
    return _truncate("\n".join(lines), 1800)


def _render_llm_request_alert_message(
    *,
    operation: str,
    endpoint_label: Optional[str],
    model: Optional[str],
    status_code: Optional[int],
    provider_code: Optional[str],
    provider_message: Optional[str],
    request_id: Optional[str],
    occurred_at: str,
) -> str:
    status_text = "no HTTP response" if status_code is None else str(status_code)
    lines = [
        "Edu News Pipeline - LLM request rejected (systematic)",
        f"Operation: {operation}",
        f"Endpoint: {endpoint_label or '(unknown)'}",
        f"Model: {model or '(unknown)'}",
        f"HTTP status: {status_text}",
        f"Provider code: {provider_code or '-'}",
        f"Request id: {request_id or '-'}",
        f"Occurred at: {occurred_at}",
        "",
        f"Provider response: {_truncate(provider_message or '(empty)', 600)}",
        "",
        "Action: 检查调用代码、接入点能力（如 response_format/结构化输出支持）、"
        "模型名与请求参数；该错误不会重试，本轮步骤已熔断，修复后重跑即可。",
    ]
    return _truncate("\n".join(lines), 1800)


def _request_alert_fingerprint(
    *,
    status_code: Optional[int],
    provider_code: Optional[str],
    provider_message: Optional[str],
    endpoint_label: Optional[str],
    model: Optional[str],
) -> str:
    normalized = "\n".join(
        (
            f"status={'none' if status_code is None else status_code}",
            f"code={(provider_code or '').strip().lower()}",
            f"message={_normalize_alert_text(provider_message)}",
            f"endpoint={(endpoint_label or '').strip().lower()}",
            f"model={(model or '').strip().lower()}",
        )
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_alert_text(value: Optional[str]) -> str:
    """Collapse whitespace and drop request ids so identical errors share a fingerprint."""

    cleaned = _REQUEST_ID_TEXT_PATTERN.sub(" ", value or "")
    return " ".join(cleaned.split())


def _read_request_alert_state(state_path: Path) -> dict[str, float]:
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, Mapping):
        return {}
    raw_entries = data.get("fingerprints")
    if not isinstance(raw_entries, Mapping):
        return {}
    entries: dict[str, float] = {}
    for key, value in raw_entries.items():
        if isinstance(key, str) and isinstance(value, (int, float)):
            entries[key] = float(value)
    return entries


def _write_request_alert_state(
    state_path: Path,
    entries: Mapping[str, float],
) -> None:
    trimmed = sorted(entries.items(), key=lambda item: item[1], reverse=True)
    payload = dict(trimmed[:_REQUEST_ALERT_STATE_LIMIT])
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({"fingerprints": payload}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"[llm_alert] failed to write request alert state: {exc}")


def _render_console_registration_message(
    *,
    username: str,
    display_name: str,
    preferred_weekday: int,
    occurred_at: str,
) -> str:
    safe_username = " ".join(username.split())
    safe_display_name = " ".join(display_name.split())
    lines = [
        "Edu News Pipeline - 新用户注册",
        f"姓名：{_truncate(safe_display_name, 100)}",
        f"用户名：{_truncate(safe_username, 100)}",
        f"首选值班日：{_WEEKDAY_LABELS[preferred_weekday]}",
        f"注册时间：{occurred_at}",
        "",
        "账号尚未自动排班，请前往“用户与排班”页面确认安排。",
    ]
    return "\n".join(lines)


def _load_config() -> _FeishuConfig:
    settings = get_settings()
    app_id = settings.feishu_app_id
    app_secret = settings.feishu_app_secret
    receive_id = settings.feishu_receive_id
    if not (app_id and app_secret and receive_id):
        raise FeishuConfigError(
            "Feishu credentials missing. Set FEISHU_APP_ID, FEISHU_APP_SECRET, and FEISHU_RECEIVE_ID."
        )
    receive_id_type = settings.feishu_receive_id_type or "open_id"
    return _FeishuConfig(
        app_id=app_id,
        app_secret=app_secret,
        receive_id=receive_id,
        receive_id_type=receive_id_type,
    )


def _fetch_tenant_access_token(config: _FeishuConfig) -> _TokenCache:
    payload = {"app_id": config.app_id, "app_secret": config.app_secret}
    try:
        response = requests.post(_TOKEN_URL, json=payload, timeout=_DEFAULT_TIMEOUT)
    except requests.RequestException as exc:  # pragma: no cover - network failure
        raise FeishuRequestError(f"Failed to request tenant_access_token: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:  # pragma: no cover - defensive
        raise FeishuRequestError("Feishu token response was not valid JSON") from exc

    if data.get("code") != 0 or "tenant_access_token" not in data:
        raise FeishuRequestError(f"Failed to obtain tenant_access_token: {data}")

    expires_in = int(data.get("expire", data.get("expire_in", 7200)))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in - 60))
    return _TokenCache(token=data["tenant_access_token"], expires_at=expires_at)


def _get_token(config: _FeishuConfig) -> str:
    global _token_cache
    with _token_lock:
        if _token_cache is None or not _token_cache.is_valid():
            _token_cache = _fetch_tenant_access_token(config)
        return _token_cache.token


def _send_text_message(config: _FeishuConfig, message: str) -> None:
    token = _get_token(config)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    params = {"receive_id_type": config.receive_id_type}
    payload = {
        "receive_id": config.receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": message}, ensure_ascii=False),
    }
    try:
        response = requests.post(
            _MESSAGE_URL,
            headers=headers,
            params=params,
            json=payload,
            timeout=_DEFAULT_TIMEOUT,
        )
    except requests.RequestException as exc:  # pragma: no cover - network failure
        raise FeishuRequestError(f"Failed to send Feishu message: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:  # pragma: no cover
        raise FeishuRequestError("Feishu send message response was not valid JSON") from exc

    if data.get("code") != 0:
        msg = data.get("msg") or data
        raise FeishuRequestError(f"Feishu message API error: {msg}")


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


__all__ = [
    "FeishuConfigError",
    "FeishuRequestError",
    "is_configured",
    "notify_console_registration",
    "notify_llm_quota_alert",
    "notify_llm_request_alert",
    "send_text_to_chat",
]
