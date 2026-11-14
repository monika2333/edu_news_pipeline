from __future__ import annotations

from datetime import datetime, timezone

import pytest
import requests

from src.adapters import http_bjwb
from src.adapters.http_bjrb import BjrbArticle, BjrbIssueItem, parse_article

PAPERINDEX_HTML = (
    "<html><head></head><body>"
    '<script type="text/javascript">'
    'window.location.href="./mobile/2026/20261114/20261114_m.html?v="'
    "+new Date().valueOf();;"
    "</script></body></html>"
)

ISSUE_HTML = """\
<!DOCTYPE html>
<html>
<body>
<div class="nav-items">
  <div class="nav-panel-heading">第1版 头版</div>
  <ul class="nav-list-group">
    <li><a data-newid="bjwb2026111400101" data-href="./20261114_001/content_20261114_001_1.htm#page0">晨雾漫染</a></li>
  </ul>
</div>
<div class="nav-items">
  <div class="nav-panel-heading">第2版 要闻时事</div>
  <ul class="nav-list-group">
    <li><a data-newid="bjwb2026111400201" data-href="./20261114_002/content_20261114_002_1.htm#page1">以高质量党建引领企业高质量发展</a></li>
  </ul>
</div>
</body>
</html>
"""

ARTICLE_HTML = """\
<!DOCTYPE html>
<html>
<body>
<font id="guide">导语</font>
<font id="main-title"><b>晚报正文标题</b></font>
<font id="sub-title">——副标题</font>
<span id="date">2026年11月14日</span>
<div class="content my-gallery" id="content">
  <p>第一段教育内容。</p>
</div>
</body>
</html>
"""

EMPTY_TEXT_ARTICLE_HTML = """\
<!DOCTYPE html>
<html>
<body>
<font id="main-title">■广告</font>
<span id="date">2026年11月14日</span>
<div class="content my-gallery" id="content">
  <p><span align="center" style="display: block;"><img data-src="./20261114_007_244960.jpg"/></span></p>
</div>
</body>
</html>
"""


class _FakeResponse:
    def __init__(self, content: bytes, *, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


class _FakeSession:
    def __init__(self, responses: dict[str, _FakeResponse] | None = None) -> None:
        self.urls: list[str] = []
        self._responses = responses or {}

    def get(self, url: str, timeout: float) -> _FakeResponse:
        self.urls.append(url)
        for marker, response in self._responses.items():
            if marker in url:
                return response
        return _FakeResponse(ARTICLE_HTML.encode("utf-8"))

    def close(self) -> None:
        return None


def _item(article_id: str, title: str) -> BjrbIssueItem:
    return BjrbIssueItem(
        article_id=article_id,
        title=title,
        url=f"https://bjrbdzb.bjd.com.cn/bjwb/mobile/2026/20261114/20261114_001/{article_id}.htm",
        publish_date="20261114",
        page_name="第1版 头版",
        newid=article_id,
    )


def test_paperindex_target_is_resolved_and_cache_buster_dropped() -> None:
    issue_url = http_bjwb.issue_url_from_paperindex(
        PAPERINDEX_HTML,
        "https://bjrbdzb.bjd.com.cn/bjwb/paperindex.htm",
    )

    assert issue_url == (
        "https://bjrbdzb.bjd.com.cn/bjwb/mobile/2026/20261114/20261114_m.html"
    )
    assert http_bjwb.issue_date_from_url(issue_url) == "20261114"


def test_paperindex_without_redirect_is_a_structure_failure() -> None:
    with pytest.raises(RuntimeError, match="location.href"):
        http_bjwb.parse_paperindex_target("<html><body>empty</body></html>")

    with pytest.raises(RuntimeError, match="无法从北京晚报刊期地址解析出日期"):
        http_bjwb.issue_date_from_url("https://bjrbdzb.bjd.com.cn/bjwb/unknown.html")


def test_list_issue_items_follows_paperindex_and_prefixes_ids_with_bjwb() -> None:
    session = _FakeSession({
        "paperindex.htm": _FakeResponse(PAPERINDEX_HTML.encode("utf-8")),
        "20261114_m.html": _FakeResponse(ISSUE_HTML.encode("utf-8")),
    })

    items = http_bjwb.list_issue_items(session=session)

    assert [item.article_id for item in items] == [
        "bjwb:bjwb2026111400101",
        "bjwb:bjwb2026111400201",
    ]
    assert items[0].page_name == "第1版 头版"
    assert items[0].publish_date == "20261114"
    assert session.urls[0].endswith("/bjwb/paperindex.htm")
    assert session.urls[1].endswith("/bjwb/mobile/2026/20261114/20261114_m.html")


def test_explicit_issue_date_skips_paperindex(monkeypatch) -> None:
    monkeypatch.setenv("BJWB_DATE", "20261113")
    session = _FakeSession({
        "20261113_m.html": _FakeResponse(ISSUE_HTML.encode("utf-8")),
    })

    items = http_bjwb.list_issue_items(session=session)

    assert [item.publish_date for item in items] == ["20261113", "20261113"]
    assert len(session.urls) == 1
    assert "paperindex" not in session.urls[0]
    assert session.urls[0].endswith("/bjwb/mobile/2026/20261113/20261113_m.html")


def test_list_issue_items_marks_404_issue_as_not_published() -> None:
    session = _FakeSession({
        "paperindex.htm": _FakeResponse(
            PAPERINDEX_HTML.replace("20261114", "20261115").encode("utf-8")
        ),
        "20261115_m.html": _FakeResponse(b"Not Found", status_code=404),
    })

    with pytest.raises(http_bjwb.BjwbIssueNotPublished, match="20261115 期尚未上线"):
        http_bjwb.list_issue_items(session=session)


def test_list_issue_items_propagates_real_http_failures() -> None:
    paperindex_failure = _FakeSession({
        "paperindex.htm": _FakeResponse(b"server error", status_code=500),
    })
    with pytest.raises(requests.HTTPError):
        http_bjwb.list_issue_items(session=paperindex_failure)

    issue_failure = _FakeSession({
        "paperindex.htm": _FakeResponse(PAPERINDEX_HTML.encode("utf-8")),
        "20261114_m.html": _FakeResponse(b"server error", status_code=500),
    })
    with pytest.raises(requests.HTTPError):
        http_bjwb.list_issue_items(session=issue_failure)


def test_published_issue_page_without_articles_raises_parse_failure() -> None:
    session = _FakeSession({
        "paperindex.htm": _FakeResponse(PAPERINDEX_HTML.encode("utf-8")),
        "20261114_m.html": _FakeResponse(b"<html><body>empty shell</body></html>"),
    })

    with pytest.raises(RuntimeError, match="未解析出稿件"):
        http_bjwb.list_issue_items(session=session)


def test_image_only_page_is_not_a_failure_for_bjwb_but_still_is_for_bjrb() -> None:
    """纯图片版（广告等）正文留空：晚报允许，日报仍按原行为报错。"""

    item = BjrbIssueItem(
        article_id="bjwb:bjwb2026111400703",
        title="■广告",
        url="https://bjrbdzb.bjd.com.cn/bjwb/mobile/2026/20261114/20261114_007/content_20261114_007_3.htm",
        publish_date="20261114",
        page_name="第7版 纵闻天下",
        newid="bjwb2026111400703",
    )
    session = _FakeSession({
        "content_20261114_007_3.htm": _FakeResponse(
            EMPTY_TEXT_ARTICLE_HTML.encode("utf-8")
        ),
    })

    article = http_bjwb.fetch_article(item, session=session)
    assert article.content_markdown == ""
    assert article.title == "■广告"

    with pytest.raises(RuntimeError, match="Empty BJRB article content"):
        parse_article(EMPTY_TEXT_ARTICLE_HTML, item)


def test_rows_use_beijing_evening_news_as_source() -> None:
    item = _item("bjwb:bjwb2026111400101", "晨雾漫染")
    article = BjrbArticle(
        article_id=item.article_id,
        title="晚报正文标题——副标题",
        url=item.url,
        publish_date="20261114",
        content_markdown="第一段教育内容。",
        page_name="第1版 头版",
        guide="导语",
        subtitle="——副标题",
        newid=item.newid,
    )
    fetched_at = datetime(2026, 11, 14, 4, 0, tzinfo=timezone.utc)

    feed_row = http_bjwb.article_to_feed_row(item, fetched_at=fetched_at)
    detail_row = http_bjwb.article_to_detail_row(
        article,
        detail_fetched_at=fetched_at,
    )

    assert feed_row["source"] == "北京晚报"
    assert feed_row["article_id"] == "bjwb:bjwb2026111400101"
    assert feed_row["summary"] == "第1版 头版"
    assert detail_row["source"] == "北京晚报"
    assert detail_row["summary"] == "第1版 头版｜导语｜——副标题"
    assert detail_row["content_markdown"] == "第一段教育内容。"
