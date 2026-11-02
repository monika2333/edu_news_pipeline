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


def test_fetch_detail_pic_column_article_strips_images_keeps_captions() -> None:
    html = _fixture("article_9e5711c6.html")
    url = "http://bj.news.cn/20260917/9e5711c63cf94f48b2c2e24cfadc2524/c.html"

    data = hx._parse_detail_html(html, url)

    assert data["title"] == "2026“我与地坛”北京书市开幕"
    assert data["publish_time_iso"] == "2026-09-17T17:39:12+08:00"
    markdown = data["content_markdown"]
    # 流水线纯文字：图片整体剥离，但图说文字保留
    assert markdown.count("![") == 0
    assert "读者在地坛书市上挑选书籍。" in markdown
    assert "http://bj.news.cn/20260917/9e5711c63cf94f48b2c2e24cfadc2524/202609179e5711c63cf94f48b2c2e24cfadc2524_" not in markdown
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


# ---------------------------------------------------------------------------
# xinhua_edu（新华网教育频道）
# ---------------------------------------------------------------------------

EDU_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "xinhua_edu"

# fixture 抓取于 2026-11-02；窗口断言以此为基准做确定性断言。
EDU_TODAY = date(2026, 11, 2)

EDU_CHANNEL_URLS = {name: url for name, url in hx.EDUCATION_CHANNELS}
MRYW_URL = EDU_CHANNEL_URLS["每日要闻"]
JCJY_URL = EDU_CHANNEL_URLS["基础教育"]
GDJY_URL = EDU_CHANNEL_URLS["高等教育"]
EDUZYJY_URL = EDU_CHANNEL_URLS["职业教育"]

_EDU_FIXTURE_ALIAS = {
    "每日要闻": "mryw",
    "基础教育": "jcjy",
    "高等教育": "gdjy",
    "职业教育": "eduzyjy",
}

# 夹具中的关键稿件（slug 均来自真实页面；合成的纯文字条目 seed 只含十六进制字符）
MRYW_TOP_ID = "xinhua:dbc6acf913ef43abb14aab3f8230804e"  # 10-31 年会稿
MRYW_BOUNDARY_ID = "xinhua:c60e1513ab77425a98e7227e4716f10e"  # URL 10-30，今天-3 恰好压线
MRYW_OUT_ID = "xinhua:7e1ba45e54d441a3886a17b2605fad8e"  # URL 10-29，超窗一天
LEGAL_ID = "xinhua:114719983f344db4b92834f24190bb24"  # www.news.cn/legal 前缀
LEGAL_URL = "https://www.news.cn/legal/20261020/114719983f344db4b92834f24190bb24/c.html"
JCJY_TOP_ID = "xinhua:31ac66cc535b42afaa2c74ab9ce446ac"  # 10-30 贵州六盘水（夹具中在侧栏）
PINNED_ID = "xinhua:3072f9a7a6534ed6bb870749246dbec6"  # eduzyjy 置顶条目（夹具中无 .time）
TEXT_ONLY_ID = "xinhua:deadbeefdeadbeefdeadbeefdeadbeef"  # 合成的纯文字条目（URL 10-30）
MRYW_MISALIGNED_ID = "xinhua:46a769c6d5e645a98a63d219f53432d0"  # URL 10-25 / .time 10-26
JCJY_MISALIGNED_ID = "xinhua:42df625125134807807214c612518641"  # URL 10-16 / .time 10-17


@pytest.fixture(autouse=True)
def _default_lookback_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """窗口变量不继承外部环境，保证夹具断言的确定性。"""

    monkeypatch.delenv("XINHUA_LOOKBACK_DAYS", raising=False)
    monkeypatch.delenv("XINHUA_EDU_LOOKBACK_DAYS", raising=False)


def _edu_fixture(name: str) -> str:
    return (EDU_FIXTURE_DIR / name).read_text(encoding="utf-8")


def _serve_edu_fixtures(
    monkeypatch: pytest.MonkeyPatch,
    *,
    failing_urls: frozenset[str] = frozenset(),
    overrides: dict[str, str] | None = None,
) -> _FakeSession:
    pages = {
        url: _edu_fixture(f"{_EDU_FIXTURE_ALIAS[name]}_index.htm")
        for name, url in hx.EDUCATION_CHANNELS
    }
    if overrides:
        pages.update(overrides)
    return _serve(monkeypatch, pages, failing_urls=failing_urls)


def test_prefixed_article_path_recognized_and_windowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # T4：带栏目名的文章地址产出稳定 ID，日期从 URL 取到。
    assert hx.make_article_id(LEGAL_URL) == LEGAL_ID
    assert hx._url_date(LEGAL_URL) == date(2026, 10, 20)

    _serve_edu_fixtures(monkeypatch)

    # 默认窗口（今天-3=10-30）里没有它：URL 日期 10-20，被窗口过滤。
    default_ids = {
        hx.make_article_id(item.url) for item in hx.list_education_items(today=EDU_TODAY)
    }
    assert LEGAL_ID not in default_ids

    # 放宽窗口后回到结果，说明它一直被识别、只是被窗口拦住。
    monkeypatch.setenv("XINHUA_EDU_LOOKBACK_DAYS", "16")
    widened_ids = {
        hx.make_article_id(item.url) for item in hx.list_education_items(today=EDU_TODAY)
    }
    assert LEGAL_ID in widened_ids


def test_prefixed_article_path_still_rejects_non_articles() -> None:
    # T4：路径规则放宽后，原有拒绝口径一个都不能松。
    for url in (
        "http://www.bj.xinhuanet.com/legal/20260125/afb2417d270b4df98e3f26b8bc1f1437/c.html",
        "https://www.news.cn/legal/20261020/notahexslug/c.html",
        "https://www.news.cn/legal/20261020/114719983f344db4b92834f24190bb24/index.htm",
        "https://www.news.cn/LEGAL/20261020/114719983f344db4b92834f24190bb24/c.html",
        "https://www.news.cn/legal/index.htm",
        "",
    ):
        with pytest.raises(ValueError):
            hx.make_article_id(url)


def test_education_list_requests_exactly_four_channel_front_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # T1：每轮恰好请求 4 个频道首页，顺序与常量一致；不翻页。
    session = _serve_edu_fixtures(monkeypatch)

    items = hx.list_education_items(today=EDU_TODAY, pages=3)

    assert session.requested == [url for _, url in hx.EDUCATION_CHANNELS]
    assert len(items) == 9


def test_education_parser_scopes_to_content_list_container() -> None:
    # T2：#content-list 外的窗口内文章（侧栏"每日要闻"热榜）不进结果。
    html = _edu_fixture("mryw_index.htm")
    items, _dropped, _skipped = hx._parse_education_list_html(
        html,
        MRYW_URL,
        cutoff=EDU_TODAY - timedelta(days=3),
    )
    urls = {item.url for item in items}

    assert f"https://education.news.cn/20261030/{JCJY_TOP_ID.split(':')[1]}/c.html" not in urls
    assert any(MRYW_TOP_ID.split(":")[1] in url for url in urls)


def test_education_parser_keeps_image_first_and_text_only_entries() -> None:
    # T3：图文条目第一个链接是图片（无文字），标题取 .tit；纯文字条目同样有标题。
    html = _edu_fixture("mryw_index.htm")
    items, _dropped, _skipped = hx._parse_education_list_html(
        html,
        MRYW_URL,
        cutoff=date(2026, 10, 1),
    )
    by_url = {item.url: item for item in items}

    img_first = by_url[
        "https://education.news.cn/20261031/dbc6acf913ef43abb14aab3f8230804e/c.html"
    ]
    assert img_first.title == "第27届中国国际教育年会在京举办"

    text_only = by_url[
        "https://education.news.cn/20261030/deadbeefdeadbeefdeadbeefdeadbeef/c.html"
    ]
    assert text_only.title == "教育部部署2027年面向中小学生的全国性竞赛活动名单"

    assert all(item.title for item in items)


def test_education_parser_counts_window_drops_and_items_without_article() -> None:
    # 计数口径：第三项是「找不到文章链接的 .item」数量，分享链接不计入，
    # 正常页面应为 0；置顶条目无 .time。
    expected = {
        "mryw": (MRYW_URL, 3, 13, 0),
        "jcjy": (JCJY_URL, 1, 14, 0),
        "gdjy": (GDJY_URL, 4, 11, 0),
        "eduzyjy": (EDUZYJY_URL, 1, 14, 0),
    }
    for alias, (url, kept, dropped, without_article) in expected.items():
        items, window_dropped, items_without_article = hx._parse_education_list_html(
            _edu_fixture(f"{alias}_index.htm"),
            url,
            cutoff=EDU_TODAY - timedelta(days=3),
        )
        assert (len(items), window_dropped, items_without_article) == (
            kept,
            dropped,
            without_article,
        ), f"{alias} counts drifted"


def test_education_parser_raises_without_container() -> None:
    with pytest.raises(ValueError, match="content-list"):
        hx._parse_education_list_html(
            "<html><body><p>页面改版了</p></body></html>",
            MRYW_URL,
            cutoff=EDU_TODAY - timedelta(days=3),
        )


# 容器在但一条文章都解析不出的两种形态：没有 .item；.item 在但都没有文章链接。
DEAD_CHANNEL_PAGES = (
    '<html><body><div id="content-list"></div></body></html>',
    '<html><body><div id="content-list">'
    '<div class="item"><span>无链接条目</span></div>'
    '<div class="item"><a href="//service.weibo.com/share/share.php?url=x">分享</a></div>'
    "</div></body></html>",
)


def test_education_parser_raises_when_no_article_entries_parse() -> None:
    # 容器缺失之外的第二类频道失败：窗口过滤前一条文章都解析不出。
    for html in DEAD_CHANNEL_PAGES:
        with pytest.raises(ValueError, match="article entries"):
            hx._parse_education_list_html(
                html,
                MRYW_URL,
                cutoff=EDU_TODAY - timedelta(days=3),
            )


def test_education_parser_counts_items_without_article_links() -> None:
    # 计数口径：分享链接不计入；只有找不到文章链接的 .item 才计数。
    html = (
        '<html><body><div id="content-list">'
        '<div class="item"><div class="txt"><div class="tit">'
        '<a href="https://education.news.cn/20261030/dbc6acf913ef43abb14aab3f8230804e/c.html">'
        "正常条目</a></div>"
        '<div class="info"><div class="share">'
        '<a href="//service.weibo.com/share/share.php?url=x">分享</a></div></div></div></div>'
        '<div class="item"><a href="//service.weibo.com/share/share.php?url=y">只有分享</a></div>'
        '<div class="item"><span>纯文字无链接</span></div>'
        "</div></body></html>"
    )

    items, _dropped, items_without_article = hx._parse_education_list_html(
        html,
        MRYW_URL,
        cutoff=EDU_TODAY - timedelta(days=3),
    )

    assert [item.title for item in items] == ["正常条目"]
    assert items_without_article == 2


def test_education_date_prefers_url_date_over_list_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # T5：URL 与 .time 错位时取 URL 日期；错位跨窗口边界时决定存留。
    _serve_edu_fixtures(monkeypatch)
    monkeypatch.setenv("XINHUA_EDU_LOOKBACK_DAYS", "16")

    items = hx.list_education_items(today=EDU_TODAY)
    by_id = {hx.make_article_id(item.url): item for item in items}

    item = by_id[MRYW_MISALIGNED_ID]
    assert item.raw["list_date"] == "2026-10-25"  # 不是 .time 显示的 2026-10-26
    assert item.publish_time_iso == "2026-10-25T00:00:00+08:00"

    # URL 10-16 在窗口外、.time 10-17 恰好压线：按 URL 日期应丢弃。
    assert JCJY_MISALIGNED_ID not in by_id


def test_education_pinned_entry_without_time_uses_url_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # T5：置顶条目不显示日期，按 URL 日期走窗口过滤。
    _serve_edu_fixtures(monkeypatch)

    items = hx.list_education_items(today=EDU_TODAY)
    by_id = {hx.make_article_id(item.url): item for item in items}

    item = by_id[PINNED_ID]
    assert item.raw["list_date"] == "2026-10-30"
    assert item.publish_time_iso == "2026-10-30T00:00:00+08:00"


def test_education_window_boundary_and_env_independence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # T6：今天-3 保留、再早一天丢弃；XINHUA_LOOKBACK_DAYS 不影响教育频道。
    _serve_edu_fixtures(monkeypatch)

    items = hx.list_education_items(today=EDU_TODAY)
    ids = {hx.make_article_id(item.url) for item in items}
    assert MRYW_BOUNDARY_ID in ids  # URL 10-30 == 今天-3
    assert MRYW_OUT_ID not in ids  # URL 10-29，超窗一天

    monkeypatch.setenv("XINHUA_LOOKBACK_DAYS", "16")
    unchanged = hx.list_education_items(today=EDU_TODAY)
    assert {hx.make_article_id(item.url) for item in unchanged} == ids

    # 调大 XINHUA_EDU_LOOKBACK_DAYS 后旧稿回到结果。
    monkeypatch.setenv("XINHUA_EDU_LOOKBACK_DAYS", "16")
    widened = hx.list_education_items(today=EDU_TODAY)
    widened_ids = {hx.make_article_id(item.url) for item in widened}
    assert len(widened) == 46
    assert MRYW_OUT_ID in widened_ids
    assert LEGAL_ID in widened_ids


def test_education_merges_across_channels_skips_existing_and_truncates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # T7：跨频道按文章 ID 合并、先到先得；existing_ids 跳过；limit 截断。
    _serve_edu_fixtures(monkeypatch)

    items = hx.list_education_items(today=EDU_TODAY)
    ids = [hx.make_article_id(item.url) for item in items]
    assert len(ids) == 9
    assert len(set(ids)) == len(ids)

    skipped = hx.list_education_items(
        today=EDU_TODAY,
        existing_ids={MRYW_TOP_ID, JCJY_TOP_ID},
    )
    assert len(skipped) == 7
    skipped_ids = {hx.make_article_id(item.url) for item in skipped}
    assert MRYW_TOP_ID not in skipped_ids
    assert JCJY_TOP_ID not in skipped_ids

    assert hx.list_education_items(limit=4, today=EDU_TODAY) == items[:4]
    assert hx.list_education_items(limit=99, today=EDU_TODAY) == items
    assert hx.list_education_items(limit=0, today=EDU_TODAY) == []


def test_education_single_channel_http_failure_keeps_others(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # T8/T12：单频道 HTTP 失败不算整体失败，但必须有 [warn]。
    _serve_edu_fixtures(monkeypatch, failing_urls=frozenset({MRYW_URL}))

    items = hx.list_education_items(today=EDU_TODAY)

    # 只剩基础教育 1 + 高等教育 4 + 职业教育 1
    assert len(items) == 6
    stderr = capsys.readouterr().err
    assert "[warn]" in stderr
    assert "每日要闻" in stderr


def test_education_single_channel_missing_container_keeps_others(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # T8：容器缺失与 HTTP 失败同级——该频道算失败，其余照常。
    _serve_edu_fixtures(
        monkeypatch,
        overrides={MRYW_URL: "<html><body><p>改版了</p></body></html>"},
    )

    items = hx.list_education_items(today=EDU_TODAY)

    assert len(items) == 6
    stderr = capsys.readouterr().err
    assert "[warn]" in stderr
    assert "每日要闻" in stderr


@pytest.mark.parametrize(
    "dead_page",
    DEAD_CHANNEL_PAGES,
    ids=["empty-container", "items-without-article-links"],
)
def test_education_dead_channel_keeps_others(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    dead_page: str,
) -> None:
    # 窗口过滤前一条文章都解析不出（无 .item / .item 无文章链接）与容器缺失
    # 同级：该频道 [warn]，其余频道照常。
    _serve_edu_fixtures(monkeypatch, overrides={MRYW_URL: dead_page})

    items = hx.list_education_items(today=EDU_TODAY)

    assert len(items) == 6
    stderr = capsys.readouterr().err
    assert "[warn]" in stderr
    assert "每日要闻" in stderr


def test_education_all_http_failures_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_edu_fixtures(
        monkeypatch,
        failing_urls=frozenset(EDU_CHANNEL_URLS.values()),
    )

    with pytest.raises(RuntimeError, match="education channels failed"):
        hx.list_education_items(today=EDU_TODAY)


def test_education_all_missing_containers_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_edu_fixtures(
        monkeypatch,
        overrides={url: "<html></html>" for url in EDU_CHANNEL_URLS.values()},
    )

    with pytest.raises(RuntimeError, match="education channels failed"):
        hx.list_education_items(today=EDU_TODAY)


@pytest.mark.parametrize(
    "dead_page",
    DEAD_CHANNEL_PAGES,
    ids=["empty-container", "items-without-article-links"],
)
def test_education_all_dead_channels_raise(
    monkeypatch: pytest.MonkeyPatch,
    dead_page: str,
) -> None:
    # 全部频道都解析不出文章时同样 raise。
    _serve_edu_fixtures(
        monkeypatch,
        overrides={url: dead_page for url in EDU_CHANNEL_URLS.values()},
    )

    with pytest.raises(RuntimeError, match="education channels failed"):
        hx.list_education_items(today=EDU_TODAY)


def test_education_zero_in_window_is_not_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # T8：能解析出文章但窗口内 0 条是正常情况，不能抛错。
    _serve_edu_fixtures(monkeypatch)

    assert hx.list_education_items(today=date(2026, 11, 20)) == []


def test_education_detail_pages_parse_three_url_shapes() -> None:
    # T9：三类地址（education.news.cn / www.news.cn / 带 /legal/ 前缀）详情均可解析。
    cases = [
        (
            "article_c60e1513.html",
            "https://education.news.cn/20261030/c60e1513ab77425a98e7227e4716f10e/c.html",
            "多所高校内出现这种“小房子”，学生：太实用！",
            "2026-10-30T11:01:15+08:00",
            "在图书馆静心学习的你",
        ),
        (
            "article_2013feb5e.html",
            "https://www.news.cn/20261026/2013feb5e2754913b97dc8ed6f320604/c.html",
            "从增加一张课桌到拓宽一条成长道路——读懂广东新增百万高中学位背后的深意",
            "2026-10-26T10:12:03+08:00",
            "普通高中教育是基础教育的",
        ),
        (
            "article_114719983.html",
            LEGAL_URL,
            "警惕虚假招聘扰乱就业市场秩序",
            "2026-10-20T08:16:15+08:00",
            "当前正值求职招聘旺季",
        ),
    ]
    for fname, url, title, iso, snippet in cases:
        data = hx._parse_detail_html(_edu_fixture(fname), url)
        assert data["title"] == title, fname
        assert data["publish_time_iso"] == iso, fname
        assert data["source"] == "新华网", fname
        assert snippet in data["content_markdown"], fname
        # 流水线纯文字：正文不出现图片语法
        assert "![](" not in data["content_markdown"], fname


def test_education_rows_carry_fixed_source_and_detail_time_override() -> None:
    # T10：feed 行 source 固定"新华网"、时间为列表日期零点；详情行覆盖为精确时间。
    fetched_at = datetime(2026, 11, 2, tzinfo=timezone.utc)
    item = hx.FeedItemLike(
        title="多所高校内出现这种“小房子”，学生：太实用！",
        url="https://education.news.cn/20261030/c60e1513ab77425a98e7227e4716f10e/c.html",
        section=None,
        publish_time_iso="2026-10-30T00:00:00+08:00",
        raw={},
    )

    feed_row = hx.feed_item_to_row(item, MRYW_BOUNDARY_ID, fetched_at=fetched_at)
    assert feed_row["source"] == "新华网"
    assert feed_row["publish_time_iso"].isoformat() == "2026-10-30T00:00:00+08:00"

    detail_row = hx.build_detail_update(
        item,
        MRYW_BOUNDARY_ID,
        {"publish_time_iso": "2026-10-30T11:01:15+08:00"},
        detail_fetched_at=fetched_at,
    )
    assert detail_row["source"] == "新华网"
    assert detail_row["publish_time_iso"].isoformat() == "2026-10-30T11:01:15+08:00"
    assert detail_row["publish_time"] == int(feed_row["publish_time_iso"].timestamp()) + int(
        timedelta(hours=11, minutes=1, seconds=15).total_seconds()
    )


def test_education_stderr_summary_line(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    # T12：每轮 stderr 输出一行汇总，含各频道条数与各项计数。
    _serve_edu_fixtures(monkeypatch)

    hx.list_education_items(today=EDU_TODAY)

    stderr = capsys.readouterr().err
    assert (
        "[info] xinhua_edu channels: 每日要闻=3, 基础教育=1, 高等教育=4, 职业教育=1" in stderr
    )
    for token in (
        "unique=9",
        "new=9",
        "existing_skipped=0",
        "window_dropped=52",
        "items_without_article=0",
    ):
        assert token in stderr, token
