from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from src.adapters.http_laodongwubao import (
    ArticleRecord,
    article_to_detail_row,
    article_to_feed_row,
    crawl_latest_issue,
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


def test_crawl_latest_issue_uses_http_fallback_and_solves_challenge():
    session = requests.Session()
    calls: list[str] = []

    challenge_html = """\
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
    redirect_html = """\
<html><head><META HTTP-EQUIV="REFRESH" CONTENT="0; URL=content/2026-06/18/node_2.htm"></head></html>
"""
    issue_html = """\
<html>
  <body>
    <a id="pageLink" href="./node_2.htm">01： 封面</a>
    <a href="content_169122.htm">过去五年北京每用3度电就有1度是绿电</a>
  </body>
</html>
"""

    def fake_get(url: str, *, timeout: float, verify: bool) -> DummyResponse:
        calls.append(url)
        if url == "https://ldwb.workerbj.cn/":
            raise requests.exceptions.SSLError("tlsv1 alert internal error")
        if url == "http://ldwb.workerbj.cn/":
            if session.cookies.get("ctwjcode"):
                return DummyResponse(redirect_html)
            return DummyResponse(challenge_html)
        if url == "http://ldwb.workerbj.cn/content/2026-06/18/node_2.htm":
            return DummyResponse(issue_html)
        if url == "http://ldwb.workerbj.cn/content/2026-06/18/content_169122.htm":
            return DummyResponse(HTML_SAMPLE)
        raise AssertionError(f"unexpected URL {url}")

    setattr(session, "get", fake_get)

    records = crawl_latest_issue(limit=1, session=session, verify_tls=False, timeout=1)

    assert calls[:3] == [
        "https://ldwb.workerbj.cn/",
        "http://ldwb.workerbj.cn/",
        "http://ldwb.workerbj.cn/",
    ]
    assert len(records) == 1
    assert records[0].article_id == "laodongwubao:/content/2026-06/18/content_169122"
    assert records[0].url == "http://ldwb.workerbj.cn/content/2026-06/18/content_169122.htm"
