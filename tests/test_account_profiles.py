from __future__ import annotations

import time
from typing import Any, Optional

import pytest
import requests

from src.adapters import (
    account_profiles,
    http_beijinghao,
    http_btime,
    http_tencent,
    http_toutiao,
)


class _Response:
    def __init__(
        self,
        *,
        status_code: int = 200,
        text: str = "",
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"
        self._payload = payload

    def json(self) -> dict[str, Any]:
        if self._payload is None:
            raise requests.JSONDecodeError("invalid", "", 0)
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []
        self.headers: dict[str, str] = {}

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return self.response


class _FailingSession(_Session):
    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        raise requests.Timeout("slow profile")


def test_tencent_name_uses_official_profile_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(payload={"ret": 0, "userinfo": {"nick": " 中国网 "}}))
    monkeypatch.setattr(http_tencent, "_session", lambda: session)

    name = account_profiles.resolve_account_name(
        "tencent",
        normalized_identifier="author-1",
        profile_url="https://news.qq.com/omn/author/author-1",
        timeout=8,
    )

    assert name == "中国网"
    assert session.calls[0]["url"] == http_tencent.AUTHOR_INFO_API
    assert session.calls[0]["params"]["guestSuid"] == "author-1"


def test_tencent_name_tightens_timeout_and_sends_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FailingSession(_Response())
    monkeypatch.setattr(http_tencent, "_session", lambda: session)
    monkeypatch.setattr(http_tencent.time, "sleep", lambda _delay: None)

    with pytest.raises(account_profiles.AccountNameUnavailable):
        account_profiles.resolve_account_name(
            "tencent",
            normalized_identifier="author-1",
            profile_url="https://news.qq.com/omn/author/author-1",
            timeout=60,
        )

    assert len(session.calls) == 1
    assert session.calls[0]["timeout"] == 8


def test_fetch_author_profile_defaults_to_ten_seconds_and_three_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FailingSession(_Response())
    monkeypatch.setattr(http_tencent.time, "sleep", lambda _delay: None)

    with pytest.raises(requests.Timeout):
        http_tencent.fetch_author_profile("author-1", session=session)

    assert len(session.calls) == 3
    assert [call["timeout"] for call in session.calls] == [10, 10, 10]


def test_btime_name_uses_create_name_from_list_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(
        _Response(
            payload={
                "code": 0,
                "data": {
                    "data": [
                        {
                            "data": {
                                "create_uid": "2874221",
                                "create_name": " BRTV新闻 社会新闻 ",
                            }
                        }
                    ]
                },
            }
        )
    )
    monkeypatch.setattr(http_btime, "_session", lambda: session)

    name = account_profiles.resolve_account_name(
        "btime",
        normalized_identifier="2874221",
        profile_url="https://record.btime.com/show?uid=2874221",
        timeout=8,
    )

    assert name == "BRTV新闻 社会新闻"
    assert session.calls[0]["url"] == http_btime.LIST_API_URL
    assert session.calls[0]["headers"] == {
        "Referer": "https://record.btime.com/show?uid=2874221"
    }


def test_beijinghao_name_uses_column_page_title_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(text="<html><title> 现代教育报 </title></html>"))
    monkeypatch.setattr(http_beijinghao, "_session", lambda: session)

    name = account_profiles.resolve_account_name(
        "beijinghao",
        normalized_identifier="6142fe79e4b0a8b3e76510d9",
        profile_url=(
            "https://peking.bjd.com.cn/bjhrootcolumn/system/"
            "6142fe79e4b0a8b3e76510d9"
        ),
        timeout=8,
    )

    assert name == "现代教育报"


def test_toutiao_resolves_via_browser_feed_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    def fake_fetch(
        token: str,
        profile_url: str,
        *,
        timeout_seconds: float,
    ) -> Optional[str]:
        observed["token"] = token
        observed["profile_url"] = profile_url
        observed["timeout_seconds"] = timeout_seconds
        return " 京报网 "

    monkeypatch.setattr(http_toutiao, "fetch_profile_display_name", fake_fetch)

    name = account_profiles.resolve_account_name(
        "toutiao",
        normalized_identifier="token-1",
        profile_url="https://www.toutiao.com/c/user/token/token-1/",
        timeout=30,
    )

    assert name == "京报网"
    assert observed["token"] == "token-1"
    assert observed["profile_url"] == "https://www.toutiao.com/c/user/token/token-1/"
    # 预算钳制在头条网页解析的上限，不透传更大的值
    assert observed["timeout_seconds"] == http_toutiao.PROFILE_NAME_TIMEOUT_SECONDS


def test_toutiao_maps_empty_feed_and_timeout_to_readable_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_name(*_args: Any, **_kwargs: Any) -> Optional[str]:
        return None

    def timeout(*_args: Any, **_kwargs: Any) -> Optional[str]:
        raise TimeoutError("budget exhausted")

    monkeypatch.setattr(http_toutiao, "fetch_profile_display_name", no_name)
    with pytest.raises(
        account_profiles.AccountNameUnavailable,
        match="没有可用的账号名",
    ):
        account_profiles.resolve_account_name(
            "toutiao",
            normalized_identifier="token-1",
            profile_url="https://www.toutiao.com/c/user/token/token-1/",
            timeout=8,
        )

    monkeypatch.setattr(http_toutiao, "fetch_profile_display_name", timeout)
    with pytest.raises(
        account_profiles.AccountNameUnavailable,
        match="头条主页请求超时",
    ):
        account_profiles.resolve_account_name(
            "toutiao",
            normalized_identifier="token-1",
            profile_url="https://www.toutiao.com/c/user/token/token-1/",
            timeout=8,
        )


def test_toutiao_profile_name_extraction_prefers_source_over_media_name() -> None:
    assert http_toutiao._profile_display_name_from_feed(
        {"data": [{"source": " 人民网 ", "media_name": "忽略我"}]}
    ) == "人民网"
    assert http_toutiao._profile_display_name_from_feed(
        {"data": [{"title": "无来源行"}, {"media_name": " 备用名 "}]}
    ) == "备用名"
    assert http_toutiao._profile_display_name_from_feed({"data": []}) is None
    assert http_toutiao._profile_display_name_from_feed({}) is None


def test_m6_single_account_timeout_is_capped_at_eight_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_timeouts: list[float] = []

    class SlowWhenMisconfiguredSession(_Session):
        def get(self, url: str, **kwargs: Any) -> _Response:
            timeout = float(kwargs["timeout"])
            observed_timeouts.append(timeout)
            if timeout > 8.1:
                time.sleep(0.2)
            raise requests.Timeout("slow profile")

    session = SlowWhenMisconfiguredSession(_Response())
    monkeypatch.setattr(http_beijinghao, "_session", lambda: session)
    started = time.monotonic()

    with pytest.raises(account_profiles.AccountNameUnavailable, match="超时"):
        account_profiles.resolve_account_name(
            "beijinghao",
            normalized_identifier="column-1",
            profile_url="https://peking.bjd.com.cn/bjhrootcolumn/system/column-1",
            timeout=60,
        )

    assert time.monotonic() - started < 0.15
    assert len(observed_timeouts) == 2
    assert max(observed_timeouts) <= 8.0


def test_account_name_is_stripped_and_truncated_to_column_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(
        _Response(payload={"ret": 0, "userinfo": {"name": f" {'名' * 250} "}})
    )
    monkeypatch.setattr(http_tencent, "_session", lambda: session)

    name = account_profiles.resolve_account_name(
        "tencent",
        normalized_identifier="author-1",
        profile_url="https://news.qq.com/omn/author/author-1",
        timeout=8,
    )

    assert name == "名" * 200
