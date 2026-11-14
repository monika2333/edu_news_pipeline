"""北京晚报数字报（``bjrbdzb.bjd.com.cn/bjwb``）。

北京晚报与北京日报同属北京日报报业集团的数字报平台，mobile 版式、刊期页
（``div.nav-items``）与正文页（``#main-title`` / ``#sub-title`` / ``#guide`` /
``#date`` / ``#content``）结构完全一致，因此解析器与行数据整理直接复用
``http_bjrb``（只传晚报自己的 ``source_key`` / ``source_name``），本模块只补
晚报自己的三件事：

1. 站点前缀与来源名（``bjwb`` / 北京晚报）；
2. 刊期定位：跟随 ``paperindex.htm`` 的 JS 跳转抓**站点当前最新一期**，而不是
   「今天这一期」。晚报当天的版通常下午才上传，00:00/08:00/12:00 三轮按当天
   日期必然抓空；跟随刊期索引还能兜住 23:30 之后才上传的尾单，非出版日只是
   重复检查同一期，靠 ``article_id`` 幂等去重（``BJWB_DATE`` 只用于历史补抓）；
3. 纯图片版（广告、电视节目预告、答案页等没有文字正文的条目）不算抓取失败：
   这些页面 ``#content`` 只有一个懒加载图片，按「流水线纯文字、图片剥离」的
   口径正文留空写入，关键词闸门不会命中，也就不会进入后续流水线。

``http_bjrb`` 里被复用的私有 helper（``_create_session`` / ``_fetch_text``）是
同平台共用的取文与解码实现，不要再复制一份，否则两个报纸的行为会漂移。
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

from src.adapters.http_bjrb import (
    DEFAULT_DELAY,
    DEFAULT_TIMEOUT,
    BjrbArticle as BjwbArticle,
    BjrbIssueItem as BjwbIssueItem,
    _create_session,
    _fetch_text,
    article_to_detail_row as _bjrb_article_to_detail_row,
    article_to_feed_row as _bjrb_article_to_feed_row,
    fetch_article as _bjrb_fetch_article,
    parse_article,
    parse_issue_index,
)

BASE_URL = "https://bjrbdzb.bjd.com.cn/bjwb"
SOURCE_NAME = "北京晚报"
SOURCE_KEY = "bjwb"
LOCATION_HREF_PATTERN = re.compile(
    r"""location\.href\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
ISSUE_DATE_PATTERN = re.compile(r"/(\d{4})/(\d{8})/\2_m\.html$")


class BjwbIssueNotPublished(RuntimeError):
    """北京晚报刊期尚未上线（刊期索引或指定日期还没有电子版）。

    与抓取故障区分：调用方应记一行「北京晚报 YYYYMMDD 期尚未上线」并跳过本轮，
    不计入失败来源；网络错误、5xx 与页面结构解析失败仍按真实故障抛出。
    """


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _resolve_base_url(base_url: Optional[str]) -> str:
    return (base_url or os.getenv("BJWB_BASE_URL") or BASE_URL).rstrip("/")


def _resolve_timeout(timeout: Optional[float]) -> float:
    return (
        float(timeout)
        if timeout is not None
        else _env_float("BJWB_TIMEOUT", DEFAULT_TIMEOUT)
    )


def resolve_issue_date(value: Optional[str] = None) -> Optional[str]:
    """``BJWB_DATE`` / 参数给定某期时抓该期；都为空时返回 None（跟随刊期索引抓最新一期）。"""

    raw = (value or os.getenv("BJWB_DATE") or "").strip()
    if not raw:
        return None
    if not re.fullmatch(r"\d{8}", raw):
        raise ValueError("BJWB_DATE must use YYYYMMDD format")
    datetime.strptime(raw, "%Y%m%d")
    return raw


def _paperindex_url(*, base_url: Optional[str] = None) -> str:
    return f"{_resolve_base_url(base_url)}/paperindex.htm"


def _issue_index_url(issue_date: str, *, base_url: Optional[str] = None) -> str:
    root = _resolve_base_url(base_url)
    return f"{root}/mobile/{issue_date[:4]}/{issue_date}/{issue_date}_m.html"


def parse_paperindex_target(html_text: str) -> str:
    """``paperindex.htm`` 是纯跳转页，取出它 ``location.href`` 指向的最新一期地址。"""

    match = LOCATION_HREF_PATTERN.search(html_text or "")
    if not match:
        raise RuntimeError("北京晚报刊期索引里没有 location.href 跳转目标")
    return match.group(1)


def issue_url_from_paperindex(html_text: str, index_url: str) -> str:
    """解析跳转目标为绝对地址，并去掉 ``?v=<时间戳>`` 形式的缓存参数。"""

    target = parse_paperindex_target(html_text)
    parts = urlsplit(urljoin(index_url, target))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def issue_date_from_url(issue_url: str) -> str:
    match = ISSUE_DATE_PATTERN.search(issue_url or "")
    if not match:
        raise RuntimeError(f"无法从北京晚报刊期地址解析出日期：{issue_url}")
    return match.group(2)


def list_issue_items(
    *,
    issue_date: Optional[str] = None,
    limit: Optional[int] = None,
    base_url: Optional[str] = None,
    timeout: Optional[float] = None,
    session: Optional[requests.Session] = None,
) -> list[BjwbIssueItem]:
    timeout_s = _resolve_timeout(timeout)
    sess = session or _create_session()
    created_session = session is None
    try:
        date_value = resolve_issue_date(issue_date)
        if date_value is None:
            index_url = _paperindex_url(base_url=base_url)
            index_html = _fetch_text(sess, index_url, timeout=timeout_s)
            issue_url = issue_url_from_paperindex(index_html, index_url)
            date_value = issue_date_from_url(issue_url)
        else:
            issue_url = _issue_index_url(date_value, base_url=base_url)
        try:
            html_text = _fetch_text(sess, issue_url, timeout=timeout_s)
        except requests.HTTPError as exc:
            # 刊期索引已指向该期但版面页 404：上传还没完成，按尚未上线处理
            if exc.response is not None and exc.response.status_code == 404:
                raise BjwbIssueNotPublished(
                    f"北京晚报 {date_value} 期尚未上线"
                ) from exc
            raise
        items = parse_issue_index(
            html_text,
            issue_url,
            date_value,
            source_key=SOURCE_KEY,
        )
        if not items:
            # 刊期页拿得到却解析不出任何稿件 = 站点结构变了，属真实故障
            raise RuntimeError(f"北京晚报 {date_value} 期页面未解析出稿件：{issue_url}")
        if limit is not None and limit > 0:
            return items[:limit]
        return items
    finally:
        if created_session:
            sess.close()


def fetch_article(
    item: BjwbIssueItem,
    *,
    timeout: Optional[float] = None,
    session: Optional[requests.Session] = None,
) -> BjwbArticle:
    """抓取正文；纯图片版没有文字正文时返回正文为空的文章（不算失败）。

    晚报每期都有少量整版图片条目（广告、电视节目预告、答案页），它们的
    ``#content`` 只有一个懒加载 ``<img data-src=...>``；流水线是纯文字的，
    这些条目正文留空即可，不能当抓取失败反复重试。
    """

    timeout_s = _resolve_timeout(timeout)
    return _bjrb_fetch_article(
        item,
        timeout=timeout_s,
        session=session,
        allow_empty_content=True,
    )


def article_to_feed_row(
    item: BjwbIssueItem,
    *,
    fetched_at: datetime,
) -> dict[str, Any]:
    return _bjrb_article_to_feed_row(
        item,
        fetched_at=fetched_at,
        source_name=SOURCE_NAME,
    )


def article_to_detail_row(
    article: BjwbArticle,
    *,
    detail_fetched_at: datetime,
) -> dict[str, Any]:
    return _bjrb_article_to_detail_row(
        article,
        detail_fetched_at=detail_fetched_at,
        source_name=SOURCE_NAME,
    )


__all__ = [
    "BASE_URL",
    "BjwbArticle",
    "BjwbIssueItem",
    "BjwbIssueNotPublished",
    "DEFAULT_DELAY",
    "DEFAULT_TIMEOUT",
    "SOURCE_KEY",
    "SOURCE_NAME",
    "article_to_detail_row",
    "article_to_feed_row",
    "fetch_article",
    "issue_date_from_url",
    "issue_url_from_paperindex",
    "list_issue_items",
    "parse_article",
    "parse_issue_index",
    "parse_paperindex_target",
    "resolve_issue_date",
]
