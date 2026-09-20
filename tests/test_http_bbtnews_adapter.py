from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from src.adapters import http_bbtnews as bbt

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "bbtnews"

# fixture 抓取于 2026-09-20；列表日期分布以此为基准做确定性断言。
TODAY = date(2026, 9, 20)

P1_URL = "https://www.bbtnews.com.cn/chuizhipd/chanjingzx/jiaoyupd/"
P2_URL = "https://www.bbtnews.com.cn/chuizhipd/chanjingzx/jiaoyupd/2.shtml"
TARGET_ID = "bbtnews:606364"
TARGET_URL = "https://www.bbtnews.com.cn/2026/0920/606364.shtml"
HEAD_ID = "bbtnews:605471"  # 头条区（news-head）里的文章，不在主列表行里
HOT_ID = "bbtnews:606280"  # 热点推荐（hot-list-news）里的文章，与主列表重复
PAGE2_FIRST_ID = "bbtnews:603508"
# 页脚"热门新闻"区里的其他频道稿件，解析必须排除。
FOOTER_IDS = ("bbtnews:606042", "bbtnews:606171", "bbtnews:606270")


def _fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.encoding = "utf-8"

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(
        self,
        pages: dict[str, str],
        *,
        failing_urls: frozenset[str] = frozenset(),
    ) -> None:
        self.pages = pages
        self.failing_urls = failing_urls
        self.requested: list[str] = []

    def get(self, url: str, timeout: Any = None) -> _FakeResponse:
        self.requested.append(url)
        if url in self.failing_urls:
            raise RuntimeError(f"simulated failure for {url}")
        return _FakeResponse(self.pages.get(url, ""))


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    pages: dict[str, str],
    *,
    failing_urls: frozenset[str] = frozenset(),
) -> _FakeSession:
    session = _FakeSession(pages, failing_urls=failing_urls)
    monkeypatch.setattr(bbt, "_session", lambda: session)
    return session


def _wide_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """fixture 首页稿件最早到 2026-08-26，把窗口放大到全部保留。"""

    monkeypatch.setenv("BBTNEWS_LOOKBACK_DAYS", "30")


def test_make_article_id_uses_numeric_path_id() -> None:
    # URL 末段数字是全站唯一的 CMS 稿件 ID；http 链接、带 query 的链接
    # 都归一到同一个 id。
    for url in (
        "https://www.bbtnews.com.cn/2026/0920/606364.shtml",
        "http://www.bbtnews.com.cn/2026/0920/606364.shtml",
        "https://www.bbtnews.com.cn/2026/0920/606364.shtml?from=list",
        "//www.bbtnews.com.cn/2026/0920/606364.shtml",
        "/2026/0920/606364.shtml",
    ):
        assert bbt.make_article_id(url) == TARGET_ID


def test_make_article_id_rejects_non_article_urls() -> None:
    for url in (
        P1_URL,
        P2_URL,
        "https://www.bbtnews.com.cn/",
        "https://other.example.com/2026/0920/606364.shtml",
        "https://www.bbtnews.com.cn/2026/0920/606364.htm",
        "https://www.bbtnews.com.cn/2026/0920/606abc.shtml",
        # 13 月不是合法日期：路径里的年月日必须能还原成真实发布日期。
        "https://www.bbtnews.com.cn/2026/1301/606364.shtml",
        "",
    ):
        with pytest.raises(ValueError):
            bbt.make_article_id(url)


def test_normalize_url_forces_https_and_strips_query() -> None:
    # 站方列表页输出的文章链接是 http，全站 301 到 https；入库 URL
    # 统一成 https 免去详情抓取时多一跳重定向。
    assert (
        bbt.normalize_url("http://www.bbtnews.com.cn/2026/0920/606364.shtml?x=1#frag")
        == TARGET_URL
    )


def test_list_items_reads_only_left_column_zones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wide_window(monkeypatch)
    session = _serve(monkeypatch, {P1_URL: _fixture("list_jiaoyu_p1.htm")})

    items = bbt.list_items(today=TODAY)

    assert session.requested == [P1_URL]
    ids = [bbt.make_article_id(item.url) for item in items]
    assert len(ids) == len(set(ids)), "article ids must be unique"
    assert len(items) == 32
    # 主列表、头条区、热点推荐三个区都算频道内容。
    assert TARGET_ID in ids
    assert HEAD_ID in ids
    assert HOT_ID in ids
    for footer_id in FOOTER_IDS:
        assert footer_id not in ids


def test_list_items_walks_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    _wide_window(monkeypatch)
    session = _serve(
        monkeypatch,
        {P1_URL: _fixture("list_jiaoyu_p1.htm"), P2_URL: _fixture("list_jiaoyu_p2.htm")},
    )

    items = bbt.list_items(pages=2, today=TODAY)

    assert session.requested == [P1_URL, P2_URL]
    ids = [bbt.make_article_id(item.url) for item in items]
    assert PAGE2_FIRST_ID in ids
    assert TARGET_ID in ids
    # 第 1 页在前：页序即时间序，先抓到的稿件排在前面。
    assert ids.index(TARGET_ID) < ids.index(PAGE2_FIRST_ID)


def test_list_items_row_carries_row_date(monkeypatch: pytest.MonkeyPatch) -> None:
    _wide_window(monkeypatch)
    _serve(monkeypatch, {P1_URL: _fixture("list_jiaoyu_p1.htm")})

    items = bbt.list_items(today=TODAY)

    by_id = {bbt.make_article_id(item.url): item for item in items}
    target = by_id[TARGET_ID]
    assert target.title == "滑板车背诵、有道乐读等App因未公开个人信息收集使用规则等被通报"
    assert target.publish_time_iso == "2026-09-20T00:00:00+08:00"
    assert target.raw["list_date"] == "2026-09-20"


def test_default_window_drops_stale_rows_and_keeps_hot_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 默认 3 天窗口（cutoff=09-17）：主列表只剩 09-20 一条；热点推荐的
    # 09-18 行没有行内日期，靠 URL 日期压线保留；头条区 09-10 被丢弃。
    _serve(monkeypatch, {P1_URL: _fixture("list_jiaoyu_p1.htm")})

    items = bbt.list_items(today=TODAY)

    assert {bbt.make_article_id(item.url) for item in items} == {TARGET_ID, HOT_ID}


def test_invalid_lookback_env_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 环境变量填了非数字时按默认 3 天窗口执行，不能让抓取轮直接崩掉。
    _serve(monkeypatch, {P1_URL: _fixture("list_jiaoyu_p1.htm")})

    monkeypatch.setenv("BBTNEWS_LOOKBACK_DAYS", "abc")
    items = bbt.list_items(today=TODAY)

    assert {bbt.make_article_id(item.url) for item in items} == {TARGET_ID, HOT_ID}


def test_window_boundary_semantics_on_synthetic_rows() -> None:
    # 窗口语义是"早于 今天-N天 的丢弃"：边界那天（=cutoff）必须保留。
    boundary_href = "https://www.bbtnews.com.cn/2026/0917/606001.shtml"
    older_href = "https://www.bbtnews.com.cn/2026/0916/606002.shtml"
    rows = "".join(
        f'<li><div class="info"><h4><a href="{href}">标题{num}</a></h4>'
        f'<p class="others"><span>出处：北京商报</span><span>2026-09-{day}</span></p></div></li>'
        for num, day, href in (
            (1, "17", boundary_href),
            (2, "16", older_href),
        )
    )
    html = (
        '<html><body><div class="c-l-wrap">'
        f'<div class="news-feed"><ul class="clearfix">{rows}</ul></div>'
        "</div></body></html>"
    )

    items = bbt._parse_list_html(html, P1_URL, cutoff=TODAY.replace(day=17))

    assert [bbt.make_article_id(item.url) for item in items] == ["bbtnews:606001"]
    assert items[0].raw["list_date"] == "2026-09-17"


def test_head_row_without_date_falls_back_to_url_date() -> None:
    # 头条区/热点推荐行不带行内日期，必须退回 URL 路径里的日期。
    html = (
        '<html><body><div class="c-l-wrap">'
        '<div class="news-head clearfix">'
        '<a class="fL" href="https://www.bbtnews.com.cn/2026/0918/606002.shtml" target="_blank">'
        '<img src="https://upload.bbtnews.com.cn/a.png" alt="头条图题"/></a>'
        '<div class="fR info"><p class="tit">'
        '<a href="https://www.bbtnews.com.cn/2026/0918/606002.shtml">头条标题</a></p></div>'
        "</div></div></body></html>"
    )

    items = bbt._parse_list_html(html, P1_URL, cutoff=TODAY.replace(day=17))

    assert len(items) == 1
    assert items[0].title == "头条标题"
    assert items[0].publish_time_iso == "2026-09-18T00:00:00+08:00"
    assert items[0].raw["list_date"] == "2026-09-18"


def test_list_items_skips_existing_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    _wide_window(monkeypatch)
    _serve(monkeypatch, {P1_URL: _fixture("list_jiaoyu_p1.htm")})

    items = bbt.list_items(
        today=TODAY,
        existing_ids={TARGET_ID, HEAD_ID},
    )

    ids = {bbt.make_article_id(item.url) for item in items}
    assert len(items) == 30
    assert TARGET_ID not in ids
    assert HEAD_ID not in ids


def test_list_items_truncates_to_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _wide_window(monkeypatch)
    _serve(monkeypatch, {P1_URL: _fixture("list_jiaoyu_p1.htm")})

    everything = bbt.list_items(today=TODAY)
    assert len(everything) == 32

    assert bbt.list_items(limit=5, today=TODAY) == everything[:5]
    assert bbt.list_items(limit=99, today=TODAY) == everything
    assert bbt.list_items(limit=0, today=TODAY) == []


def test_list_items_keeps_earlier_pages_after_later_page_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wide_window(monkeypatch)
    _serve(
        monkeypatch,
        {P1_URL: _fixture("list_jiaoyu_p1.htm"), P2_URL: _fixture("list_jiaoyu_p2.htm")},
        failing_urls=frozenset({P2_URL}),
    )

    items = bbt.list_items(pages=2, today=TODAY)

    assert len(items) == 32
    assert TARGET_ID in {bbt.make_article_id(item.url) for item in items}


def test_list_items_raises_when_all_pages_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, {}, failing_urls=frozenset({P1_URL}))

    with pytest.raises(RuntimeError, match="bbtnews list pages failed"):
        bbt.list_items(today=TODAY)


def test_page_url_derives_numbered_pages_from_column_url() -> None:
    assert bbt._page_url(P1_URL, 1) == P1_URL
    assert bbt._page_url(P1_URL, 2) == P2_URL
    assert bbt._page_url(P1_URL, 10) == "https://www.bbtnews.com.cn/chuizhipd/chanjingzx/jiaoyupd/10.shtml"


def test_fetch_detail_target_article_extracts_clean_content() -> None:
    html = _fixture("article_606364.html")

    data = bbt._parse_detail_html(html, TARGET_URL)

    expected_title = "滑板车背诵、有道乐读等App因未公开个人信息收集使用规则等被通报"
    assert data["title"] == expected_title
    assert data["source"] == "北京商报"
    assert data["url"] == TARGET_URL
    assert data["publish_time_iso"] == "2026-09-20T00:00:00+08:00"

    markdown = data["content_markdown"]
    assert "北京商报讯（记者 吴其芸）" in markdown
    assert "15 个工作日内完成整改" not in markdown  # 原文是"15个工作日"，防宽松匹配
    assert "15个工作日内完成整改" in markdown
    assert "红卷乐读、道臣智运、有道乐读、啃书兽等11款App" in markdown
    # 正文容器之外的页头/右侧栏/页脚区不进正文。
    assert markdown.count(expected_title) == 0
    for junk in ("右侧广告", "深蓝智库", "热点推荐", "ICP备案"):
        assert junk not in markdown


def test_fetch_detail_raises_without_content_container() -> None:
    with pytest.raises(RuntimeError, match="content"):
        bbt._parse_detail_html("<html><body><p>nothing</p></body></html>", TARGET_URL)


def test_detail_publish_time_falls_back_to_url_date() -> None:
    # 元信息区缺失日期时退回 URL 路径里的发布日期。
    html = (
        "<html><body>"
        '<div class="article-hd"><h3>无元信息标题</h3></div>'
        '<div class="article-bd"><p>正文一段</p></div>'
        "</body></html>"
    )

    data = bbt._parse_detail_html(html, TARGET_URL)

    assert data["title"] == "无元信息标题"
    assert data["publish_time_iso"] == "2026-09-20T00:00:00+08:00"


def test_rows_carry_fixed_bbtnews_source() -> None:
    fetched_at = datetime(2026, 9, 20, tzinfo=timezone.utc)
    item = bbt.FeedItemLike(
        title="滑板车背诵、有道乐读等App因未公开个人信息收集使用规则等被通报",
        url=TARGET_URL,
        section=None,
        publish_time_iso="2026-09-20T00:00:00+08:00",
        raw={},
    )

    feed_row = bbt.feed_item_to_row(item, TARGET_ID, fetched_at=fetched_at)
    assert feed_row["source"] == "北京商报"
    assert feed_row["publish_time_iso"].isoformat() == "2026-09-20T00:00:00+08:00"

    detail_row = bbt.build_detail_update(
        item,
        TARGET_ID,
        {"title": "滑板车背诵、有道乐读等App因未公开个人信息收集使用规则等被通报"},
        detail_fetched_at=fetched_at,
    )
    assert detail_row["source"] == "北京商报"
    assert detail_row["publish_time"] == int(
        datetime(2026, 9, 19, 16, 0, tzinfo=timezone.utc).timestamp()
    )
