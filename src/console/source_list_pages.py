"""数据源页「列表页」链接的唯一映射：来源 key → adapter 里现有的列表页常量。

映射只做「取地址 + 加栏目名」，不复制任何 URL：单页来源的地址、栏目来源的
栏目与顺序全部来自各爬虫 adapter，爬虫增删栏目时设置页自动跟着变。
``list_pages_for_source`` 每次调用都重新读取 adapter 的取值规则——光明网、
中国教育报的地址可被环境变量覆盖，不能在模块加载时缓存。
"""

from __future__ import annotations

from typing import TypedDict

from src.adapters import (
    http_bbtnews,
    http_chinaeducationdaily,
    http_chinanews,
    http_chinanews_xj,
    http_gmw,
    http_stdaily,
    http_xinhua,
)


class ListPage(TypedDict):
    label: str
    url: str


# adapter 里没有栏目名的单页来源统一用这个标签
SINGLE_PAGE_LABEL = "列表页"


def _single(url: str) -> list[ListPage]:
    return [{"label": SINGLE_PAGE_LABEL, "url": url}]


def _columns(columns: tuple[tuple[str, str], ...]) -> list[ListPage]:
    return [{"label": label, "url": url} for label, url in columns]


def list_pages_for_source(source: str) -> list[ListPage]:
    """返回某来源当前对应的原始列表页，顺序与 adapter 常量一致。

    需要账号的来源、电子报来源，以及尚未接列表页的来源都返回空数组。
    """

    if source == "chinanews":
        return _single(http_chinanews.scroll_news_url(1))
    if source == "chinanews_xj":
        return _single(http_chinanews_xj.LIST_URL)
    if source == "jyb":
        return _single(http_chinaeducationdaily.resolve_listing_url())
    if source == "gmw":
        return _single(http_gmw.resolve_listing_url())
    if source == "xinhua":
        return _columns(http_xinhua.COLUMNS)
    if source == "xinhua_edu":
        return _columns(http_xinhua.EDUCATION_CHANNELS)
    if source == "stdaily":
        return _columns(http_stdaily.COLUMNS)
    if source == "bbtnews":
        return _columns(http_bbtnews.COLUMNS)
    return []


__all__ = [
    "ListPage",
    "SINGLE_PAGE_LABEL",
    "list_pages_for_source",
]
