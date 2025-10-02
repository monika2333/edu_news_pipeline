from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Optional, Sequence

import requests

from src.config import get_settings

_FEISHU_API_ROOT = "https://open.feishu.cn/open-apis"
_TOKEN_URL = f"{_FEISHU_API_ROOT}/auth/v3/tenant_access_token/internal/"
_MESSAGE_URL = f"{_FEISHU_API_ROOT}/im/v1/messages"
_DEFAULT_TIMEOUT = 10


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


def notify_export_summary(
    *,
    tag: str,
    output_path: Path,
    entries: Sequence[str],
    category_counts: Mapping[str, int],
    preview_limit: int = 3,
) -> bool:
    """Send a Feishu notification for a successful export."""
    config = _load_config()
    if not entries:
        raise ValueError("entries must not be empty")

    message = _render_message(
        tag=tag,
        output_path=output_path,
        entries=entries,
        category_counts=category_counts,
        preview_limit=preview_limit,
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


def _render_message(
    *,
    tag: str,
    output_path: Path,
    entries: Sequence[str],
    category_counts: Mapping[str, int],
    preview_limit: int,
) -> str:
    counts_line = _format_counts(category_counts)
    preview_lines = _build_preview(entries, limit=preview_limit)

    lines: list[str] = [f"Edu News Brief - {tag}"]
    if counts_line:
        lines.append(counts_line)
    if preview_lines:
        lines.append("")
        lines.extend(preview_lines)
    lines.append("")
    lines.append(f"Full file: {output_path}")

    return _truncate("\n".join(lines), 1800)


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




def _build_preview(entries: Sequence[str], *, limit: int) -> list[str]:
    lines: list[str] = []
    for entry in entries[:limit]:
        normalized = entry.replace("\n", " ")
        normalized = " ".join(normalized.split())
        lines.append(f"- {_truncate(normalized, 200)}")
    if len(entries) > limit:
        lines.append(f"... remaining {len(entries) - limit} entries in file")
    return lines





def _format_counts(counts: Mapping[str, int]) -> str:
    filtered = [f"{name}:{total}" for name, total in counts.items() if total]
    return "Category counts: " + ", ".join(filtered) if filtered else ""


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
    "notify_export_summary",
]
