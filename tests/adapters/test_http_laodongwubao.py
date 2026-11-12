from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
import requests

from src.adapters import http_laodongwubao
from src.adapters.http_laodongwubao import (
    ArticleRecord,
    article_to_detail_row,
    article_to_feed_row,
    crawl_latest_issue,
    ensure_access,
    make_article_id,
    parse_article,
    SOURCE_NAME,
)


HTML_SAMPLE = """\
<html>
  <head>
    <title>示例标题</title>
  </head>
  <body>
    <!--enpproperty
      <founder-title>示例标题</founder-title>
      <founder-subtitle>副标题</founder-subtitle>
      <founder-date>2025-11-13</founder-date>
    -->
    <founder-content>
      <p>第一段</p>
      <p>第二段</p>
    </founder-content>
  </body>
</html>
"""


def test_make_article_id_normalizes_urls():
    url = "https://ldwb.workerbj.cn/content/2025-11/13/content_161580.htm"
    assert make_article_id(url) == "laodongwubao:/content/2025-11/13/content_161580"


def test_parse_article_extracts_metadata():
    record = parse_article(HTML_SAMPLE, "https://ldwb.workerbj.cn/content/2025-11/13/content_161580.htm", "A01")
    assert record.title == "示例标题｜副标题"
    assert record.publish_date == "2025-11-13"
    assert "第一段" in record.content_markdown
    publish_dt = record.publish_datetime()
    assert publish_dt == datetime(2025, 11, 13, 0, 0, tzinfo=timezone(timedelta(hours=8)))


def test_article_rows_use_fixed_source():
    record = ArticleRecord(
        article_id=make_article_id("https://ldwb.workerbj.cn/content/2025-11/13/content_161580.htm"),
        title="示例标题",
        url="https://ldwb.workerbj.cn/content/2025-11/13/content_161580.htm",
        publish_date="2025-11-13",
        content_markdown="段落一\n\n段落二",
        page_name="A01",
    )
    fetched_at = datetime(2025, 11, 13, 8, 0, tzinfo=timezone.utc)
    feed_row = article_to_feed_row(record, fetched_at=fetched_at)
    assert feed_row["source"] == SOURCE_NAME
    assert feed_row["publish_time_iso"] == datetime(2025, 11, 13, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    detail_row = article_to_detail_row(record, detail_fetched_at=fetched_at)
    assert detail_row["content_markdown"] == "段落一\n\n段落二"
    assert detail_row["source"] == SOURCE_NAME


class DummyResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code
        self.encoding = "utf-8"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


POW_CHALLENGE_HTML = """\
<html>
  <head><title>验证中</title></head>
  <body>
    <script>
      var zeroLen=1;
      var stampPrefix="abc";
      var counter=0;
    </script>
  </body>
</html>
"""

WAF_CHALLENGE_HTML = """\
<html>
  <head><title>非法访问</title></head>
  <body>
    <script>
      const g_ctwaf_env_check_info = `masked`;
      document.querySelector('#challenge');
    </script>
  </body>
</html>
"""

REDIRECT_HTML = """\
<html><head><META HTTP-EQUIV="REFRESH" CONTENT="0; URL=content/2026-06/18/node_2.htm"></head></html>
"""

ISSUE_HTML = """\
<html>
  <body>
    <a id="pageLink" href="./node_2.htm">01： 封面</a>
    <a href="content_169122.htm">过去五年北京每用3度电就有1度是绿电</a>
  </body>
</html>
"""


def test_verification_challenge_uses_sha256_prefix_zero():
    """站点把工作量证明从前缀零 SHA-1 换成了 SHA-256，用 SHA-1 会被判 403。"""
    session = requests.Session()
    challenge_html = '<script>var zeroLen=3; var stampPrefix="ldwb-probe";</script>'
    expected = next(
        counter
        for counter in range(1_000_000)
        if hashlib.sha256(f"ldwb-probe{counter}".encode("utf-8")).hexdigest().startswith("000")
    )

    solved = http_laodongwubao._maybe_solve_verification_challenge(
        session, "http://ldwb.workerbj.cn/", challenge_html
    )

    assert solved is True
    assert session.cookies.get("ctwjcode") == str(expected)
    assert session.cookies.get("stampprefix") == "ldwb-probe"


def test_ensure_access_skips_browser_when_page_is_open(monkeypatch: pytest.MonkeyPatch):
    session = requests.Session()
    calls: list[str] = []

    def fake_get(url: str, *, timeout: float, verify: bool) -> DummyResponse:
        calls.append(url)
        return DummyResponse(REDIRECT_HTML)

    def fail_browser() -> list[tuple[str, str]]:
        raise AssertionError("browser must not be launched when the page is open")

    monkeypatch.setattr(http_laodongwubao, "_browser_waf_cookies", fail_browser)
    setattr(session, "get", fake_get)

    ensure_access(session, timeout=1, verify=False, allow_browser=True)

    assert calls == ["http://ldwb.workerbj.cn/"]


def test_ensure_access_uses_browser_tokens_for_waf_page(monkeypatch: pytest.MonkeyPatch):
    session = requests.Session()
    calls: list[str] = []
    browser_calls: list[int] = []

    def fake_get(url: str, *, timeout: float, verify: bool) -> DummyResponse:
        calls.append(url)
        if session.cookies.get("ctwj_token_probe"):
            return DummyResponse(REDIRECT_HTML)
        return DummyResponse(WAF_CHALLENGE_HTML)

    def fake_browser() -> list[tuple[str, str]]:
        browser_calls.append(1)
        return [("ctwj_token_probe", "token-value"), ("ctw_dnytoken_token_probe", "dny")]

    monkeypatch.setattr(http_laodongwubao, "_browser_waf_cookies", fake_browser)
    setattr(session, "get", fake_get)

    ensure_access(session, timeout=1, verify=False, allow_browser=True)

    assert len(browser_calls) == 1
    assert calls == ["http://ldwb.workerbj.cn/", "http://ldwb.workerbj.cn/"]
    assert session.cookies.get("ctwj_token_probe") == "token-value"


def test_ensure_access_reports_when_browser_disabled(monkeypatch: pytest.MonkeyPatch):
    session = requests.Session()

    def fake_get(url: str, *, timeout: float, verify: bool) -> DummyResponse:
        return DummyResponse(WAF_CHALLENGE_HTML)

    def fail_browser() -> list[tuple[str, str]]:
        raise AssertionError("browser must not be launched when disabled")

    monkeypatch.setattr(http_laodongwubao, "_browser_waf_cookies", fail_browser)
    setattr(session, "get", fake_get)

    with pytest.raises(RuntimeError, match="LDWB_WAF_BROWSER is disabled"):
        ensure_access(session, timeout=1, verify=False, allow_browser=False)


def test_ensure_access_reports_persisting_waf_after_browser(monkeypatch: pytest.MonkeyPatch):
    session = requests.Session()

    def fake_get(url: str, *, timeout: float, verify: bool) -> DummyResponse:
        return DummyResponse(WAF_CHALLENGE_HTML)

    def fake_browser() -> list[tuple[str, str]]:
        return [("ctwj_token_probe", "token-value")]

    monkeypatch.setattr(http_laodongwubao, "_browser_waf_cookies", fake_browser)
    setattr(session, "get", fake_get)

    with pytest.raises(RuntimeError, match="after the browser handshake"):
        ensure_access(session, timeout=1, verify=False, allow_browser=True)


def test_crawl_latest_issue_primes_access_then_crawls(monkeypatch: pytest.MonkeyPatch):
    session = requests.Session()
    calls: list[str] = []

    def fail_browser() -> list[tuple[str, str]]:
        raise AssertionError("browser must not be launched when the PoW answer is accepted")

    def fake_get(url: str, *, timeout: float, verify: bool) -> DummyResponse:
        calls.append(url)
        if url == "https://ldwb.workerbj.cn/":
            raise requests.exceptions.SSLError("tlsv1 alert internal error")
        if url == "http://ldwb.workerbj.cn/":
            if session.cookies.get("ctwjcode"):
                return DummyResponse(REDIRECT_HTML)
            return DummyResponse(POW_CHALLENGE_HTML)
        if url == "http://ldwb.workerbj.cn/content/2026-06/18/node_2.htm":
            return DummyResponse(ISSUE_HTML)
        if url == "http://ldwb.workerbj.cn/content/2026-06/18/content_169122.htm":
            return DummyResponse(HTML_SAMPLE)
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(http_laodongwubao, "_browser_waf_cookies", fail_browser)
    setattr(session, "get", fake_get)

    records = crawl_latest_issue(limit=1, session=session, verify_tls=False, timeout=1)

    assert calls == [
        "http://ldwb.workerbj.cn/",
        "http://ldwb.workerbj.cn/",
        "https://ldwb.workerbj.cn/",
        "http://ldwb.workerbj.cn/",
        "http://ldwb.workerbj.cn/content/2026-06/18/node_2.htm",
        # 该刊期首页本身就是 node_2（a#pageLink 指向自己），所以版面页再取一次。
        "http://ldwb.workerbj.cn/content/2026-06/18/node_2.htm",
        "http://ldwb.workerbj.cn/content/2026-06/18/content_169122.htm",
    ]
    assert len(records) == 1
    assert records[0].article_id == "laodongwubao:/content/2026-06/18/content_169122"
    assert records[0].url == "http://ldwb.workerbj.cn/content/2026-06/18/content_169122.htm"

