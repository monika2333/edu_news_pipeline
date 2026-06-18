from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.adapters import http_bjrb
from src.adapters.http_bjrb import (
    BjrbIssueItem,
    article_to_detail_row,
    article_to_feed_row,
    make_article_id,
    parse_article,
    parse_issue_index,
    parse_period_dates,
)


ISSUE_HTML = """\
\ufeff<!DOCTYPE html>
<html>
<head><meta http-equiv="Content-Type" content="text/html; charset=GBK"></head>
<body>
<div class="nav-items">
  <div class="nav-panel-heading" pdf_href="../20260618_001/x.pdf">第1版 头版</div>
  <ul class="nav-list-group">
    <li><a data-newid="bjrb2026061800101" data-href="./20260618_001/content_20260618_001_1.htm#page0">头版新闻</a></li>
    <li><a data-newid="bjrb2026061800102" data-href="./20260618_001/content_20260618_001_2.htm#page0">第二条</a></li>
  </ul>
</div>
<div class="nav-items">
  <div class="nav-panel-heading" pdf_href="../20260618_002/x.pdf">第2版 要闻·时政</div>
  <ul class="nav-list-group">
    <li><a data-newid="bjrb2026061800201" data-href="./20260618_002/content_20260618_002_1.htm#page1">要闻标题</a></li>
  </ul>
</div>
</body>
</html>
"""


ARTICLE_HTML = """\
\ufeff<!DOCTYPE html>
<html>
<body>
<font id="guide">市政府召开常务会议</font>
<font id="main-title"><b>研究“幼有所育”工作推进情况等事项</b></font>
<font id="sub-title">市长殷勇主持会议</font>
<span id="date">2026年06月18日</span>
<div class="content my-gallery" id="content">
  <p>第一段教育内容。</p>
  <p>第二段内容。<img src="./image.jpg" alt="配图"></p>
</div>
</body>
</html>
"""


class _FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str, timeout: float) -> _FakeResponse:
        self.urls.append(url)
        return _FakeResponse(b'var datelist=["20260617"];')

    def close(self) -> None:
        return None


def test_parse_issue_index_extracts_sections_and_links() -> None:
    issue_url = "https://bjrbdzb.bjd.com.cn/bjrb/mobile/2026/20260618/20260618_m.html"

    items = parse_issue_index(ISSUE_HTML, issue_url, "20260618")

    assert len(items) == 3
    assert items[0].article_id == "bjrb:bjrb2026061800101"
    assert items[0].page_name == "第1版 头版"
    assert items[0].title == "头版新闻"
    assert items[0].url.endswith("/20260618_001/content_20260618_001_1.htm")
    assert items[2].page_name == "第2版 要闻·时政"


def test_parse_article_extracts_metadata_and_markdown() -> None:
    item = BjrbIssueItem(
        article_id="bjrb:bjrb2026061800106",
        title="目录标题",
        url="https://bjrbdzb.bjd.com.cn/bjrb/mobile/2026/20260618/20260618_001/content_20260618_001_6.htm",
        publish_date="20260618",
        page_name="第1版 头版",
        newid="bjrb2026061800106",
    )

    article = parse_article(ARTICLE_HTML, item)

    assert article.title == "研究“幼有所育”工作推进情况等事项"
    assert article.guide == "市政府召开常务会议"
    assert article.subtitle == "市长殷勇主持会议"
    assert article.publish_date == "20260618"
    assert "第一段教育内容。" in article.content_markdown
    assert "![配图](https://bjrbdzb.bjd.com.cn/bjrb/mobile/2026/20260618/20260618_001/image.jpg)" in article.content_markdown


def test_decode_html_prefers_utf8_bom_over_meta_charset() -> None:
    raw = ISSUE_HTML.encode("utf-8-sig")

    decoded = http_bjrb._decode_html(raw)

    assert "第1版 头版" in decoded


def test_period_dates_and_unavailable_issue() -> None:
    assert parse_period_dates('var datelist=["20260617","20260618"];') == ["20260617", "20260618"]
    fake_session = _FakeSession()

    items = http_bjrb.list_issue_items(issue_date="20260618", session=fake_session)

    assert items == []
    assert fake_session.urls == ["https://bjrbdzb.bjd.com.cn/bjrb/period/202606/period.js"]


def test_make_article_id_and_rows_are_stable() -> None:
    url = "https://bjrbdzb.bjd.com.cn/bjrb/mobile/2026/20260618/20260618_001/content_20260618_001_6.htm#page0"
    assert make_article_id(url, "bjrb2026061800106") == "bjrb:bjrb2026061800106"
    assert make_article_id(url) == "bjrb:bjrb/mobile/2026/20260618/20260618_001/content_20260618_001_6"

    item = BjrbIssueItem(
        article_id="bjrb:bjrb2026061800106",
        title="标题",
        url=url,
        publish_date="20260618",
        page_name="第1版 头版",
        newid="bjrb2026061800106",
    )
    article = parse_article(ARTICLE_HTML, item)
    fetched_at = datetime(2026, 6, 18, 0, 0, tzinfo=timezone.utc)

    feed_row = article_to_feed_row(item, fetched_at=fetched_at)
    detail_row = article_to_detail_row(article, detail_fetched_at=fetched_at)

    assert feed_row["source"] == "北京日报"
    assert feed_row["publish_time_iso"] == datetime(2026, 6, 18, tzinfo=timezone(timedelta(hours=8)))
    assert detail_row["summary"] == "第1版 头版｜市政府召开常务会议｜市长殷勇主持会议"
    assert detail_row["content_markdown"].startswith("第一段教育内容。")
