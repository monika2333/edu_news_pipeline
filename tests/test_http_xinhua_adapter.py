from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from src.adapters import http_xinhua as hx

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "xinhua"

# fixture 抓取于 2026-09-18；列表日期分布以此为基准做确定性断言。
TODAY = date(2026, 9, 18)

TARGET_ID = "xinhua:b83a1ece15b24586a7e905c9d4ebd93c"
TARGET_URL = "http://bj.news.cn/20260917/b83a1ece15b24586a7e905c9d4ebd93c/c.html"
SHUANGSHI_ID = "xinhua:9e5711c63cf94f48b2c2e24cfadc2524"  # 图说与聚焦重复收录
OLD_JJ_ID = "xinhua:60edb93e1db94cb5bc24c491ae0804f5"  # 聚焦列表里 2026-09-13 的旧稿

COLUMN_URLS = {name: url for name, url in hx.COLUMNS}
JJ_URL = COLUMN_URLS["聚焦"]
TP_URL = COLUMN_URLS["图说"]
KJ_URL = COLUMN_URLS["科教"]


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
    monkeypatch.setattr(hx, "_session", lambda: session)
    return session


def _list_page_fixture(alias: str) -> str:
    return _fixture(f"{alias}_index.htm")


def test_make_article_id_is_stable_across_hosts_and_subdomains() -> None:
    slug = "b83a1ece15b24586a7e905c9d4ebd93c"
    for url in (
        f"http://bj.news.cn/20260917/{slug}/c.html",
        f"https://www.news.cn/20260917/{slug}/c.html",
        f"https://news.cn/20260917/{slug}/c.html",
        f"//bj.news.cn/20260917/{slug}/c.html",
        f"../20260917/{slug}/c.html",
    ):
        assert hx.make_article_id(url) == f"xinhua:{slug}"


def test_make_article_id_rejects_non_article_urls() -> None:
    for url in (
        "http://www.bj.xinhuanet.com/20260125/afb2417d270b4df98e3f26b8bc1f1437/c.html",
        "http://bj.news.cn/jj/index.htm",
        "http://bj.news.cn/20260917/notahexslug/c.html",
        "",
    ):
        with pytest.raises(ValueError):
            hx.make_article_id(url)


def test_list_items_merges_same_article_across_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _serve(
        monkeypatch,
        {
            JJ_URL: _list_page_fixture("jj"),
            TP_URL: _list_page_fixture("tp"),
            KJ_URL: _list_page_fixture("kj"),
        },
    )

    items = hx.list_items(today=TODAY)

    # 每个栏目只请求 index 首屏，共 15 个文章栏目（"专题"非文章列表，不在内）。
    assert session.requested == [url for _, url in hx.COLUMNS]
    ids = [hx.make_article_id(item.url) for item in items]
    assert len(ids) == len(set(ids)), "article ids must be unique after column merge"
    assert len(items) == 13
    assert ids.count(SHUANGSHI_ID) == 1
    assert TARGET_ID in ids  # 目标文章只出现在"聚焦"，不在"科教"


def test_list_items_applies_lookback_window(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, {JJ_URL: _list_page_fixture("jj")})

    items = hx.list_items(today=TODAY)

    assert len(items) == 10
    ids = {hx.make_article_id(item.url) for item in items}
    assert OLD_JJ_ID not in ids  # 2026-09-13，早于 今天-3天
    assert TARGET_ID in ids
    for item in items:
        list_date = date.fromisoformat(item.raw["list_date"])
        assert list_date >= TODAY - timedelta(days=3)

    # 放宽窗口后旧稿回到结果里。
    monkeypatch.setenv("XINHUA_LOOKBACK_DAYS", "10")
    widened = hx.list_items(today=TODAY)
    assert len(widened) == 20
    assert OLD_JJ_ID in {hx.make_article_id(item.url) for item in widened}


def test_list_items_skips_existing_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, {JJ_URL: _list_page_fixture("jj")})

    items = hx.list_items(
        today=TODAY,
        existing_ids={TARGET_ID, SHUANGSHI_ID},
    )

    ids = {hx.make_article_id(item.url) for item in items}
    assert len(items) == 8
    assert TARGET_ID not in ids
    assert SHUANGSHI_ID not in ids


def _synthetic_list_page(rows: list[tuple[str, str, str]]) -> str:
    """rows: (标题, 列表显示日期, href)。"""

    lis = "".join(
        f'<li><h2><a href="{href}">{title}</a></h2><span>{displayed}</span></li>'
        for title, displayed, href in rows
    )
    return f'<html><body><ul class="wz-list">{lis}</ul></body></html>'


def _slug(seed: str) -> str:
    # seed 只能含十六进制字符，否则整条链接不会被识别为文章 URL。
    return (seed + "0123456789abcdef" * 2)[:32]


def test_window_keeps_item_dated_exactly_on_boundary() -> None:
    # 窗口语义是"早于 今天-N天 的丢弃"：边界那天（=cutoff）必须保留。
    boundary_href = f"http://bj.news.cn/20260916/{_slug('beef')}/c.html"
    older_href = f"http://bj.news.cn/20260914/{_slug('fade')}/c.html"
    html = _synthetic_list_page(
        [
            ("边界日稿件", "2026-09-15", boundary_href),  # today-3，恰好压线
            ("超窗一天稿件", "2026-09-14", older_href),
        ]
    )

    items, dropped = hx._parse_list_html(
        html,
        JJ_URL,
        cutoff=TODAY - timedelta(days=3),
    )

    assert [item.title for item in items] == ["边界日稿件"]
    assert items[0].raw["list_date"] == "2026-09-15"
    assert dropped == 1


def test_list_date_wins_over_url_date() -> None:
    # 真实页面存在"URL 日期 0916、列表显示 09-17"的错位稿（入秋、中关村两篇），
    # 解析必须以列表显示日期为准。
    html = _list_page_fixture("jj")
    items, _ = hx._parse_list_html(html, JJ_URL, cutoff=date(2026, 9, 15))

    by_id = {hx.make_article_id(item.url): item for item in items}
    item = by_id["xinhua:8620cce5cb2f488c92346c920a7289c9"]  # 北京2026年入秋…
    assert item.raw["list_date"] == "2026-09-17"  # 不是 URL 里的 2026-09-16
    assert item.publish_time_iso == "2026-09-17T00:00:00+08:00"

    # 错位大到跨越窗口边界时，优先顺序决定稿件的存留：
    # 列表显示 09-16（窗口内）但 URL 日期 0912（窗口外）——按列表日期应保留。
    straddle_href = f"http://bj.news.cn/20260912/{_slug('cafe')}/c.html"
    straddle_html = _synthetic_list_page(
        [("跨窗错位稿件", "2026-09-16", straddle_href)],
    )
    straddle_items, _ = hx._parse_list_html(
        straddle_html,
        JJ_URL,
        cutoff=date(2026, 9, 15),
    )
    assert [item.title for item in straddle_items] == ["跨窗错位稿件"]
    assert straddle_items[0].raw["list_date"] == "2026-09-16"


def test_list_items_truncates_to_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, {JJ_URL: _list_page_fixture("jj")})

    everything = hx.list_items(today=TODAY)
    assert len(everything) == 10

    assert hx.list_items(limit=5, today=TODAY) == everything[:5]
    assert hx.list_items(limit=99, today=TODAY) == everything
    assert hx.list_items(limit=0, today=TODAY) == []


def test_list_items_keeps_other_columns_after_single_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(
        monkeypatch,
        {
            JJ_URL: _list_page_fixture("jj"),
            TP_URL: _list_page_fixture("tp"),
            KJ_URL: _list_page_fixture("kj"),
        },
        failing_urls=frozenset({TP_URL}),
    )

    items = hx.list_items(today=TODAY)

    ids = {hx.make_article_id(item.url) for item in items}
    assert len(items) == 10  # 只剩"聚焦"首屏窗口内的 10 条
    assert TARGET_ID in ids


def test_list_items_raises_when_all_columns_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, {}, failing_urls=frozenset(COLUMN_URLS.values()))

    with pytest.raises(RuntimeError, match="columns failed"):
        hx.list_items(today=TODAY)


def test_fetch_detail_target_article_extracts_clean_content() -> None:
    html = _fixture("article_b83a1ece.html")

    data = hx._parse_detail_html(html, TARGET_URL)

    expected_title = "北京市教委举办国家网络安全宣传周网络安全教育研讨会"
    assert data["title"] == expected_title
    assert data["source"] == "新华网"
    assert data["url"] == TARGET_URL
    assert data["publish_time_iso"] == "2026-09-17T09:06:40+08:00"

    markdown = data["content_markdown"]
    assert "国家网络安全宣传周网络安全教育研讨会" in markdown
    # 页面里 PC/移动两套标题区都不进正文：标题在正文中一次也不出现。
    assert markdown.count(expected_title) == 0
    for junk in ("责任编辑", "分享到", "纠错", "二维码", "字体："):
        assert junk not in markdown
    # 图片保留且补全为绝对地址；正文图片相对 src 解析到 <id>/ 目录下（已对真实站点验证 200）。
    assert (
        "http://bj.news.cn/20260917/b83a1ece15b24586a7e905c9d4ebd93c/"
        "20260917b83a1ece15b24586a7e905c9d4ebd93c_202609176d421fa881f74b01a46ea2284f2a3963.jpg"
    ) in data["content"]
    assert "9月16日拍摄的研讨会现场。" in markdown


def test_fetch_detail_pic_column_article_keeps_images_and_captions() -> None:
    html = _fixture("article_9e5711c6.html")
    url = "http://bj.news.cn/20260917/9e5711c63cf94f48b2c2e24cfadc2524/c.html"

    data = hx._parse_detail_html(html, url)

    assert data["title"] == "2026“我与地坛”北京书市开幕"
    assert data["publish_time_iso"] == "2026-09-17T17:39:12+08:00"
    markdown = data["content_markdown"]
    assert markdown.count("![") >= 8
    assert "读者在地坛书市上挑选书籍。" in markdown
    assert "http://bj.news.cn/20260917/9e5711c63cf94f48b2c2e24cfadc2524/202609179e5711c63cf94f48b2c2e24cfadc2524_" in markdown
    for junk in ("责任编辑", "分享到", "纠错"):
        assert junk not in markdown


def test_fetch_detail_video_column_article() -> None:
    html = _fixture("article_01ee3b60.html")
    url = "http://bj.news.cn/20260917/01ee3b6094b14a0796119a2e9643a1a6/c.html"

    data = hx._parse_detail_html(html, url)

    assert data["title"] == "校馆弦歌｜北京大学图书馆 传承红色基因 以文化人"
    assert data["publish_time_iso"] == "2026-09-17T09:04:27+08:00"
    assert "北京大学图书馆" in data["content_markdown"]


def test_fetch_detail_raises_without_content_container() -> None:
    with pytest.raises(RuntimeError, match="content"):
        hx._parse_detail_html("<html><body><p>nothing</p></body></html>", TARGET_URL)


def test_rows_carry_fixed_xinhua_source() -> None:
    fetched_at = datetime(2026, 9, 18, tzinfo=timezone.utc)
    item = hx.FeedItemLike(
        title="北京市教委举办国家网络安全宣传周网络安全教育研讨会",
        url=TARGET_URL,
        section=None,
        publish_time_iso="2026-09-17T00:00:00+08:00",
        raw={},
    )

    feed_row = hx.feed_item_to_row(item, TARGET_ID, fetched_at=fetched_at)
    assert feed_row["source"] == "新华网"
    # 列表只有日期，发布时间取当天北京时间零点。
    assert feed_row["publish_time_iso"].isoformat() == "2026-09-17T00:00:00+08:00"

    detail_row = hx.build_detail_update(
        item,
        TARGET_ID,
        {"title": "北京市教委举办网络安全教育研讨会", "source": "北京日报"},
        detail_fetched_at=fetched_at,
    )
    assert detail_row["source"] == "新华网"
