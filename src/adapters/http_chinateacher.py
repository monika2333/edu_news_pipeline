"""中国教师报数字报（http://chinateacher.jyb.cn/zgjsb/）来源 adapter。

站点知识（代码看不出来的部分）：

- 中国教师报是**周报，每周三出版**，一期 16 版，电子版通常在北京时间
  **09:00 左右**整期上线（实测 32 期绝大多数落在周三 09:00–09:16，个别当天
  11:26 / 14:27 / 14:46 / 23:30 补传，也有次日 09:00 才补上的）。因此「每日
  任务」的真实语义是「每天检查一次有没有新一期」：默认抓站点当前最新一期
  （paperindex.htm 始终指向最新一期，非出版日指向上周三那一期），靠
  article_id 幂等去重，漏跑一天也不会丢稿。
- 版面页文件名不是简单递增：前 4 版是 node_22~node_25，第 5–16 版是
  node_82~node_93。版面目录必须从版面页的 ``#pageList1 a#pageLink`` 读出，
  不要在代码里拼这个序列。同一页还有 ``#bmdh``（悬停版名导航）是第二份副本，
  以及只出现在正文页左侧栏的 ``#pagelist1``（小写 L），三者不是一回事。
- 版面页的 ``#titleList1`` 只列本版稿件；``#btdh``（标题导航）与版面图的
  ``<area>`` usemap 会重复列出同样的稿件，全页抓锚点会得到 2–3 倍重复，
  必须圈定容器并按 content 编号去重。
- 每期都有两类**非稿件块**：第 01 版末尾固定的「日期」（报头块：日期、农历、
  期号、CN 号、邮发代号、主办/主管/出版），以及图片版里的「无标题」（正文只有
  一句图注）。两者都不是稿子，必须在列表阶段跳过。
- 正文页 ``div#ozoom td.content_tt`` 里的段落是**双层嵌套**（外层 ``<P>`` 包住
  所有 ``<p>``），自己遍历 ``<p>`` 会把同一段文字拿到两次；交给
  ``http_common.html_to_markdown`` 只走一遍文本节点。正文缩进是 U+00A0
  （``&nbsp;``），该函数只折叠 ``[ \\t\\r\\f\\v]``，喂之前要先把 nbsp 换成普通空格。
- 元数据在 ``</html>`` 之后的 ``<!--mpproperty ... /mpproperty-->`` 注释里
  （``founder-*`` 标签），这是唯一可靠来源；``founder-nodename`` 恒为空，
  版名只能从版面页取。
- 站方只支持 http（https 会直接断开连接）；jyb.cn 家族需要 ``trust_env=False``
  绕开系统代理。
"""

from __future__ import annotations

import html
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from src.adapters.http_common import build_session, decode_response, html_to_markdown

LOGGER = logging.getLogger(__name__)

BASE_URL = "http://chinateacher.jyb.cn/zgjsb"
SOURCE_NAME = "中国教师报"
SOURCE_KEY = "zgjsb"
FRONT_PAGE_FILENAME = "node_22.htm"
DEFAULT_TIMEOUT = 20.0
DEFAULT_DELAY = 0.2
NON_ARTICLE_TITLES = frozenset({"日期", "无标题"})
CHINA_TZ = timezone(timedelta(hours=8))
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/129.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

PAGE_CATALOG_SELECTOR = "#pageList1 a#pageLink"
ARTICLE_LIST_SELECTOR = "#titleList1 a[href]"
CONTENT_SELECTOR = "#ozoom td.content_tt"

SUBTITLE_DASH_PREFIX = re.compile(r"^[—–－―─-]+")
SUBTITLE_COLON_PREFIX = re.compile(r"^[：:]")
CONTENT_ID_PATTERN = re.compile(r"content_(\d+)\.htm", re.IGNORECASE)
PERIOD_DATE_PATTERN = re.compile(
    r"<period_date>\s*(\d{4}-\d{2}-\d{2})\s*</period_date>",
    re.IGNORECASE,
)
REFRESH_URL_PATTERN = re.compile(r"REFRESH[^>]*?URL\s*=\s*[\"']?([^\"'\s>]+)", re.IGNORECASE)
ISSUE_PATH_DATE_PATTERN = re.compile(r"(\d{4})-(\d{1,2})/(\d{1,2})/")


@dataclass(slots=True)
class ZgjsbIssueItem:
    """列表项（feed 阶段）：只含版面页能拿到的信息。"""

    article_id: str
    title: str
    url: str
    publish_date: str
    page_name: str
    content_id: Optional[str]

    def publish_datetime(self) -> datetime:
        base = datetime.strptime(self.publish_date, "%Y-%m-%d")
        return base.replace(tzinfo=CHINA_TZ)


@dataclass(slots=True)
class ZgjsbArticle:
    """正文（detail 阶段）：合并了 founder-* 元数据。"""

    article_id: str
    title: str
    url: str
    publish_date: str
    content_markdown: str
    page_name: str
    introtitle: Optional[str]
    subtitle: Optional[str]
    author: Optional[str]

    def publish_datetime(self) -> datetime:
        base = datetime.strptime(self.publish_date, "%Y-%m-%d")
        return base.replace(tzinfo=CHINA_TZ)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_timeout(timeout: Optional[float]) -> float:
    return float(timeout) if timeout is not None else _env_float("ZGJSB_TIMEOUT", DEFAULT_TIMEOUT)


def _resolve_delay(delay: Optional[float]) -> float:
    resolved = float(delay) if delay is not None else _env_float("ZGJSB_DELAY", DEFAULT_DELAY)
    return max(0.0, resolved)


def resolve_base_url() -> str:
    """ZGJSB_BASE_URL 或内置 BASE_URL，去掉末尾斜杠。"""

    return (os.getenv("ZGJSB_BASE_URL") or BASE_URL).strip().rstrip("/")


def _normalize_base_url(base_url: Optional[str] = None) -> str:
    return (base_url or resolve_base_url()).strip().rstrip("/")


def _session() -> requests.Session:
    # jyb.cn 家族需要绕开系统代理（与 http_chinaeducationdaily 同口径）。
    return build_session(DEFAULT_HEADERS, trust_env=False)


def resolve_issue_date(value: Optional[str] = None) -> str:
    """解析某一期的日期：显式参数 > 环境变量 ZGJSB_DATE > 抛错。

    调用方负责在两者都为空时先通过 :func:`resolve_latest_issue` 定位最新一期。
    接受 ``YYYYMMDD`` 与 ``YYYY-MM-DD``，统一返回 ``YYYY-MM-DD``。
    """

    raw = (value or os.getenv("ZGJSB_DATE") or "").strip()
    if not raw:
        raise ValueError("ZGJSB_DATE is empty; resolve the latest issue before parsing an article.")
    if re.fullmatch(r"\d{8}", raw):
        parsed = datetime.strptime(raw, "%Y%m%d")
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        parsed = datetime.strptime(raw, "%Y-%m-%d")
    else:
        raise ValueError("ZGJSB_DATE must use YYYYMMDD or YYYY-MM-DD format")
    return parsed.strftime("%Y-%m-%d")


def issue_root_url(issue_date: str, *, base_url: Optional[str] = None) -> str:
    """期次根地址：``{base}/html/YYYY-MM/DD/``；正文页相对该目录。"""

    try:
        parsed = datetime.strptime(issue_date, "%Y-%m-%d")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid issue_date: {issue_date!r}") from exc
    root = _normalize_base_url(base_url)
    return f"{root}/html/{parsed.strftime('%Y-%m')}/{parsed.strftime('%d')}/"


def _front_page_url(issue_date: str, *, base_url: Optional[str] = None) -> str:
    return f"{issue_root_url(issue_date, base_url=base_url)}{FRONT_PAGE_FILENAME}"


def _parse_paper_index_target(html_text: str) -> Optional[tuple[str, str]]:
    """从跳转页的 REFRESH 里取出 (期次日期, 相对路径/URL)。"""

    match = REFRESH_URL_PATTERN.search(html_text or "")
    if not match:
        return None
    target = match.group(1).strip()
    if not target:
        return None
    date_match = ISSUE_PATH_DATE_PATTERN.search(target)
    if not date_match:
        return None
    year, month, day = (int(part) for part in date_match.groups())
    try:
        issue_date = datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None
    return issue_date, target


def parse_paper_index(html_text: str) -> Optional[tuple[str, str]]:
    """解析 paperindex.htm 的 REFRESH 跳转。

    返回 ``(期次日期, 头版绝对 URL)``；没有 REFRESH 或格式不符时返回 ``None``。
    """

    parsed = _parse_paper_index_target(html_text)
    if parsed is None:
        return None
    issue_date, target = parsed
    return issue_date, urljoin(f"{resolve_base_url()}/", target)


def parse_period_dates(xml_text: str) -> list[str]:
    """从 period.xml 抽出所有 ``<period_date>``，返回 ``YYYY-MM-DD`` 列表。"""

    return [match.group(1) for match in PERIOD_DATE_PATTERN.finditer(xml_text or "")]


def _month_keys(reference: datetime) -> list[str]:
    """当月 → 上月 的 ``YYYY-MM`` 列表。"""

    keys: list[str] = []
    year, month = reference.year, reference.month
    for _ in range(2):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return keys


def _fetch_text(session: requests.Session, url: str, *, timeout: float) -> str:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return decode_response(response)


def resolve_latest_issue(
    *,
    session: requests.Session,
    timeout: float,
    base_url: Optional[str] = None,
) -> tuple[str, str]:
    """定位站点当前最新一期，返回 ``(期次日期, 头版绝对 URL)``。

    首选 paperindex.htm 的跳转；解析不出时按月回退 period.xml（当月 → 上月），
    取其中最大日期。两者都失败抛 ``RuntimeError``。
    """

    root = _normalize_base_url(base_url)
    errors: list[str] = []

    paper_index_url = f"{root}/paperindex.htm"
    try:
        index_html = _fetch_text(session, paper_index_url, timeout=timeout)
    except requests.RequestException as exc:
        errors.append(f"{paper_index_url}: {exc}")
    else:
        parsed = _parse_paper_index_target(index_html)
        if parsed is not None:
            issue_date, target = parsed
            return issue_date, urljoin(f"{root}/", target)
        errors.append(f"{paper_index_url}: REFRESH target not found")

    for month in _month_keys(datetime.now(CHINA_TZ)):
        period_url = f"{root}/html/{month}/period.xml"
        try:
            xml_text = _fetch_text(session, period_url, timeout=timeout)
        except requests.RequestException as exc:
            errors.append(f"{period_url}: {exc}")
            continue
        dates = parse_period_dates(xml_text)
        if not dates:
            errors.append(f"{period_url}: no period_date")
            continue
        latest = max(dates)
        return latest, _front_page_url(latest, base_url=root)

    raise RuntimeError("Failed to locate the latest ZGJSB issue. " + "; ".join(errors))


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _strip_text(value: str) -> str:
    return (value or "").strip()


def parse_page_links(html_text: str, page_url: str) -> list[tuple[str, str]]:
    """版面目录：``#pageList1 a#pageLink`` → ``[(版面名, 绝对 URL)]``。

    同一份版面页里还有 ``#bmdh`` 的重复副本，按 URL 去重后恒为实际版数。
    """

    soup = BeautifulSoup(html_text or "", "html.parser")
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.select(PAGE_CATALOG_SELECTOR):
        href = (anchor.get("href") or "").strip()
        if not href:
            continue
        url = urljoin(page_url, href)
        if url in seen:
            continue
        seen.add(url)
        page_name = _clean_text(anchor.get_text(" ", strip=True))
        links.append((page_name or url, url))
    return links


def _parse_page_items_with_skips(
    html_text: str,
    page_url: str,
    *,
    issue_date: str,
    page_name: str,
) -> tuple[list[ZgjsbIssueItem], int]:
    soup = BeautifulSoup(html_text or "", "html.parser")
    items: list[ZgjsbIssueItem] = []
    seen_ids: set[str] = set()
    skipped = 0
    for anchor in soup.select(ARTICLE_LIST_SELECTOR):
        title = _clean_text(anchor.get_text(" ", strip=True))
        if not title or title in NON_ARTICLE_TITLES:
            skipped += 1
            continue
        match = CONTENT_ID_PATTERN.search(anchor.get("href") or "")
        if not match:
            continue
        content_id = match.group(1)
        if content_id in seen_ids:
            continue
        seen_ids.add(content_id)
        items.append(
            ZgjsbIssueItem(
                article_id=f"{SOURCE_KEY}:{content_id}",
                title=title,
                url=urljoin(page_url, f"content_{content_id}.htm"),
                publish_date=issue_date,
                page_name=page_name,
                content_id=content_id,
            )
        )
    return items, skipped


def parse_page_items(
    html_text: str,
    page_url: str,
    *,
    issue_date: str,
    page_name: str,
) -> list[ZgjsbIssueItem]:
    """版面页文章列表：只认 ``#titleList1``，按 content 编号去重（保序）。

    ``url`` 一律是去掉 ``?div=`` 参数后的规范地址；「日期」「无标题」两类
    非稿件块被跳过。
    """

    items, _skipped = _parse_page_items_with_skips(
        html_text,
        page_url,
        issue_date=issue_date,
        page_name=page_name,
    )
    return items


def list_issue_items(
    *,
    issue_date: Optional[str] = None,
    limit: Optional[int] = None,
    timeout: Optional[float] = None,
    base_url: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> list[ZgjsbIssueItem]:
    """抓一期的文章列表（feed 阶段）。

    显式 ``issue_date`` / 环境变量 ``ZGJSB_DATE`` 都为空时抓站点当前最新一期。
    头版取不到或版面目录为空 → ``RuntimeError``（整期失败）；单个版面页失败
    只告警并跳过（不拖垮整期）。
    """

    timeout_s = _resolve_timeout(timeout)
    sess = session or _session()
    created_session = session is None
    try:
        explicit_date = (issue_date or os.getenv("ZGJSB_DATE") or "").strip()
        if explicit_date:
            date_value = resolve_issue_date(explicit_date)
            front_url = _front_page_url(date_value, base_url=base_url)
        else:
            date_value, front_url = resolve_latest_issue(
                session=sess,
                timeout=timeout_s,
                base_url=base_url,
            )

        try:
            front_html = _fetch_text(sess, front_url, timeout=timeout_s)
        except requests.RequestException as exc:
            raise RuntimeError(f"Failed to load ZGJSB front page {front_url}: {exc}") from exc

        page_links = parse_page_links(front_html, front_url)
        if not page_links:
            raise RuntimeError(f"No page catalog found on ZGJSB front page {front_url}")

        items: list[ZgjsbIssueItem] = []
        seen_ids: set[str] = set()
        skipped = 0
        for page_name, page_url in page_links:
            if page_url == front_url:
                page_html = front_html
            else:
                try:
                    page_html = _fetch_text(sess, page_url, timeout=timeout_s)
                except requests.RequestException as exc:
                    print(
                        f"[warn] zgjsb page fetch failed: {page_name} {page_url} ({exc})",
                        file=sys.stderr,
                    )
                    LOGGER.warning(
                        "zgjsb page fetch failed: %s %s (%s)", page_name, page_url, exc
                    )
                    continue
            page_items, page_skipped = _parse_page_items_with_skips(
                page_html,
                page_url,
                issue_date=date_value,
                page_name=page_name,
            )
            skipped += page_skipped
            for item in page_items:
                if item.article_id in seen_ids:
                    continue
                seen_ids.add(item.article_id)
                items.append(item)

        print(
            f"[info] zgjsb issue {date_value}: pages={len(page_links)} "
            f"articles={len(items)} skipped_non_articles={skipped}",
            file=sys.stderr,
        )
        if limit is not None and limit > 0:
            return items[:limit]
        return items
    finally:
        if created_session:
            sess.close()


def _extract_founder(raw_html: str, field: str) -> str:
    """从 mpproperty 注释块里取 ``<founder-{field}>``，去空白。"""

    pattern = rf"<founder-{field}>(.*?)</founder-{field}>"
    match = re.search(pattern, raw_html or "", flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return html.unescape(match.group(1)).strip()


def _merge_title_subtitle(title: str, subtitle: Optional[str]) -> str:
    """报纸版面上主标题与副标题分列两行，入库标题按副题原有连接符合并。

    副题以破折号开头 → ``主标题——副标题``；以冒号开头 → ``主标题：副标题``；
    副题为空或去掉前缀后为空 → 只用主标题。
    """

    if not title or not subtitle:
        return title
    candidate = subtitle.strip()
    if SUBTITLE_COLON_PREFIX.match(candidate):
        joiner = "："
        body = SUBTITLE_COLON_PREFIX.sub("", candidate).strip()
    else:
        joiner = "——"
        body = SUBTITLE_DASH_PREFIX.sub("", candidate).strip()
    if not body:
        return title
    return f"{title}{joiner}{body}"


def _title_cell_text(soup: BeautifulSoup) -> str:
    node = soup.select_one("td.title1")
    if node is None:
        return ""
    return _strip_text(node.get_text(" ", strip=True))


def parse_article(html_text: str, item: ZgjsbIssueItem) -> ZgjsbArticle:
    """解析一篇正文页。取不到正文或正文为空 → ``RuntimeError``。"""

    raw_html = html_text or ""
    soup = BeautifulSoup(raw_html, "html.parser")
    founder_title = _extract_founder(raw_html, "title")
    subtitle = _extract_founder(raw_html, "subtitle") or None
    introtitle = _clean_text(_extract_founder(raw_html, "introtitle")) or None
    author = _clean_text(_extract_founder(raw_html, "author")) or None
    main_title = founder_title or _title_cell_text(soup) or item.title
    title = _merge_title_subtitle(main_title, subtitle)

    content = soup.select_one(CONTENT_SELECTOR)
    if content is None:
        raise RuntimeError(f"Unable to find ZGJSB article content for {item.url}")
    # 正文缩进是 U+00A0；html_to_markdown 只折叠 [ \t\r\f\v]，先归一成普通空格。
    normalized_html = str(content).replace("&nbsp;", " ").replace("\xa0", " ")
    content_markdown = html_to_markdown(normalized_html, extra_unwanted="input")
    if not content_markdown.strip():
        raise RuntimeError(f"Empty ZGJSB article content for {item.url}")

    return ZgjsbArticle(
        article_id=item.article_id,
        title=title,
        url=item.url,
        publish_date=item.publish_date,
        content_markdown=content_markdown,
        page_name=item.page_name,
        introtitle=introtitle,
        subtitle=subtitle,
        author=author,
    )


def fetch_article(
    item: ZgjsbIssueItem,
    *,
    timeout: Optional[float] = None,
    session: Optional[requests.Session] = None,
) -> ZgjsbArticle:
    timeout_s = _resolve_timeout(timeout)
    sess = session or _session()
    created_session = session is None
    try:
        html_text = _fetch_text(sess, item.url, timeout=timeout_s)
        return parse_article(html_text, item)
    finally:
        if created_session:
            sess.close()


def crawl_issue(
    *,
    issue_date: Optional[str] = None,
    limit: Optional[int] = None,
    timeout: Optional[float] = None,
    delay: Optional[float] = None,
    base_url: Optional[str] = None,
) -> list[ZgjsbArticle]:
    timeout_s = _resolve_timeout(timeout)
    delay_s = _resolve_delay(delay)
    session = _session()
    try:
        items = list_issue_items(
            issue_date=issue_date,
            limit=limit,
            timeout=timeout_s,
            base_url=base_url,
            session=session,
        )
        articles: list[ZgjsbArticle] = []
        for index, item in enumerate(items, start=1):
            articles.append(fetch_article(item, timeout=timeout_s, session=session))
            if delay_s and index < len(items):
                time.sleep(delay_s)
        return articles
    finally:
        session.close()


def make_article_id(url: str) -> str:
    """content 编号优先（``zgjsb:650356``）；否则退化为路径式稳定 id。"""

    raw = (url or "").strip()
    match = CONTENT_ID_PATTERN.search(raw)
    if match:
        return f"{SOURCE_KEY}:{match.group(1)}"
    if not raw:
        return f"{SOURCE_KEY}:/"
    parsed = urlparse(urldefrag(raw).url)
    path = re.sub(r"\.s?html?$", "", parsed.path or "", flags=re.IGNORECASE)
    path = re.sub(r"/+", "/", path).strip()
    if not path.startswith("/"):
        path = "/" + path
    return f"{SOURCE_KEY}:{path or '/'}"


def article_to_feed_row(item: ZgjsbIssueItem, *, fetched_at: datetime) -> Dict[str, Any]:
    publish_dt = item.publish_datetime()
    publish_ts = int(publish_dt.astimezone(timezone.utc).timestamp())
    return {
        "token": None,
        "profile_url": None,
        "article_id": item.article_id,
        "title": item.title,
        "source": SOURCE_NAME,
        "publish_time": publish_ts,
        "publish_time_iso": publish_dt,
        "url": item.url,
        "summary": item.page_name or None,
        "comment_count": None,
        "digg_count": None,
        "fetched_at": fetched_at,
    }


def article_to_detail_row(article: ZgjsbArticle, *, detail_fetched_at: datetime) -> Dict[str, Any]:
    publish_dt = article.publish_datetime()
    publish_ts = int(publish_dt.astimezone(timezone.utc).timestamp())
    summary_parts = [part for part in (article.page_name, article.introtitle) if part]
    return {
        "token": None,
        "profile_url": None,
        "article_id": article.article_id,
        "title": article.title,
        "source": SOURCE_NAME,
        "publish_time": publish_ts,
        "publish_time_iso": publish_dt,
        "url": article.url,
        "summary": "｜".join(summary_parts) if summary_parts else None,
        "comment_count": None,
        "digg_count": None,
        "content_markdown": article.content_markdown,
        "detail_fetched_at": detail_fetched_at,
    }


__all__ = [
    "BASE_URL",
    "CHINA_TZ",
    "DEFAULT_DELAY",
    "DEFAULT_TIMEOUT",
    "FRONT_PAGE_FILENAME",
    "NON_ARTICLE_TITLES",
    "SOURCE_KEY",
    "SOURCE_NAME",
    "ZgjsbArticle",
    "ZgjsbIssueItem",
    "article_to_detail_row",
    "article_to_feed_row",
    "crawl_issue",
    "fetch_article",
    "issue_root_url",
    "list_issue_items",
    "make_article_id",
    "parse_article",
    "parse_page_items",
    "parse_page_links",
    "parse_paper_index",
    "parse_period_dates",
    "resolve_base_url",
    "resolve_issue_date",
    "resolve_latest_issue",
]
