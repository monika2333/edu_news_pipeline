from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from src.adapters.http_common import build_session, decode_response, html_to_markdown as common_html_to_markdown
from src.adapters.http_linked_page_rows import build_detail_update as build_linked_detail_update
from src.adapters.http_linked_page_rows import feed_item_to_row as linked_feed_item_to_row

COLUMN_BASE_URL = "https://peking.bjd.com.cn/bjhrootcolumn/system/"
DETAIL_BASE_URL = "https://peking.bjd.com.cn/content/"
DETAIL_HOST = "peking.bjd.com.cn"
# 北京日报客户端栏目分类页的稿件放在 xinwen 域，正文 JSON 与北京号同构。
CLIENT_DETAIL_HOST = "xinwen.bjd.com.cn"
DETAIL_HOSTS = frozenset({DETAIL_HOST, CLIENT_DETAIL_HOST})
SOURCE_NAME = "北京日报"
ARTICLE_ID_PREFIX = "beijinghao:"
CHINA_TZ = timezone(timedelta(hours=8))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
# 客户端栏目分类页（static.bjd.com.cn/static/category/）专用：
# 列表没有 bjhrootcolumn 栏目编号，只能按 categoryId + columnId 走检索接口取，
# 分页是检索接口的偏移页（page=0,1,2…），不是 stories.json 的版式页。
CATEGORY_HOST = "static.bjd.com.cn"
CATEGORY_PATH = "/static/category/"
CATEGORY_SEARCH_URL = "https://jiansuo.bjd.com.cn/cass/search"
CATEGORY_META_URL_TEMPLATE = (
    "https://static.bjd.com.cn/mdms-res/category/json/{channel_id}/category.json"
)
DEFAULT_CHANNEL_ID = "5b165687a010550e5ddc0e6a"
# 视频/直播稿没有可用的文字正文，对应页面「文章」页签的口径。
NON_ARTICLE_STORY_TYPES = frozenset({"VIDEO", "LIVE"})
_DETAIL_PATH_RE = re.compile(r"^/content/([^/?#]+)\.html$", re.IGNORECASE)


@dataclass(frozen=True)
class ColumnEntry:
    """一个待抓取的栏目。

    传统北京号栏目 ``column_code`` 是 bjhrootcolumn 编号；客户端栏目分类页
    没有这种编号，``column_code`` 存规整后的栏目页地址（与 21jingji、
    chinadaily 等「账号 = 栏目 URL」来源一致），列表抓取按 ``page_url`` 判型。
    """

    column_code: str
    page_url: str
    raw_source: str


@dataclass(frozen=True)
class CategoryEntry:
    """北京日报客户端「栏目分类」页的坐标与规整后的页面地址。"""

    column_id: str
    category_id: str
    channel_id: str
    page_url: str

    def search_url(self, page_number: int) -> str:
        query = urlencode(
            {
                "channelId": self.channel_id,
                "categoryId": self.category_id,
                "page": page_number,
            }
        )
        return f"{CATEGORY_SEARCH_URL}?{query}"


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
            "Accept-Language": "zh-CN,zh;q=0.9",
            "User-Agent": USER_AGENT,
        }
    )


def column_page_url(column_code: str) -> str:
    return f"{COLUMN_BASE_URL}{column_code}"


def parse_column_code(raw: str) -> str:
    value = (raw or "").strip().lstrip("\ufeff").rstrip("/")
    if not value:
        raise ValueError("Empty Beijinghao column code")
    if re.match(r"^https?://", value, re.IGNORECASE):
        segments = [segment for segment in urlsplit(value).path.split("/") if segment]
        try:
            system_index = segments.index("system")
            value = segments[system_index + 1]
        except (ValueError, IndexError) as exc:
            raise ValueError(f"Unable to derive Beijinghao column code from: {raw}") from exc
    if not re.fullmatch(r"[A-Za-z0-9]+", value):
        raise ValueError(f"Unable to derive Beijinghao column code from: {raw}")
    return value


def _query_value(query: dict[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return str(values[0] if values else "").strip()


def parse_category_input(raw: str) -> Optional[CategoryEntry]:
    """识别客户端栏目分类页地址；不是这一类地址时返回 ``None``。

    地址形如 ``https://static.bjd.com.cn/static/category/?categoryId=…&channelId=…&columnId=…``
    （``channelId`` 可省略，按客户端生产频道补全）。同域名但参数缺失/非法时
    报错而不是当成北京号栏目编号处理，否则错误信息会指向无关的编号解析。
    """

    cleaned = (raw or "").strip().lstrip("\ufeff")
    if not cleaned:
        return None
    candidate = cleaned
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    if not re.match(r"^https?://", candidate, re.IGNORECASE):
        if not candidate.lower().startswith(f"{CATEGORY_HOST}/"):
            return None
        candidate = f"https://{candidate}"
    parsed = urlsplit(candidate)
    if (parsed.hostname or "").lower() != CATEGORY_HOST:
        return None
    if parsed.path.rstrip("/") != CATEGORY_PATH.rstrip("/"):
        raise ValueError(
            f"北京日报客户端栏目页地址必须是 {CATEGORY_HOST}{CATEGORY_PATH} 形式：{cleaned}"
        )
    query = parse_qs(parsed.query)
    column_id = _query_value(query, "columnId")
    category_id = _query_value(query, "categoryId")
    channel_id = _query_value(query, "channelId") or DEFAULT_CHANNEL_ID
    missing = [
        name
        for name, value in (("columnId", column_id), ("categoryId", category_id))
        if not value
    ]
    if missing:
        raise ValueError(f"栏目页地址缺少 {'、'.join(missing)} 参数：{cleaned}")
    for name, value in (
        ("columnId", column_id),
        ("categoryId", category_id),
        ("channelId", channel_id),
    ):
        if not re.fullmatch(r"[A-Za-z0-9]+", value):
            raise ValueError(f"栏目页地址的 {name} 参数不合法：{cleaned}")
    # 参数顺序统一，同一栏目无论从哪种写法粘进来都落到同一个账号标识。
    canonical = urlunsplit(
        (
            "https",
            CATEGORY_HOST,
            CATEGORY_PATH,
            urlencode(
                {
                    "categoryId": category_id,
                    "channelId": channel_id,
                    "columnId": column_id,
                }
            ),
            "",
        )
    )
    return CategoryEntry(
        column_id=column_id,
        category_id=category_id,
        channel_id=channel_id,
        page_url=canonical,
    )


def category_meta_url(entry: CategoryEntry) -> str:
    return CATEGORY_META_URL_TEMPLATE.format(channel_id=entry.channel_id)


def parse_category_meta_name(payload: Any, entry: CategoryEntry) -> Optional[str]:
    """从栏目分类清单（page 自己渲染标题用的那份 JSON）里取出分类名。"""

    if not isinstance(payload, dict):
        return None
    columns = payload.get("data")
    for column in columns if isinstance(columns, list) else []:
        if not isinstance(column, dict) or str(column.get("uuid") or "") != entry.column_id:
            continue
        categories = column.get("categories")
        for category in categories if isinstance(categories, list) else []:
            if not isinstance(category, dict):
                continue
            if str(category.get("uuid") or "") != entry.category_id:
                continue
            return str(category.get("name") or "").strip() or None
    return None


def parse_column_input(raw: str) -> ColumnEntry:
    cleaned = (raw or "").strip().lstrip("\ufeff")
    if not cleaned:
        raise ValueError("Empty Beijinghao column code")
    category_entry = parse_category_input(cleaned)
    if category_entry is not None:
        return ColumnEntry(
            column_code=category_entry.page_url,
            page_url=category_entry.page_url,
            raw_source=cleaned,
        )
    column_code = parse_column_code(cleaned)
    return ColumnEntry(
        column_code=column_code,
        page_url=(
            cleaned
            if re.match(r"^https?://", cleaned, re.IGNORECASE)
            else column_page_url(column_code)
        ),
        raw_source=cleaned,
    )


def normalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = f"https:{raw}"
    resolved = urljoin(DETAIL_BASE_URL, raw)
    parsed = urlsplit(resolved)
    scheme = "https" if (parsed.hostname or "").lower() in DETAIL_HOSTS else parsed.scheme.lower()
    return urlunsplit((scheme, parsed.netloc.lower(), parsed.path, "", ""))


def make_article_id(value: str) -> str:
    cleaned = (value or "").strip()
    if re.match(r"^https?://|^//", cleaned, re.IGNORECASE):
        cleaned = urlsplit(normalize_url(cleaned)).path.rstrip("/").rsplit("/", 1)[-1]
    cleaned = re.sub(r"\.(?:html|json)$", "", cleaned.strip().strip("/"), flags=re.IGNORECASE)
    if not cleaned:
        raise ValueError("Empty Beijinghao article id")
    return f"{ARTICLE_ID_PREFIX}{cleaned}"


def _is_detail_url(url: str) -> bool:
    parsed = urlsplit(normalize_url(url))
    return (parsed.hostname or "") in DETAIL_HOSTS and bool(_DETAIL_PATH_RE.fullmatch(parsed.path))


def _date_to_iso(value: str) -> Optional[str]:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=CHINA_TZ).isoformat()
    except (AttributeError, ValueError):
        return None


def _timestamp_to_iso(value: Any) -> Optional[str]:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    return parsed.isoformat()


def _parse_list_html(
    html_text: str,
    *,
    existing_ids: Optional[set[str]] = None,
) -> list[FeedItemLike]:
    soup = BeautifulSoup(html_text or "", "html.parser")
    items: list[FeedItemLike] = []
    seen: set[str] = set()
    for row in soup.select(".picTxt"):
        anchor = row.select_one(".title a[href]")
        if anchor is None:
            continue
        url = normalize_url(str(anchor.get("href") or ""))
        if not _is_detail_url(url):
            continue
        title = str(anchor.get("title") or "").strip() or anchor.get_text(" ", strip=True)
        if not title:
            continue
        article_id = make_article_id(url)
        if article_id in seen or (existing_ids is not None and article_id in existing_ids):
            continue
        seen.add(article_id)
        date_node = row.select_one(".other-box .data, .data")
        date_text = date_node.get_text(" ", strip=True) if date_node else ""
        items.append(
            FeedItemLike(
                title=title,
                url=url,
                section=SOURCE_NAME,
                publish_time_iso=_date_to_iso(date_text),
                raw={"publish_text": date_text},
            )
        )
    return items


def _list_page_url(entry: ColumnEntry, page_number: int) -> str:
    base_url = column_page_url(entry.column_code)
    return base_url if page_number == 1 else f"{base_url}/more_{page_number}"


def _category_search_rows(payload: Any) -> list[Any]:
    """校验检索接口信封并返回这一页的原始行。

    单独拆出来是为了让「这一页还有没有内容」按**原始行数**判断：
    existing_ids 只过滤、不截停翻页（全页都已入库时继续往后翻）。
    """

    if not isinstance(payload, dict):
        raise RuntimeError("Beijinghao category search returned invalid payload")
    code = payload.get("code")
    if code not in (0, None):
        raise RuntimeError(
            f"Beijinghao category search error {code}: "
            f"{payload.get('message') or 'unknown error'}"
        )
    data = payload.get("data")
    rows = data.get("content") if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else []


def _parse_category_list_payload(
    payload: Any,
    *,
    existing_ids: Optional[set[str]] = None,
) -> list[FeedItemLike]:
    """解析客户端检索接口（/cass/search）的一页结果。"""

    items: list[FeedItemLike] = []
    seen: set[str] = set(existing_ids or set())
    for row in _category_search_rows(payload):
        if not isinstance(row, dict):
            continue
        if str(row.get("storyType") or "").strip().upper() in NON_ARTICLE_STORY_TYPES:
            continue
        url = normalize_url(str(row.get("url") or ""))
        if not _is_detail_url(url):
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        article_id = make_article_id(url)
        if article_id in seen:
            continue
        seen.add(article_id)
        publish_time_iso = _timestamp_to_iso(row.get("publishTime"))
        items.append(
            FeedItemLike(
                title=title,
                url=url,
                section=SOURCE_NAME,
                publish_time_iso=publish_time_iso,
                raw={"publish_text": publish_time_iso or ""},
            )
        )
    return items


def _collect_category_items(
    entry: CategoryEntry,
    session: requests.Session,
    *,
    page_count: int,
    known_ids: set[str],
    items: list[FeedItemLike],
    limit: Optional[int],
) -> bool:
    """按检索接口的偏移页取列表；返回是否已达 limit（True 需提前结束）。"""

    for page_number in range(0, page_count):
        response = session.get(
            entry.search_url(page_number),
            headers={"Referer": entry.page_url},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if not _category_search_rows(payload):
            # 偏移分页：原始行数空了才是到末尾，不能拿过滤后的条数判断
            break
        for item in _parse_category_list_payload(payload, existing_ids=known_ids):
            items.append(item)
            known_ids.add(make_article_id(item.url))
            if limit is not None and len(items) >= limit:
                return True
    return False


def list_items(
    entries: list[ColumnEntry],
    limit: Optional[int] = None,
    pages: Optional[int] = None,
    *,
    existing_ids: Optional[set[str]] = None,
) -> list[FeedItemLike]:
    if limit is not None and limit <= 0:
        return []
    page_count = max(1, pages or 1)
    session = _session()
    items: list[FeedItemLike] = []
    known_ids = set(existing_ids or set())
    for entry in entries:
        category_entry = parse_category_input(entry.page_url)
        if category_entry is not None:
            if _collect_category_items(
                category_entry,
                session,
                page_count=page_count,
                known_ids=known_ids,
                items=items,
                limit=limit,
            ):
                return items
            continue
        for page_number in range(1, page_count + 1):
            response = session.get(
                _list_page_url(entry, page_number),
                headers={"Referer": entry.page_url},
                timeout=15,
            )
            response.raise_for_status()
            batch = _parse_list_html(decode_response(response), existing_ids=known_ids)
            for item in batch:
                items.append(item)
                known_ids.add(make_article_id(item.url))
                if limit is not None and len(items) >= limit:
                    return items
    return items


def html_to_markdown(html_str: str) -> str:
    return common_html_to_markdown(
        html_str,
        extra_unwanted="iframe, .share, .recommend, .related, .editor, .copyright",
    )


def _detail_json_url(url: str) -> str:
    normalized = normalize_url(url)
    article_id = make_article_id(normalized).split(":", 1)[1]
    # 北京号稿件在 peking，客户端栏目分类页稿件在 xinwen；正文 JSON 同构，
    # 按稿件 URL 自己的域名取，不要写死成其中一个。
    host = (urlsplit(normalized).hostname or DETAIL_HOST).lower()
    return f"https://{host}/content/{article_id}.json"


def _parse_detail_payload(payload: dict[str, Any], url: str) -> dict[str, Any]:
    code = payload.get("code")
    if code != 0:
        raise RuntimeError(
            f"Beijinghao detail API error {code}: {payload.get('message') or 'unknown error'}"
        )
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"Beijinghao detail API returned invalid data for {url}")
    content_html = str(data.get("content") or "")
    return {
        "title": str(data.get("title") or "").strip() or None,
        "source": SOURCE_NAME,
        "publish_time": None,
        "publish_time_iso": _timestamp_to_iso(data.get("publishTime")),
        "url": normalize_url(str(data.get("url") or url)),
        "content": content_html,
        "content_markdown": html_to_markdown(content_html),
    }


def fetch_detail(url: str) -> dict[str, Any]:
    session = _session()
    response = session.get(_detail_json_url(url), headers={"Referer": normalize_url(url)}, timeout=15)
    response.raise_for_status()
    return _parse_detail_payload(response.json(), url)


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


def build_detail_update(
    item: FeedItemLike,
    article_id: str,
    data: dict[str, Any],
    *,
    detail_fetched_at: datetime,
) -> dict[str, Any]:
    return build_linked_detail_update(
        item,
        article_id,
        {**data, "source": SOURCE_NAME},
        detail_fetched_at=detail_fetched_at,
        default_source=SOURCE_NAME,
        render_content=html_to_markdown,
    )


__all__ = [
    "CategoryEntry",
    "ColumnEntry",
    "FeedItemLike",
    "build_detail_update",
    "category_meta_url",
    "feed_item_to_row",
    "fetch_detail",
    "html_to_markdown",
    "list_items",
    "make_article_id",
    "normalize_url",
    "parse_category_input",
    "parse_category_meta_name",
    "parse_column_code",
    "parse_column_input",
]
