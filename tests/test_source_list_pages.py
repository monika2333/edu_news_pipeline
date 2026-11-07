"""数据源页「列表页」链接：接口契约、adapter 常量与爬虫请求地址的一致性。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.adapters import (
    http_bbtnews,
    http_chinaeducationdaily,
    http_chinanews,
    http_stdaily,
    http_xinhua,
)
from src.business_config import SOURCE_CATALOG
from src.console import settings_service, source_list_pages


def _settings_payload(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    app_config = SimpleNamespace(fetch_settings=lambda: [])
    monkeypatch.setattr(
        settings_service,
        "get_adapter",
        lambda: SimpleNamespace(app_config=app_config),
    )
    return settings_service.get_settings_payload()


def _list_pages(payload: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    return {item["key"]: item["list_pages"] for item in payload["sources"]}


def _expected(columns: tuple[tuple[str, str], ...]) -> list[dict[str, str]]:
    return [{"label": label, "url": url} for label, url in columns]


def test_b1_every_hourly_account_free_source_has_list_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B1：无账号的每小时来源必须接上列表页，其余来源必须是空数组。"""

    pages = _list_pages(_settings_payload(monkeypatch))

    for source in SOURCE_CATALOG:
        if source.daily_only or source.requires_accounts:
            assert pages[source.key] == [], source.key
        else:
            assert pages[source.key], (
                f"{source.key} 是无账号的每小时来源，list_pages 不能为空；"
                "新增这类来源时记得在 source_list_pages 里接上列表页"
            )


def test_b2_list_pages_match_adapter_constants_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B2：栏目顺序与 label/url 逐项取自 adapter 常量，不手抄。"""

    pages = _list_pages(_settings_payload(monkeypatch))

    assert pages["xinhua"] == _expected(http_xinhua.COLUMNS)
    assert pages["xinhua_edu"] == _expected(http_xinhua.EDUCATION_CHANNELS)
    assert pages["stdaily"] == _expected(http_stdaily.COLUMNS)
    assert pages["bbtnews"] == _expected(http_bbtnews.COLUMNS)
    # 两条字面量锚定：地址在代码里被改动时必须被看见
    assert pages["jyb"] == [
        {
            "label": "列表页",
            "url": "http://www.jyb.cn/search.html?topsearch=",
        }
    ]
    assert pages["chinanews"] == [
        {
            "label": "列表页",
            "url": "https://www.chinanews.com.cn/scroll-news/news1.html",
        }
    ]


def test_b3_env_overrides_flow_into_list_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B3：光明网/中国教育报与爬虫共用同一条环境变量取值规则。"""

    monkeypatch.setenv("GMW_BASE_URL", " https://gmw.test/list ")
    assert _list_pages(_settings_payload(monkeypatch))["gmw"] == [
        {"label": "列表页", "url": "https://gmw.test/list"}
    ]

    monkeypatch.setenv("JYB_START_URL", "http://jyb.test/search.html?site=all")
    assert _list_pages(_settings_payload(monkeypatch))["jyb"] == [
        {
            "label": "列表页",
            "url": "http://jyb.test/search.html?site=all&topsearch=",
        }
    ]

    monkeypatch.setenv("JYB_START_URL", "http://jyb.test/search.html")
    assert _list_pages(_settings_payload(monkeypatch))["jyb"] == [
        {"label": "列表页", "url": "http://jyb.test/search.html?topsearch="}
    ]


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self) -> None:
        self.requested_urls: list[str] = []

    def get(self, url: str, *, timeout: float) -> _FakeResponse:
        self.requested_urls.append(url)
        return _FakeResponse()


def test_b4_chinanews_requests_unchanged_urls_after_extracting_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B4：常量提取是纯挪位置，爬虫实际请求的 URL 必须逐字不变。"""

    monkeypatch.setattr(http_chinanews, "decode_response", lambda _resp: "")
    session = _FakeSession()

    http_chinanews._fetch_page_html(1, session)
    http_chinanews._fetch_page_html(3, session)

    assert session.requested_urls == [
        "https://www.chinanews.com.cn/scroll-news/news1.html",
        "https://www.chinanews.com.cn/scroll-news/news3.html",
    ]


def test_mapping_is_copied_per_call_and_keeps_columns_ordered() -> None:
    """返回的列表是每次新建的，调用方改动不会污染下一次 payload。"""

    first = source_list_pages.list_pages_for_source("xinhua")
    first.pop()
    first[0]["label"] = "被改坏"

    second = source_list_pages.list_pages_for_source("xinhua")
    assert len(second) == len(http_xinhua.COLUMNS)
    assert second[0]["label"] == http_xinhua.COLUMNS[0][0]


def test_jyb_listing_url_matches_crawler_start_url_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """中国教育报：设置页链接 = 爬虫起点 + 空 topsearch 参数。"""

    monkeypatch.delenv("JYB_START_URL", raising=False)
    assert http_chinaeducationdaily.resolve_start_url() == (
        http_chinaeducationdaily.SEARCH_URL
    )
    assert http_chinaeducationdaily.resolve_listing_url() == (
        f"{http_chinaeducationdaily.SEARCH_URL}?topsearch="
    )
