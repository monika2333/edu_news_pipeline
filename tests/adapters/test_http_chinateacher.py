from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence

import pytest
import requests

from src.adapters import http_chinateacher
from src.adapters.http_chinateacher import (
    ZgjsbIssueItem,
    article_to_detail_row,
    article_to_feed_row,
    list_issue_items,
    make_article_id,
    parse_article,
    parse_page_items,
    parse_page_links,
    parse_paper_index,
    parse_period_dates,
    resolve_issue_date,
    resolve_latest_issue,
)

BASE = "http://chinateacher.jyb.cn/zgjsb"
CHINA_TZ = timezone(timedelta(hours=8))

PAPER_INDEX_HTML = """\
<html><head>
<META HTTP-EQUIV="REFRESH" CONTENT="0; URL=html/2026-11/11/node_22.htm">
</head><body></body></html>
"""

PERIOD_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<periodlist>
  <period id="93041"><period_date>2026-11-04</period_date><front_page>node_22.htm</front_page></period>
  <period id="93061"><period_date>2026-11-11</period_date><front_page>node_22.htm</front_page></period>
</periodlist>
"""

# 2026-11-11 期真实版面：前 4 版 node_22~25，第 5–16 版 node_82~93，版号不递增。
FULL_PAGES = (
    ("第01版：要闻", "node_22"),
    ("第02版：新闻", "node_23"),
    ("第03版：评论", "node_24"),
    ("第04版：区域", "node_25"),
    ("第05版：教育家", "node_82"),
    ("第06版：教育家·实践", "node_83"),
    ("第07版：教育视窗", "node_84"),
    ("第08版：阅读", "node_85"),
    ("第09版：融课程", "node_86"),
    ("第10版：教育视窗", "node_87"),
    ("第11版：现代课堂", "node_88"),
    ("第12版：现代课堂·创课", "node_89"),
    ("第13版：教师教育", "node_90"),
    ("第14版：教育研究", "node_91"),
    ("第15版：职业教育", "node_92"),
    ("第16版：文化", "node_93"),
)

SIX_ITEMS = (
    ("650342", "以爱育爱  同心润心"),
    ("650345", "“好老师不是管出来的，是信任出来的”"),
    ("650349", "班额降下来，课堂“深”进去"),
    ("650351", "学生种的白菜包进立冬的饺子"),
    ("650356", "北京市体育教改观摩活动国防体育专场举行"),
    ("650357", "哈尔滨市“整本书阅读项目教研共同体”研讨会举行"),
)


def _page_catalog(pages: Sequence[tuple[str, str]], *, duplicate: bool = True) -> str:
    """版面目录；duplicate=True 时附带 #bmdh 的第二份副本（真实页面就有）。"""

    primary = "".join(
        f'<div class="right_title1"><div class="right_title-name">'
        f"<a href={node}.htm id=pageLink>{name}</a></div></div>"
        for name, node in pages
    )
    if pages:
        # 真实版面页的 #pageList1 里 16 条互不重复（实测 16 anchors / 16 unique），
        # 按 URL 去重是「模板重复渲染」的防御；这里刻意补一条同版重复锚点，把去重钉住。
        primary += (
            f'<div class="right_title1"><div class="right_title-name">'
            f"<a href={pages[0][1]}.htm id=pageLink>{pages[0][0]}</a></div></div>"
        )
    secondary = "".join(
        f"<td><a id=pageLink href={node}.htm>{name}</a></td>" for name, node in pages
    )
    extra = f'<div id="bmdh"><table><tr>{secondary}</tr></table></div>' if duplicate else ""
    return f'<div id="pageList1"><ul>{primary}</ul></div>{extra}'


def _items_html(items: Sequence[tuple[str, str]], *, duplicate: bool = True) -> str:
    """#titleList1 稿件列表；duplicate=True 时附带 #btdh 的标题导航副本。"""

    primary = "".join(
        f'<li><a href=content_{cid}.htm?div=-1><div style="display:inline">{title}</div></a></li>'
        for cid, title in items
    )
    secondary = "".join(
        f"<li><a href=content_{cid}.htm?div=-1>{title}</a></li>" for cid, title in items
    )
    extra = f'<div id="btdh"><ul>{secondary}</ul></div>' if duplicate else ""
    return f'<div id="titleList1"><ul>{primary}</ul></div>{extra}'


PAGE_HTML = (
    _page_catalog(FULL_PAGES[:2])
    + _items_html(SIX_ITEMS)
    # 真实版面页容器外的 content 链接只有 #btdh 里同编号的副本（版面图的 usemap 用的是
    # <area> 不是 <a>），所以全页扫锚点在真实页面上与容器内扫描等价；这里补一条编号
    # 不同的容器外链接，确保「只认 #titleList1」这条作用域约定真的被测到。
    + '<div class="hot"><a href=content_650999.htm?div=-1>往期推荐</a></div>'
)

SKIP_ITEMS_HTML = (
    '<html><body><div id="titleList1"><ul>'
    "<li><a href=content_650354.htm?div=-1>日期</a></li>"
    "<li><a href=content_650380.htm?div=-1>无标题</a></li>"
    "<li><a href=content_650342.htm?div=-1>以爱育爱  同心润心</a></li>"
    "</ul></div></body></html>"
)

ARTICLE_HTML = """\
<!DOCTYPE html>
<html><body>
<div class="h666"><div class="oooot"><table><tbody>
<tr><td class="title1">以爱育爱  同心润心</td></tr>
<tr><td class="title3">周书贤</td></tr>
</tbody></table></div></div>
<div id="ozoom"><table class="content_tt"><tr>
<td class="content_tt"><!--enpcontent--><P><p>&nbsp;&nbsp;&nbsp;&nbsp;本报讯（记者&nbsp;袁媛）&nbsp;11月6日，国防体育专场举行。</p><p>第二段教育内容。</p></P><!--/enpcontent--></td>
</tr></table></div>
</body></html>
<!--mpproperty <founder-date>2026-11-11</founder-date>
<founder-author>周书贤</founder-author>
<founder-title>以爱育爱  同心润心</founder-title>
<founder-subtitle>——江西上饶创新打造“以爱育爱”家长课堂实践样板</founder-subtitle>
<founder-introtitle>地方传真</founder-introtitle>
<founder-pagenum>02</founder-pagenum> /mpproperty-->
"""

NESTED_CONTENT_HTML = """\
<html><body><div id="ozoom"><table><tr>
<td class="content_tt"><P><p>甲</p><p>乙</p></P></td>
</tr></table></div></body></html>
"""


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = text.encode("utf-8")
        self.encoding = None
        self.apparent_encoding = "utf-8"

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    """按 URL 后缀返回固定页面；值为异常时抛出。"""

    def __init__(self, pages: dict[str, object]) -> None:
        self.pages = pages
        self.requested_urls: list[str] = []

    def get(self, url: str, *, timeout: float) -> _FakeResponse:
        self.requested_urls.append(url)
        for suffix, body in self.pages.items():
            if url.endswith(suffix):
                if isinstance(body, Exception):
                    raise body
                return _FakeResponse(str(body))
        raise requests.RequestException(f"unexpected URL: {url}")

    def close(self) -> None:
        return None


def _item(
    content_id: str = "650342",
    *,
    title: str = "列表标题",
    page_name: str = "第02版：新闻",
    issue_date: str = "2026-11-11",
) -> ZgjsbIssueItem:
    return ZgjsbIssueItem(
        article_id=f"zgjsb:{content_id}",
        title=title,
        url=f"{BASE}/html/{issue_date[:7]}/{issue_date[8:10]}/content_{content_id}.htm",
        publish_date=issue_date,
        page_name=page_name,
        content_id=content_id,
    )


def test_a1_parse_paper_index_reads_refresh_target() -> None:
    assert parse_paper_index(PAPER_INDEX_HTML) == (
        "2026-11-11",
        f"{BASE}/html/2026-11/11/node_22.htm",
    )
    assert parse_paper_index("<html><body>no redirect</body></html>") is None


def test_a2_parse_page_links_dedupes_catalog_and_keeps_order() -> None:
    page_url = f"{BASE}/html/2026-11/11/node_23.htm"
    html = "<html><body>" + _page_catalog(FULL_PAGES) + "</body></html>"

    links = parse_page_links(html, page_url)

    assert len(links) == 16
    assert [name for name, _url in links] == [name for name, _node in FULL_PAGES]
    assert links[0] == ("第01版：要闻", f"{BASE}/html/2026-11/11/node_22.htm")
    assert links[-1][1] == f"{BASE}/html/2026-11/11/node_93.htm"


def test_a3_parse_page_items_scopes_to_title_list_and_canonicalizes_urls() -> None:
    page_url = f"{BASE}/html/2026-11/11/node_23.htm"

    items = parse_page_items(
        PAGE_HTML, page_url, issue_date="2026-11-11", page_name="第02版：新闻"
    )

    assert len(items) == 6
    assert [item.content_id for item in items] == [cid for cid, _title in SIX_ITEMS]
    assert items[0].article_id == "zgjsb:650342"
    assert items[0].url == f"{BASE}/html/2026-11/11/content_650342.htm"
    assert "?div=" not in items[0].url
    assert all(item.page_name == "第02版：新闻" for item in items)


def test_a4_parse_page_items_skips_date_and_untitled_blocks() -> None:
    items = parse_page_items(
        SKIP_ITEMS_HTML,
        f"{BASE}/html/2026-11/11/node_22.htm",
        issue_date="2026-11-11",
        page_name="第01版：要闻",
    )

    assert [item.article_id for item in items] == ["zgjsb:650342"]


def test_a5_parse_article_merges_subtitle_and_reads_founder_metadata() -> None:
    article = parse_article(ARTICLE_HTML, _item())

    assert article.title == (
        "以爱育爱  同心润心——江西上饶创新打造“以爱育爱”家长课堂实践样板"
    )
    assert article.introtitle == "地方传真"
    assert article.author == "周书贤"
    assert article.page_name == "第02版：新闻"

    three_dashes = parse_article(
        ARTICLE_HTML.replace("——江西上饶", "———江西上饶"), _item()
    )
    assert three_dashes.title == (
        "以爱育爱  同心润心——江西上饶创新打造“以爱育爱”家长课堂实践样板"
    )
    assert three_dashes.title.count("——") == 1

    colon = parse_article(ARTICLE_HTML.replace("——江西上饶", "：江西上饶"), _item())
    assert colon.title == (
        "以爱育爱  同心润心：江西上饶创新打造“以爱育爱”家长课堂实践样板"
    )

    no_subtitle = parse_article(
        ARTICLE_HTML.replace(
            "<founder-subtitle>——江西上饶创新打造“以爱育爱”家长课堂实践样板</founder-subtitle>",
            "",
        ),
        _item(),
    )
    assert no_subtitle.title == "以爱育爱  同心润心"
    assert no_subtitle.subtitle is None


def test_a6_parse_article_does_not_duplicate_nested_paragraph_text() -> None:
    article = parse_article(NESTED_CONTENT_HTML, _item())

    assert article.content_markdown == "甲\n\n乙"


def test_a7_parse_article_normalizes_nbsp_indentation() -> None:
    article = parse_article(ARTICLE_HTML, _item())

    assert "\xa0" not in article.content_markdown
    assert article.content_markdown.startswith("本报讯")


def test_a8_parse_article_raises_without_content_or_with_empty_content() -> None:
    item = _item()

    with pytest.raises(RuntimeError):
        parse_article("<html><body><div class='h666'></div></body></html>", item)
    with pytest.raises(RuntimeError):
        parse_article(
            '<html><body><div id="ozoom"><table><tr>'
            '<td class="content_tt"><input type="checkbox"></td>'
            "</tr></table></div></body></html>",
            item,
        )


def test_a9_make_article_id_prefers_content_number_and_falls_back_to_path() -> None:
    article_url = f"{BASE}/html/2026-11/11/content_650356.htm"

    assert make_article_id(article_url) == "zgjsb:650356"
    assert make_article_id(article_url + "?div=-1") == "zgjsb:650356"
    assert make_article_id(article_url + "?div=0") == "zgjsb:650356"

    fallback = f"{BASE}/html/2026-11/11/some_page.htm?x=1#frag"
    assert make_article_id(fallback) == "zgjsb:/zgjsb/html/2026-11/11/some_page"
    assert make_article_id(fallback) == make_article_id(
        f"{BASE}/html/2026-11/11/some_page.htm"
    )


def test_a10_rows_use_page_summary_and_china_publish_time() -> None:
    item = _item()
    article = parse_article(ARTICLE_HTML, item)
    fetched_at = datetime(2026, 11, 12, 1, 0, tzinfo=timezone.utc)

    feed_row = article_to_feed_row(item, fetched_at=fetched_at)
    detail_row = article_to_detail_row(article, detail_fetched_at=fetched_at)

    expected_iso = datetime(2026, 11, 11, tzinfo=CHINA_TZ)
    expected_ts = int(expected_iso.timestamp())
    for row in (feed_row, detail_row):
        assert row["source"] == "中国教师报"
        assert row["publish_time_iso"] == expected_iso
        assert row["publish_time"] == expected_ts
        assert row["article_id"] == "zgjsb:650342"
    assert feed_row["summary"] == "第02版：新闻"
    assert detail_row["summary"] == "第02版：新闻｜地方传真"
    assert detail_row["content_markdown"].startswith("本报讯")


def test_a11_list_issue_items_skips_a_failing_page_with_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    pages = {
        "/2026-11/11/node_22.htm": _page_catalog(FULL_PAGES[:3])
        + _items_html(SIX_ITEMS[:2]),
        "/2026-11/11/node_23.htm": _items_html(SIX_ITEMS[2:3]),
        "/2026-11/11/node_24.htm": requests.RequestException("boom"),
    }
    session = _FakeSession(pages)

    items = list_issue_items(issue_date="2026-11-11", session=session)

    assert [item.article_id for item in items] == [
        "zgjsb:650342",
        "zgjsb:650345",
        "zgjsb:650349",
    ]
    assert "[warn]" in capsys.readouterr().err


def test_a12_list_issue_items_raises_without_page_catalog() -> None:
    session = _FakeSession({"/2026-11/11/node_22.htm": "<html><body>none</body></html>"})

    with pytest.raises(RuntimeError):
        list_issue_items(issue_date="2026-11-11", session=session)


def test_a13_list_issue_items_honors_issue_date_and_limit() -> None:
    pages = {
        "/2026-11/04/node_22.htm": _page_catalog(FULL_PAGES[:2])
        + _items_html(SIX_ITEMS[:2]),
        "/2026-11/04/node_23.htm": _items_html(SIX_ITEMS[2:4]),
    }
    session = _FakeSession(pages)

    items = list_issue_items(issue_date="2026-11-04", limit=2, session=session)

    assert len(items) == 2
    assert session.requested_urls[0] == f"{BASE}/html/2026-11/04/node_22.htm"
    assert all(item.publish_date == "2026-11-04" for item in items)


def test_a14_resolve_latest_issue_falls_back_to_period_xml() -> None:
    session = _FakeSession(
        {
            "paperindex.htm": "<html><head></head><body></body></html>",
            "period.xml": PERIOD_XML,
        }
    )

    issue_date, front_url = resolve_latest_issue(session=session, timeout=5.0)

    assert issue_date == "2026-11-11"
    assert front_url == f"{BASE}/html/2026-11/11/node_22.htm"
    assert session.requested_urls[0] == f"{BASE}/paperindex.htm"


def test_a15_session_bypasses_system_proxy() -> None:
    session = http_chinateacher._session()
    try:
        assert session.trust_env is False
    finally:
        session.close()


def test_a16_resolve_issue_date_priority_and_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ZGJSB_DATE", raising=False)

    assert resolve_issue_date("20261111") == "2026-11-11"
    assert resolve_issue_date("2026-11-11") == "2026-11-11"

    monkeypatch.setenv("ZGJSB_DATE", "20261104")
    assert resolve_issue_date() == "2026-11-04"
    assert resolve_issue_date("2026-11-11") == "2026-11-11"

    with pytest.raises(ValueError):
        resolve_issue_date("2026/11/11")
    with pytest.raises(ValueError):
        resolve_issue_date("2026-13-11")

    monkeypatch.delenv("ZGJSB_DATE", raising=False)
    with pytest.raises(ValueError):
        resolve_issue_date()
