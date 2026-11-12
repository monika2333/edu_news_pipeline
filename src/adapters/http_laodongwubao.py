from __future__ import annotations

import hashlib
import html
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup, Comment, Tag
from src.adapters.http_common import build_session


BASE_URL = "https://ldwb.workerbj.cn/"
HTTP_BASE_URL = "http://ldwb.workerbj.cn/"
SOURCE_NAME = "劳动午报"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    )
}
CHINA_TZ = timezone(timedelta(hours=8))
DEFAULT_TIMEOUT = 20.0
DEFAULT_VERIFY_TLS = False
DEFAULT_PUBLISH_HOUR = 0
CHALLENGE_MAX_COUNTER = 1_000_000

# 站点自 2026-09 起把「非真人访问」挡在两层验证后面：
#   1) 纯工作量证明页（`stampPrefix` / `zeroLen`，SHA-256 前缀零）——本模块能复算；
#   2) 创天 WAF 的人机识别页（`g_ctwaf_*`，靠 WebAssembly 做环境指纹）——只能在真浏览器里跑完。
# 因此纯 requests 已经拿不到正文，必须在需要时启动一次 headless chromium 换 token cookie，
# 之后所有正文请求仍然走 requests（见 ensure_access）。
WAF_COOKIE_DOMAIN = "ldwb.workerbj.cn"
WAF_COOKIE_NAME_PREFIXES = ("ctwj_", "ctw_")
WAF_BLOCK_MARKERS = ("stampPrefix", "g_ctwaf_")
DEFAULT_WAF_BROWSER_TIMEOUT = 90.0
WAF_CHALLENGE_TITLES = {"", "验证中", "403 - Forbidden"}


class WafChallengeError(RuntimeError):
    """页面仍是站点的人机验证页，说明当前 session 没有通过 WAF。"""


def _log_info(message: str) -> None:
    print(f"[info] {message}", file=sys.stderr)


def _log_warn(message: str) -> None:
    print(f"[warn] {message}", file=sys.stderr)


@dataclass
class ArticleRecord:
    article_id: str
    title: str
    url: str
    publish_date: str
    content_markdown: str
    page_name: str

    def publish_datetime(self) -> Optional[datetime]:
        if not self.publish_date:
            return None
        try:
            base = datetime.strptime(self.publish_date, "%Y-%m-%d")
            return base.replace(hour=DEFAULT_PUBLISH_HOUR, tzinfo=CHINA_TZ)
        except ValueError:
            return None


def _resolve_timeout(override: Optional[float]) -> float:
    if override is not None:
        return float(override)
    env_value = os.getenv("LDWB_TIMEOUT")
    if env_value:
        try:
            return float(env_value)
        except ValueError:
            return DEFAULT_TIMEOUT
    return DEFAULT_TIMEOUT


def _resolve_verify_tls(override: Optional[bool]) -> bool:
    if override is not None:
        return override
    env_value = os.getenv("LDWB_VERIFY_TLS")
    if env_value:
        normalized = env_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return DEFAULT_VERIFY_TLS


def _resolve_waf_browser_timeout(override: Optional[float]) -> float:
    if override is not None:
        return float(override)
    env_value = os.getenv("LDWB_WAF_TIMEOUT")
    if env_value:
        try:
            return float(env_value)
        except ValueError:
            return DEFAULT_WAF_BROWSER_TIMEOUT
    return DEFAULT_WAF_BROWSER_TIMEOUT


def _resolve_waf_browser_enabled(override: Optional[bool]) -> bool:
    if override is not None:
        return override
    env_value = os.getenv("LDWB_WAF_BROWSER")
    if env_value:
        normalized = env_value.strip().lower()
        if normalized in {"0", "false", "no", "off"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
    return True


def _session() -> requests.Session:
    return build_session(DEFAULT_HEADERS, trust_env=False)


def _looks_like_waf_block(html_text: str) -> bool:
    return any(marker in html_text for marker in WAF_BLOCK_MARKERS)


def _fetch_text(session: requests.Session, url: str, *, timeout: float, verify: bool) -> str:
    resp = session.get(url, timeout=timeout, verify=verify)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    text = resp.text
    if _maybe_solve_verification_challenge(session, url, text):
        resp = session.get(url, timeout=timeout, verify=verify)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        text = resp.text
    if _looks_like_waf_block(text):
        raise WafChallengeError(f"Laodong Wubao returned a WAF challenge page for {url}")
    return text


def _maybe_solve_verification_challenge(session: requests.Session, url: str, html_text: str) -> bool:
    """解站点第一层工作量证明：SHA-256(前缀 + 十进制计数) 的前 zeroLen 位为 0。

    2026-09 之后站点把前缀零算法从 SHA-1 换成 SHA-256，仍用 SHA-1 算出的
    counter 会被判无效并回 403。第二层（`g_ctwaf_*` 环境指纹）复算不了，
    由 ensure_access 交给浏览器处理。
    """
    prefix_match = re.search(r'var\s+stampPrefix\s*=\s*"([^"]+)"', html_text)
    zero_match = re.search(r"var\s+zeroLen\s*=\s*(\d+)", html_text)
    if not prefix_match or not zero_match:
        return False

    stamp_prefix = prefix_match.group(1)
    zero_len = int(zero_match.group(1))
    if zero_len <= 0:
        return False

    target = "0" * zero_len
    for counter in range(CHALLENGE_MAX_COUNTER):
        digest = hashlib.sha256(f"{stamp_prefix}{counter}".encode("utf-8")).hexdigest()
        if digest.startswith(target):
            hostname = urlparse(url).hostname
            if hostname:
                session.cookies.set("ctwjcode", str(counter), domain=hostname, path="/")
                session.cookies.set("stampprefix", stamp_prefix, domain=hostname, path="/")
            else:
                session.cookies.set("ctwjcode", str(counter), path="/")
                session.cookies.set("stampprefix", stamp_prefix, path="/")
            return True
    raise RuntimeError("Unable to solve Laodong Wubao verification challenge.")


def _has_waf_cookies(cookies: Sequence[Dict[str, str]]) -> bool:
    for cookie in cookies:
        name = str(cookie.get("name") or "")
        if name.startswith(WAF_COOKIE_NAME_PREFIXES):
            return True
    return False


def _browser_waf_cookies(*, timeout: Optional[float] = None) -> List[Tuple[str, str]]:
    """用 headless chromium 跑完站点人机验证，返回可复用到 requests 的 token cookie。

    站点第二层验证依赖 WebAssembly 环境指纹，Python 侧无法复算；这里只借浏览器
    通过验证并取 cookie，正文抓取仍由 requests 完成。Chromium 默认会暴露
    `navigator.webdriver`，站点据此直接回 403，所以必须带
    `--disable-blink-features=AutomationControlled` 启动。
    """
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - 依赖缺失属于部署问题
        raise RuntimeError(
            "Laodong Wubao now needs a real browser to pass its WAF challenge; "
            "install playwright and run `playwright install chromium`."
        ) from exc

    wait_seconds = _resolve_waf_browser_timeout(timeout)
    cookies: List[Dict[str, str]] = []
    title = ""
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                context = browser.new_context(
                    user_agent=DEFAULT_HEADERS["User-Agent"],
                    locale="zh-CN",
                )
                page = context.new_page()
                page.goto(
                    HTTP_BASE_URL,
                    wait_until="domcontentloaded",
                    timeout=wait_seconds * 1000,
                )
                deadline = time.monotonic() + wait_seconds
                while time.monotonic() < deadline:
                    try:
                        title = page.title()
                    except PlaywrightError:
                        # 挑战通过后页面会整页 reload，此刻读标题会撞上导航。
                        time.sleep(1.0)
                        continue
                    if title == "403 - Forbidden":
                        # 被判成机器人时站点给的是终态页，继续等只会拖到超时。
                        break
                    if title and title not in WAF_CHALLENGE_TITLES:
                        break
                    time.sleep(1.0)
                cookies = [
                    {"name": str(c.get("name") or ""), "value": str(c.get("value") or "")}
                    for c in context.cookies()
                    if str(c.get("domain") or "").endswith(WAF_COOKIE_DOMAIN)
                ]
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise RuntimeError(f"Laodong Wubao WAF browser handshake failed: {exc}") from exc

    if not _has_waf_cookies(cookies):
        raise RuntimeError(
            "Laodong Wubao WAF challenge did not return a token cookie "
            f"(last page title: {title!r})."
        )
    return [(cookie["name"], cookie["value"]) for cookie in cookies]


def ensure_access(
    session: requests.Session,
    *,
    timeout: float,
    verify: bool,
    allow_browser: Optional[bool] = None,
) -> None:
    """确保 session 已通过站点人机验证，可以继续抓正文。

    先发一次探测请求：正常页面直接返回；只有在拿到工作量证明页或第二层 WAF 页时
    才启动一次浏览器换取 token cookie 并重试。`LDWB_WAF_BROWSER=0` 可关闭浏览器
    兜底（关闭后遇到第二层验证会直接报错，便于排查是不是站点又改了验证）。
    """
    use_browser = _resolve_waf_browser_enabled(allow_browser)
    try:
        probe = _fetch_text(session, HTTP_BASE_URL, timeout=timeout, verify=verify)
    except WafChallengeError as exc:
        blocked_reason = str(exc)
    except requests.RequestException as exc:
        blocked_reason = str(exc)
        _log_warn(f"ldwb access probe failed: {exc}")
    else:
        if not _looks_like_waf_block(probe):
            return
        blocked_reason = "probe response still contains the WAF challenge markers"

    if not use_browser:
        raise RuntimeError(
            f"Laodong Wubao is behind its WAF challenge ({blocked_reason}) "
            "and LDWB_WAF_BROWSER is disabled."
        )

    _log_info("ldwb needs the browser WAF handshake; launching headless chromium")
    for name, value in _browser_waf_cookies():
        session.cookies.set(name, value, domain=WAF_COOKIE_DOMAIN, path="/")

    try:
        retry = _fetch_text(session, HTTP_BASE_URL, timeout=timeout, verify=verify)
    except WafChallengeError as exc:
        raise RuntimeError(
            "Laodong Wubao still returns its WAF challenge after the browser handshake: "
            f"{exc}"
        ) from exc
    if _looks_like_waf_block(retry):
        raise RuntimeError(
            "Laodong Wubao still returns its WAF challenge after the browser handshake."
        )
    _log_info("ldwb WAF handshake passed")



def _latest_issue_url(session: requests.Session, *, timeout: float, verify: bool) -> str:
    errors: List[str] = []
    for base_url in (BASE_URL, HTTP_BASE_URL):
        try:
            html_text = _fetch_text(session, base_url, timeout=timeout, verify=verify)
        except requests.RequestException as exc:
            errors.append(f"{base_url}: {exc}")
            continue
        match = re.search(r'URL=([^"\']+)', html_text)
        if match:
            return urljoin(base_url, match.group(1).strip())
        errors.append(f"{base_url}: redirect URL not found")
    detail = "; ".join(errors)
    raise RuntimeError(f"Failed to locate redirect URL for the latest issue. {detail}")


def _extract_page_links(issue_html: str, issue_url: str) -> List[Tuple[str, str]]:
    soup = BeautifulSoup(issue_html, "html.parser")
    links: List[Tuple[str, str]] = []
    seen = set()
    for anchor in soup.select("a#pageLink"):
        href = anchor.get("href")
        if not href:
            continue
        full_url = urljoin(issue_url, href)
        if full_url in seen:
            continue
        text = " ".join(anchor.get_text(strip=True).split())
        links.append((text or full_url, full_url))
        seen.add(full_url)
    if not links:
        links.append(("默认版面", issue_url))
    return links


def _extract_article_links(page_html: str, page_url: str) -> List[str]:
    soup = BeautifulSoup(page_html, "html.parser")
    links: List[str] = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        lowered = href.lower()
        if "content_" not in lowered or not lowered.endswith(".htm"):
            continue
        full_url = urljoin(page_url, href)
        if full_url in seen:
            continue
        seen.add(full_url)
        links.append(full_url)
    return links


def _extract_enp_property(raw_html: str, field: str) -> str:
    pattern = rf"<founder-{field}>(.*?)</founder-{field}>"
    match = re.search(pattern, raw_html, flags=re.IGNORECASE | re.DOTALL)
    return html.unescape(match.group(1)).strip() if match else ""


def _guess_publish_date(article_url: str, fallback: str = "") -> str:
    match = re.search(r"/(\d{4}-\d{2})/(\d{2})/", article_url)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return fallback


def _founder_content_to_markdown(container: Tag) -> str:
    paragraphs: List[str] = []
    for p in container.find_all("p"):
        text = _normalize_text(p.get_text(" ", strip=True))
        if text:
            paragraphs.append(text)
    if not paragraphs:
        text = _normalize_text(container.get_text("\n", strip=True))
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def parse_article(html_text: str, article_url: str, page_name: str) -> ArticleRecord:
    soup = BeautifulSoup(html_text, "html.parser")
    content_root = soup.find("founder-content")
    if not content_root:
        raise RuntimeError(f"Unable to find founder-content for {article_url}")
    content_markdown = _founder_content_to_markdown(content_root)

    metadata_comment = next(
        (node for node in soup.find_all(string=lambda t: isinstance(t, Comment)) if "enpproperty" in node),
        None,
    )
    metadata_block = metadata_comment or ""
    title = _extract_enp_property(metadata_block, "title")
    subtitle = _extract_enp_property(metadata_block, "subtitle")
    if subtitle:
        title = f"{title}｜{subtitle}" if title else subtitle
    if not title and soup.title:
        title = _normalize_text(soup.title.get_text(strip=True))
    publish_date = _extract_enp_property(metadata_block, "date")
    publish_date = publish_date or _guess_publish_date(article_url)

    return ArticleRecord(
        article_id=make_article_id(article_url),
        title=title or "",
        url=article_url,
        publish_date=publish_date,
        content_markdown=content_markdown,
        page_name=page_name,
    )


def crawl_latest_issue(
    limit: Optional[int] = None,
    *,
    session: Optional[requests.Session] = None,
    verify_tls: Optional[bool] = None,
    timeout: Optional[float] = None,
    allow_waf_browser: Optional[bool] = None,
) -> List[ArticleRecord]:
    timeout_s = _resolve_timeout(timeout)
    verify = _resolve_verify_tls(verify_tls)
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    sess = session or _session()
    created_session = session is None
    try:
        ensure_access(
            sess,
            timeout=timeout_s,
            verify=verify,
            allow_browser=allow_waf_browser,
        )
        latest_issue = _latest_issue_url(sess, timeout=timeout_s, verify=verify)
        issue_html = _fetch_text(sess, latest_issue, timeout=timeout_s, verify=verify)
        page_links = _extract_page_links(issue_html, latest_issue)

        records: List[ArticleRecord] = []
        seen_urls = set()
        for page_name, page_url in page_links:
            page_html = _fetch_text(sess, page_url, timeout=timeout_s, verify=verify)
            article_links = _extract_article_links(page_html, page_url)
            for article_url in article_links:
                if article_url in seen_urls:
                    continue
                detail_html = _fetch_text(sess, article_url, timeout=timeout_s, verify=verify)
                record = parse_article(detail_html, article_url, page_name)
                records.append(record)
                seen_urls.add(article_url)
                if limit and len(records) >= limit:
                    return records
        return records
    finally:
        if created_session:
            sess.close()


def make_article_id(url: str) -> str:
    if not url:
        return "laodongwubao:/"
    normalized = re.sub(r"^https?://[^/]+", "", url.strip())
    normalized = re.sub(r"\.s?html?$", "", normalized)
    normalized = re.sub(r"/+", "/", normalized).strip()
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return f"laodongwubao:{normalized or '/'}"


def article_to_feed_row(record: ArticleRecord, *, fetched_at: datetime) -> Dict[str, object]:
    publish_dt = record.publish_datetime()
    publish_ts = int(publish_dt.astimezone(timezone.utc).timestamp()) if publish_dt else None
    return {
        "token": None,
        "profile_url": None,
        "article_id": record.article_id,
        "title": record.title,
        "source": SOURCE_NAME,
        "publish_time": publish_ts,
        "publish_time_iso": publish_dt,
        "url": record.url,
        "summary": None,
        "comment_count": None,
        "digg_count": None,
        "fetched_at": fetched_at,
    }


def article_to_detail_row(record: ArticleRecord, *, detail_fetched_at: datetime) -> Dict[str, object]:
    publish_dt = record.publish_datetime()
    publish_ts = int(publish_dt.astimezone(timezone.utc).timestamp()) if publish_dt else None
    return {
        "token": None,
        "profile_url": None,
        "article_id": record.article_id,
        "title": record.title,
        "source": SOURCE_NAME,
        "publish_time": publish_ts,
        "publish_time_iso": publish_dt,
        "url": record.url,
        "summary": None,
        "comment_count": None,
        "digg_count": None,
        "content_markdown": record.content_markdown,
        "detail_fetched_at": detail_fetched_at,
    }


__all__ = [
    "ArticleRecord",
    "SOURCE_NAME",
    "WafChallengeError",
    "crawl_latest_issue",
    "ensure_access",
    "parse_article",
    "make_article_id",
    "article_to_feed_row",
    "article_to_detail_row",
]
