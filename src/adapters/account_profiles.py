from __future__ import annotations

import json
import time
from typing import Any, Optional, cast

import requests
from bs4 import BeautifulSoup

from src.adapters import (
    http_beijinghao,
    http_btime,
    http_tencent,
)


MAX_ACCOUNT_NAME_LENGTH = 200
MAX_ACCOUNT_TIMEOUT_SECONDS = 8.0


class AccountNameUnavailable(Exception):
    """Raised when an account profile does not expose a usable display name."""


def _name(value: Any) -> Optional[str]:
    cleaned = str(value or "").strip()
    return cleaned[:MAX_ACCOUNT_NAME_LENGTH] or None


def _request(
    session: requests.Session,
    url: str,
    *,
    timeout: float,
    params: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    page_label: str = "主页",
) -> requests.Response:
    budget = min(MAX_ACCOUNT_TIMEOUT_SECONDS, max(0.0, float(timeout)))
    if budget <= 0:
        raise AccountNameUnavailable(f"请求{page_label}超时")
    deadline = time.monotonic() + budget
    last_error = f"请求{page_label}失败"
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AccountNameUnavailable(f"请求{page_label}超时")
        try:
            response = session.get(
                url,
                params=params,
                headers=headers,
                timeout=remaining,
            )
        except requests.Timeout:
            last_error = f"请求{page_label}超时"
        except requests.RequestException as exc:
            detail = _name(exc) or "网络错误"
            last_error = f"请求{page_label}失败：{detail}"
        else:
            if response.status_code < 400:
                return response
            last_error = f"{page_label}返回 {response.status_code}"
            if response.status_code < 500:
                break
        if attempt == 0:
            continue
    raise AccountNameUnavailable(last_error)


def _resolve_tencent(
    normalized_identifier: str,
    *,
    timeout: float,
) -> str:
    transport = http_tencent._session()

    class CappedSession:
        def get(self, url: str, **kwargs: Any) -> requests.Response:
            try:
                return _request(
                    transport,
                    url,
                    params=kwargs.get("params"),
                    timeout=timeout,
                    page_label="官方账号接口",
                )
            except AccountNameUnavailable as exc:
                class FailedResponse:
                    def __init__(self, error: AccountNameUnavailable) -> None:
                        self.error = error

                    def raise_for_status(self) -> None:
                        return None

                    def json(self) -> dict[str, Any]:
                        raise self.error

                return cast(requests.Response, FailedResponse(exc))

    try:
        profile = http_tencent.fetch_author_profile(
            normalized_identifier,
            session=cast(requests.Session, CappedSession()),
        )
    except AccountNameUnavailable:
        raise
    except (json.JSONDecodeError, requests.JSONDecodeError, ValueError) as exc:
        raise AccountNameUnavailable("官方账号接口返回了无效数据") from exc
    except RuntimeError as exc:
        raise AccountNameUnavailable("官方账号接口返回失败") from exc
    name = _name(profile.get("name") or profile.get("nick"))
    if name is None:
        raise AccountNameUnavailable("官方账号接口里没有找到账号名")
    return name


def _resolve_btime(
    normalized_identifier: str,
    profile_url: str,
    *,
    timeout: float,
) -> str:
    session = http_btime._session()
    response = _request(
        session,
        http_btime.LIST_API_URL,
        params=http_btime._build_list_params(normalized_identifier),
        headers={"Referer": profile_url},
        timeout=timeout,
        page_label="账号列表接口",
    )
    try:
        payload = response.json()
    except (json.JSONDecodeError, requests.JSONDecodeError, ValueError) as exc:
        raise AccountNameUnavailable("账号列表接口返回了无效数据") from exc
    if payload.get("code") not in (None, 0):
        raise AccountNameUnavailable("账号列表接口返回失败")
    container = payload.get("data") or {}
    rows = container.get("data") if isinstance(container, dict) else None
    for row in rows if isinstance(rows, list) else []:
        data = row.get("data") if isinstance(row, dict) else None
        if not isinstance(data, dict):
            continue
        row_uid = _name(data.get("create_uid") or data.get("author_uid"))
        if row_uid and row_uid != normalized_identifier:
            continue
        name = _name(data.get("create_name") or data.get("source"))
        if name:
            return name
    raise AccountNameUnavailable("账号列表里没有找到账号名")


def _resolve_beijinghao(profile_url: str, *, timeout: float) -> str:
    session = http_beijinghao._session()
    response = _request(
        session,
        profile_url,
        headers={"Referer": profile_url},
        timeout=timeout,
    )
    soup = BeautifulSoup(http_beijinghao._response_text(response), "html.parser")
    name = _name(soup.title.get_text(" ", strip=True) if soup.title else "")
    if name is None:
        raise AccountNameUnavailable("页面里没有找到账号名")
    return name


def resolve_account_name(
    source: str,
    *,
    normalized_identifier: str,
    profile_url: str,
    timeout: float,
) -> str:
    """Resolve one account name without writing application data."""
    try:
        if source == "tencent":
            return _resolve_tencent(normalized_identifier, timeout=timeout)
        if source == "btime":
            return _resolve_btime(
                normalized_identifier,
                profile_url,
                timeout=timeout,
            )
        if source == "beijinghao":
            return _resolve_beijinghao(profile_url, timeout=timeout)
        if source == "toutiao":
            raise AccountNameUnavailable("头条主页当前无法直接解析账号名")
        raise AccountNameUnavailable("不支持的账号来源")
    except AccountNameUnavailable:
        raise
    except Exception as exc:
        raise AccountNameUnavailable("解析账号名失败") from exc


__all__ = ["AccountNameUnavailable", "resolve_account_name"]
