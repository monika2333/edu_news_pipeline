"""新京报「新京号」主页抓取 adapter。

新京号按「账号=号主页」组织：一条 crawl_account 对应
``https://m.bjnews.com.cn/mp/<xjh_id>``。号的文章列表来自移动端内部接口
``xjh/xjh_article.php``，每页 10 条、按发布时间从新到旧，且**列表项自带
完整正文**，因此不需要再逐篇请求详情页（与北京号走详情 JSON 不同）。

新京报的「栏目」（``https://m.bjnews.com.cn/column/<column_id>.html``，
分享链接为 ``api.bjnews.com.cn/.../news/column_qrcode.php?column_id=<id>``）
也作为新京号来源下的一种账号接入。栏目列表接口 ``news/column_news.php``
每页 20 条且**不带正文**，因此栏目稿件要在列表阶段逐篇请求详情页补齐正文；
详情页抓取失败的稿件直接从本轮列表中丢弃，不写 feed 行，下一轮自然重试。

落库规则与北京号一致：所有账号的稿件 ``source`` 固定为报名「新京报」，
不随账号名变化；``article_id`` 用列表项 uuid，形如 ``xinjinghao:<uuid>``。
号与栏目共用同一套详情页 URL，同一篇稿件出现在两处时 ID 相同、自然去重。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import requests

from bs4 import BeautifulSoup

from src.adapters.http_common import (
    build_session,
    decode_response,
    html_to_markdown as common_html_to_markdown,
)
from src.adapters.http_linked_page_rows import (
    build_detail_update as build_linked_detail_update,
)
from src.adapters.http_linked_page_rows import feed_item_to_row as linked_feed_item_to_row

LOGGER = logging.getLogger(__name__)

API_BASE_URL = "https://api.bjnews.com.cn/api/v101/"
LIST_API_URL = f"{API_BASE_URL}xjh/xjh_article.php"
ACCOUNT_INFO_API_URL = f"{API_BASE_URL}xjh/xjh_info.php"
COLUMN_LIST_API_URL = f"{API_BASE_URL}news/column_news.php"
COLUMN_SHARE_PATH = "/api/v101/news/column_qrcode.php"
COLUMN_BASE_URL = "https://m.bjnews.com.cn/column/"
COLUMN_IDENTIFIER_PREFIX = "column:"
COLUMN_PAGE_SIZE = 20
MP_BASE_URL = "https://m.bjnews.com.cn/mp/"
DETAIL_BASE_URL = "https://m.bjnews.com.cn/detail/"
DETAIL_HOSTS = frozenset({"m.bjnews.com.cn", "www.bjnews.com.cn"})
SOURCE_NAME = "新京报"
ARTICLE_ID_PREFIX = "xinjinghao:"
CHINA_TZ = timezone(timedelta(hours=8))
DEFAULT_TIMEOUT = 15
PAGE_SIZE = 10
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
_MP_PATH_RE = re.compile(r"^/mp/([A-Za-z0-9]+)$")
_COLUMN_PATH_RE = re.compile(r"^/column/(\d+)(?:\.html?)?$", re.IGNORECASE)
_COLUMN_ID_RE = re.compile(r"^\d+$")
_DETAIL_PATH_RE = re.compile(r"^/detail/([A-Za-z0-9_-]+)\.html?$", re.IGNORECASE)
_ARTICLE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# 号内稿件结尾的固定模板：供稿/来源/编辑/校对/审核等，连续出现在末尾时整体剥掉。
# 只剥结尾连续行，正文中间的「来源：」不参与匹配。
_FOOTER_LINE_RE = re.compile(
    r"^(?:供稿|来源|编辑|编校|校对|审核|责任编辑|责编|终审|作者)[：:]"
)


@dataclass(frozen=True)
class AccountEntry:
    """控制台「账号=号主页」模型里的一条新京号账号。

    号账号只填 ``xjh_id``；栏目账号的 ``xjh_id`` 为空、填 ``column_id``。
    两者在 ``crawl_accounts.normalized_identifier`` 里分别存为 ``<xjh_id>``
    与 ``column:<column_id>``，见 :func:`entry_from_identifier`。
    """

    xjh_id: str
    page_url: str
    raw_source: str = ""
    column_id: str = ""

    @property
    def is_column(self) -> bool:
        return bool(self.column_id)

    @property
    def normalized_identifier(self) -> str:
        if self.column_id:
            return f"{COLUMN_IDENTIFIER_PREFIX}{self.column_id}"
        return self.xjh_id


@dataclass
class FeedItemLike:
    title: str
    url: str
    section: Optional[str]
    publish_time_iso: Optional[str]
    raw: dict[str, Any]


def _session() -> requests.Session:
    return build_session(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://m.bjnews.com.cn/",
        }
    )


def _column_entry(column_id: str, raw: str) -> AccountEntry:
    return AccountEntry(
        xjh_id="",
        page_url=f"{COLUMN_BASE_URL}{column_id}.html",
        raw_source=raw,
        column_id=column_id,
    )


def _parse_url(candidate: str, raw: str) -> AccountEntry:
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if host == "api.bjnews.com.cn" and path == COLUMN_SHARE_PATH:
        # 栏目「分享」得到的二维码链接，302 到 m 站栏目页。
        column_ids = parse_qs(parsed.query).get("column_id") or [""]
        column_id = column_ids[0].strip()
        if not _COLUMN_ID_RE.fullmatch(column_id):
            raise ValueError(f"栏目分享链接里没有有效的 column_id：{raw}")
        return _column_entry(column_id, raw)
    if host != "m.bjnews.com.cn":
        raise ValueError(f"新京号主页地址必须是 m.bjnews.com.cn 域名：{raw}")
    match = _MP_PATH_RE.fullmatch(path)
    if match is not None:
        return AccountEntry(
            xjh_id=match.group(1),
            page_url=f"{MP_BASE_URL}{match.group(1)}",
            raw_source=raw,
        )
    column_match = _COLUMN_PATH_RE.fullmatch(path)
    if column_match is not None:
        return _column_entry(column_match.group(1), raw)
    if path.startswith("/detail/") or path.lower().endswith((".html", ".htm")):
        raise ValueError(
            f"请填新京号主页（/mp/<id>）或栏目页（/column/<id>.html），不要填文章页：{raw}"
        )
    raise ValueError(
        "请填新京号主页（https://m.bjnews.com.cn/mp/<id>）"
        f"或栏目页（https://m.bjnews.com.cn/column/<id>.html）：{raw}"
    )


def parse_account_input(raw: str) -> AccountEntry:
    """接受号主页或栏目页的各种写法。

    - 号：裸 ID、``/mp/<id>``、完整主页 URL（可带 query）。``/mp/296/`` 在
      站点上返回 404，因此规范化时固定去掉结尾斜杠。
    - 栏目：``m.bjnews.com.cn/column/<id>.html``、栏目分享链接
      ``api.bjnews.com.cn/api/v101/news/column_qrcode.php?column_id=<id>``、
      或 ``column:<id>``。主页统一规范为 ``/column/<id>.html``（不带
      ``.html`` 与带结尾斜杠的写法在站点上都是 404）。

    文章页与站外域名一律拒绝，避免把详情页误当成号主页。
    """

    cleaned = (raw or "").strip().lstrip("\ufeff")
    if not cleaned:
        raise ValueError("Empty Xinjinghao account id")
    candidate = cleaned
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    if re.match(r"^https?://", candidate, re.IGNORECASE):
        return _parse_url(candidate, cleaned)
    if "/" in candidate:
        return _parse_url(f"https://{candidate}", cleaned)
    if candidate.lower().startswith(COLUMN_IDENTIFIER_PREFIX):
        column_id = candidate[len(COLUMN_IDENTIFIER_PREFIX):].strip()
        if not _COLUMN_ID_RE.fullmatch(column_id):
            raise ValueError(f"Unable to derive Xinjinghao column id from: {cleaned}")
        return _column_entry(column_id, cleaned)
    if _ARTICLE_ID_RE.fullmatch(candidate):
        return AccountEntry(
            xjh_id=candidate,
            page_url=f"{MP_BASE_URL}{candidate}",
            raw_source=cleaned,
        )
    raise ValueError(f"Unable to derive Xinjinghao account id from: {cleaned}")


def entry_from_identifier(
    normalized_identifier: str,
    page_url: str,
    raw_source: str = "",
) -> AccountEntry:
    """把 ``crawl_accounts`` 里存的标识还原成抓取用的账号条目。"""

    identifier = (normalized_identifier or "").strip()
    if identifier.startswith(COLUMN_IDENTIFIER_PREFIX):
        return AccountEntry(
            xjh_id="",
            page_url=page_url,
            raw_source=raw_source,
            column_id=identifier[len(COLUMN_IDENTIFIER_PREFIX):],
        )
    return AccountEntry(xjh_id=identifier, page_url=page_url, raw_source=raw_source)


def normalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = f"https:{raw}"
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raw = urljoin(DETAIL_BASE_URL, raw)
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").lower()
    scheme = "https" if host in DETAIL_HOSTS else parsed.scheme.lower()
    return urlunsplit((scheme, parsed.netloc.lower(), parsed.path, "", ""))


def _is_detail_url(url: str) -> bool:
    parsed = urlsplit(normalize_url(url))
    return (
        (parsed.hostname or "").lower() in DETAIL_HOSTS
        and bool(_DETAIL_PATH_RE.fullmatch(parsed.path or ""))
    )


def make_article_id(value: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError("Empty Xinjinghao article id")
    if re.match(r"^https?://|^//", cleaned, re.IGNORECASE):
        path = urlsplit(normalize_url(cleaned)).path.rstrip("/")
        cleaned = path.rsplit("/", 1)[-1] if path else ""
    cleaned = re.sub(r"\.html?$", "", cleaned, flags=re.IGNORECASE)
    if not _ARTICLE_ID_RE.fullmatch(cleaned):
        raise ValueError(f"Unable to derive Xinjinghao article id from: {value}")
    return f"{ARTICLE_ID_PREFIX}{cleaned}"


def _publish_time_iso(row: dict[str, Any]) -> Optional[str]:
    timestamp = row.get("publish_timestamp")
    try:
        if timestamp not in (None, "", "0", 0):
            return datetime.fromtimestamp(int(str(timestamp)), CHINA_TZ).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    text = str(row.get("publish_time") or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=CHINA_TZ).isoformat()
        except ValueError:
            continue
    return None


def _parse_list_payload(
    payload: dict[str, Any],
    *,
    existing_ids: Optional[set[str]] = None,
) -> list[FeedItemLike]:
    code = payload.get("code")
    try:
        code_value = int(str(code))
    except (TypeError, ValueError):
        code_value = None
    if code_value != 0:
        raise RuntimeError(
            f"Xinjinghao list API error {code}: "
            f"{payload.get('msg') or payload.get('message') or 'unknown error'}"
        )
    rows = payload.get("data")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise RuntimeError("Xinjinghao list API returned invalid data")

    items: list[FeedItemLike] = []
    seen: set[str] = set(existing_ids or set())
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        row = entry.get("row") or {}
        if not isinstance(row, dict):
            continue
        uuid = str(row.get("uuid") or entry.get("uuid") or "").strip()
        title = str(row.get("title") or "").strip()
        share_url = str(entry.get("share_url") or row.get("share_url") or "").strip()
        url = normalize_url(share_url or (f"{DETAIL_BASE_URL}{uuid}.html" if uuid else ""))
        if not (title and url and _is_detail_url(url)):
            continue
        # 文章 ID 统一由 URL 派生：过滤、known_ids 与 prepare_feed 必须同源，
        # 不能一半用 row.uuid、一半用 share_url。
        article_id = make_article_id(url)
        if article_id in seen:
            continue
        seen.add(article_id)
        items.append(
            FeedItemLike(
                title=title,
                url=url,
                section=SOURCE_NAME,
                publish_time_iso=_publish_time_iso(row),
                raw={
                    "uuid": uuid,
                    "content": str(row.get("content") or ""),
                    "publish_text": str(row.get("publish_time") or ""),
                },
            )
        )
    return items


def list_items(
    entries: list[AccountEntry],
    limit: Optional[int] = None,
    pages: Optional[int] = None,
    *,
    existing_ids: Optional[set[str]] = None,
) -> list[FeedItemLike]:
    """按账号、按页抓取新京号列表。

    ``existing_ids`` 只用于过滤已入库条目，**不参与停止判断**；停止翻页只看
    原始 ``data`` 数组：空数组、不足一页（最后一页）或 ``pages`` 硬上限。
    这样 ``--pages 70`` 补历史时，即使第 1 页全是已入库条目，也会继续翻到
    page 2 以下的老稿。
    """

    if limit is not None and limit <= 0:
        return []
    page_count = max(1, pages or 1)
    session = _session()
    items: list[FeedItemLike] = []
    known_ids = set(existing_ids or set())
    for entry in entries:
        if entry.is_column:
            list_url = COLUMN_LIST_API_URL
            params_base = {"column_id": entry.column_id}
            page_size = COLUMN_PAGE_SIZE
        else:
            list_url = LIST_API_URL
            params_base = {"xjh_id": entry.xjh_id}
            page_size = PAGE_SIZE
        for page_number in range(1, page_count + 1):
            try:
                response = session.get(
                    list_url,
                    params={**params_base, "page": page_number},
                    headers={"Referer": entry.page_url},
                    timeout=DEFAULT_TIMEOUT,
                )
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                LOGGER.warning(
                    "Failed to fetch Xinjinghao listing %s page %s: %s",
                    entry.normalized_identifier,
                    page_number,
                    exc,
                )
                break
            rows = payload.get("data")
            raw_count = len(rows) if isinstance(rows, list) else 0
            batch = _parse_list_payload(payload, existing_ids=known_ids)
            for item in batch:
                if entry.is_column and not _fill_content_from_detail(session, item):
                    # 不写 feed 行：否则 existing_ids 会挡住下一轮的重试。
                    continue
                items.append(item)
                known_ids.add(make_article_id(item.url))
                if limit is not None and len(items) >= limit:
                    return items
            # 只按原始 data 判断是否到达最后一页；过滤后的 batch 可能为空
            # （整页都已入库），此时必须继续翻页，否则补历史会被第 1 页截停。
            if raw_count < page_size:
                break
    return items


def parse_detail_content(html: str) -> str:
    """从详情页 HTML 取正文容器 ``.article-cen`` 的内部 HTML。"""

    soup = BeautifulSoup(html or "", "html.parser")
    node = soup.select_one(".article-cen")
    if node is None:
        return ""
    return node.decode_contents()


def _fill_content_from_detail(session: requests.Session, item: FeedItemLike) -> bool:
    """栏目列表不带正文：请求详情页把正文写进 ``item.raw["content"]``。"""

    try:
        response = session.get(
            item.url,
            headers={"Referer": "https://m.bjnews.com.cn/"},
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        content = parse_detail_content(decode_response(response))
    except requests.RequestException as exc:
        LOGGER.warning("Failed to fetch Xinjinghao detail %s: %s", item.url, exc)
        return False
    if not html_to_markdown(content):
        LOGGER.warning("Empty Xinjinghao detail content for %s", item.url)
        return False
    item.raw["content"] = content
    return True


def html_to_markdown(html_str: Optional[str]) -> str:
    text = common_html_to_markdown(
        html_str,
        extra_unwanted="iframe, .share, .recommend, .related, .editor, .copyright",
    )
    lines = text.split("\n\n")
    while lines and (not lines[-1].strip() or _FOOTER_LINE_RE.match(lines[-1].strip())):
        lines.pop()
    return "\n\n".join(lines)


def feed_item_to_row(
    item: FeedItemLike,
    article_id: str,
    *,
    fetched_at: datetime,
) -> dict[str, Any]:
    source_item = FeedItemLike(
        title=item.title,
        url=item.url,
        section=SOURCE_NAME,
        publish_time_iso=item.publish_time_iso,
        raw=item.raw,
    )
    return linked_feed_item_to_row(source_item, article_id, fetched_at=fetched_at)


def article_to_detail_row(
    item: FeedItemLike,
    article_id: str,
    *,
    detail_fetched_at: datetime,
) -> dict[str, Any]:
    content_html = str(item.raw.get("content") or "")
    content_markdown = html_to_markdown(content_html)
    if not content_markdown:
        # 空正文不写 detail_fetched_at：否则正文永远不会再补抓。
        raise RuntimeError(f"Empty Xinjinghao article content for {item.url}")
    return build_linked_detail_update(
        item,
        article_id,
        {
            "title": item.title,
            "source": SOURCE_NAME,
            "publish_time_iso": item.publish_time_iso,
            "url": item.url,
            "content": content_html,
            "content_markdown": content_markdown,
        },
        detail_fetched_at=detail_fetched_at,
        default_source=SOURCE_NAME,
        render_content=html_to_markdown,
    )


__all__ = [
    "ACCOUNT_INFO_API_URL",
    "API_BASE_URL",
    "ARTICLE_ID_PREFIX",
    "AccountEntry",
    "CHINA_TZ",
    "COLUMN_BASE_URL",
    "COLUMN_IDENTIFIER_PREFIX",
    "COLUMN_LIST_API_URL",
    "COLUMN_PAGE_SIZE",
    "DEFAULT_TIMEOUT",
    "DETAIL_BASE_URL",
    "DETAIL_HOSTS",
    "FeedItemLike",
    "LIST_API_URL",
    "MP_BASE_URL",
    "PAGE_SIZE",
    "SOURCE_NAME",
    "article_to_detail_row",
    "entry_from_identifier",
    "feed_item_to_row",
    "html_to_markdown",
    "list_items",
    "make_article_id",
    "normalize_url",
    "parse_account_input",
    "parse_detail_content",
]
