from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from src.adapters import http_stdaily as sd

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "stdaily"

# fixture 抓取于 2026-09-19；列表日期分布以此为基准做确定性断言。
TODAY = date(2026, 9, 19)

P1_URL = "https://www.stdaily.com/web/gdxw/node_324.html"
P2_URL = "https://www.stdaily.com/web/gdxw/node_324_2.html"
TARGET_ID = "stdaily:584227"
TARGET_URL = "https://www.stdaily.com/web/gdxw/2026-09/19/content_584227.html"
PAGE2_FIRST_ID = "stdaily:584197"  # 第二十届亚洲运动会开幕式举行…
# 首页侧栏"热门点击"里的旧稿（非滚动列表行），解析必须排除。
SIDEBAR_IDS = ("stdaily:1790739", "stdaily:582730", "stdaily:214881")


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
    monkeypatch.setattr(sd, "_session", lambda: session)
    return session


def test_make_article_id_uses_site_wide_content_number() -> None:
    # content 编号全站唯一，同一稿件在别的频道下路径不同、编号相同；
    # article_id 只取编号，跨频道天然去重。
    slug = "584227"
    for url in (
        "https://www.stdaily.com/web/gdxw/2026-09/19/content_584227.html",
        "http://stdaily.com/web/dfxw/2026-09/18/content_584227.html",
        "//www.stdaily.com/web/gdxw/2026-09/19/content_584227.html",
        "/web/gdxw/2026-09/19/content_584227.html",
    ):
        assert sd.make_article_id(url) == f"stdaily:{slug}"


def test_make_article_id_rejects_non_article_urls() -> None:
    for url in (
        "https://www.stdaily.com/web/gdxw/node_324.html",
        "https://www.stdaily.com/web/gdxw/node_324_2.html",
        "https://other.example.com/web/gdxw/2026-09/19/content_584227.html",
        "https://www.stdaily.com/web/gdxw/2026-09/19/content_abc.html",
        "https://www.stdaily.com/web/gdxw/2026-9/9/content_584227.html",
        "",
    ):
        with pytest.raises(ValueError):
            sd.make_article_id(url)


def test_list_items_reads_only_list_container(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _serve(monkeypatch, {P1_URL: _fixture("list_gdxw_p1.htm")})

    items = sd.list_items(today=TODAY)

    assert session.requested == [P1_URL]
    ids = [sd.make_article_id(item.url) for item in items]
    assert len(ids) == len(set(ids)), "article ids must be unique"
    assert len(items) == 16
    assert TARGET_ID in ids
    for sidebar_id in SIDEBAR_IDS:
        assert sidebar_id not in ids


def test_list_items_walks_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _serve(
        monkeypatch,
        {P1_URL: _fixture("list_gdxw_p1.htm"), P2_URL: _fixture("list_gdxw_p2.htm")},
    )

    items = sd.list_items(pages=2, today=TODAY)

    assert session.requested == [P1_URL, P2_URL]
    ids = [sd.make_article_id(item.url) for item in items]
    assert len(items) == 32
    assert PAGE2_FIRST_ID in ids
    assert TARGET_ID in ids
    # 第 1 页在前：页序即时间序，先抓到的稿件排在前面。
    assert ids.index(TARGET_ID) < ids.index(PAGE2_FIRST_ID)


def test_list_items_row_carry_full_publish_time(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, {P1_URL: _fixture("list_gdxw_p1.htm")})

    items = sd.list_items(today=TODAY)

    by_id = {sd.make_article_id(item.url): item for item in items}
    target = by_id[TARGET_ID]
    assert target.title == "青岛：以时尚“织忆未来”"
    assert target.publish_time_iso == "2026-09-19T22:29:53+08:00"
    assert target.raw["list_date"] == "2026-09-19"
    for item in items:
        assert item.publish_time_iso is not None


def test_window_drops_rows_older_than_cutoff_and_keeps_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, {P1_URL: _fixture("list_gdxw_p1.htm")})

    # 默认 3 天窗口：fixture 里全部是 2026-09-19 的稿，全部保留。
    items = sd.list_items(today=TODAY)
    assert len(items) == 16

    # 窗口收到 0 天：早于今天（09-18 及更早）的行被丢弃，
    # 恰好等于今天的行压线保留。
    monkeypatch.setenv("STDAILY_LOOKBACK_DAYS", "0")
    narrowed = sd.list_items(today=TODAY)
    assert len(narrowed) == 16


def test_window_boundary_semantics_on_synthetic_rows() -> None:
    # 窗口语义是"早于 今天-N天 的丢弃"：边界那天（=cutoff）必须保留。
    boundary_href = "https://www.stdaily.com/web/gdxw/2026-09/16/content_584001.html"
    older_href = "https://www.stdaily.com/web/gdxw/2026-09/15/content_584002.html"
    dls = "".join(
        f"<dl><h3><a href=\"{href}\">标题{num}</a></h3>"
        f"<div class=\"sourthTime\"><span>科技日报</span>"
        f"<span>2026-09-{day} 10:0{num}:00</span></div></dl>"
        for num, day, href in (
            (1, "16", boundary_href),
            (2, "15", older_href),
        )
    )
    html = f'<html><body><div class="f_lieb_list">{dls}</div></body></html>'

    items = sd._parse_list_html(html, P1_URL, cutoff=TODAY.replace(day=16))

    assert [sd.make_article_id(item.url) for item in items] == ["stdaily:584001"]
    assert items[0].raw["list_date"] == "2026-09-16"


def test_row_without_list_time_falls_back_to_url_date() -> None:
    href = "https://www.stdaily.com/web/gdxw/2026-09/17/content_584003.html"
    html = (
        '<html><body><div class="f_lieb_list">'
        f'<dl><h3><a href="{href}">无时间行</a></h3></dl>'
        "</div></body></html>"
    )

    items = sd._parse_list_html(html, P1_URL, cutoff=TODAY.replace(day=16))

    assert len(items) == 1
    assert items[0].publish_time_iso == "2026-09-17T00:00:00+08:00"
    assert items[0].raw["list_time"] is None
    assert items[0].raw["list_date"] == "2026-09-17"


def test_list_items_skips_existing_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, {P1_URL: _fixture("list_gdxw_p1.htm")})

    items = sd.list_items(
        today=TODAY,
        existing_ids={TARGET_ID, PAGE2_FIRST_ID},
    )

    ids = {sd.make_article_id(item.url) for item in items}
    assert len(items) == 15
    assert TARGET_ID not in ids
    assert PAGE2_FIRST_ID not in ids


def test_list_items_truncates_to_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, {P1_URL: _fixture("list_gdxw_p1.htm")})

    everything = sd.list_items(today=TODAY)
    assert len(everything) == 16

    assert sd.list_items(limit=5, today=TODAY) == everything[:5]
    assert sd.list_items(limit=99, today=TODAY) == everything
    assert sd.list_items(limit=0, today=TODAY) == []


def test_list_items_keeps_earlier_pages_after_later_page_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(
        monkeypatch,
        {P1_URL: _fixture("list_gdxw_p1.htm"), P2_URL: _fixture("list_gdxw_p2.htm")},
        failing_urls=frozenset({P2_URL}),
    )

    items = sd.list_items(pages=2, today=TODAY)

    assert len(items) == 16
    assert TARGET_ID in {sd.make_article_id(item.url) for item in items}


def test_list_items_raises_when_all_pages_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, {}, failing_urls=frozenset({P1_URL}))

    with pytest.raises(RuntimeError, match="stdaily list pages failed"):
        sd.list_items(today=TODAY)


def test_page_url_derives_numbered_pages_from_column_url() -> None:
    assert sd._page_url(P1_URL, 1) == P1_URL
    assert sd._page_url(P1_URL, 2) == P2_URL
    assert sd._page_url(P1_URL, 10) == "https://www.stdaily.com/web/gdxw/node_324_10.html"


def test_fetch_detail_target_article_extracts_clean_content() -> None:
    html = _fixture("article_584227.html")

    data = sd._parse_detail_html(html, TARGET_URL)

    expected_title = "青岛：以时尚“织忆未来”"
    assert data["title"] == expected_title
    assert data["source"] == "科技日报"
    assert data["url"] == TARGET_URL
    assert data["publish_time_iso"] == "2026-09-19T22:29:52+08:00"

    markdown = data["content_markdown"]
    assert "科技日报记者 宋迎迎" in markdown
    assert "2026青岛时装周分会场暨第十届谷里时尚买手节" in markdown
    # 正文容器之外的页头/页脚区不进正文。
    assert markdown.count(expected_title) == 0
    for junk in ("责任编辑", "来源:", "点击数", "网友评论", "相关稿件"):
        assert junk not in markdown
    # 图片保留且补全为绝对地址。
    assert (
        "https://www.stdaily.com/web/gdxw/pic/2026-09/19/"
        "584227_980f5153-b0f9-4a66-a5be-f42cf197ff83copy.jpg"
    ) in data["content"]
    # 流水线纯文字：图片（含 topic 属性图说）整体剥离，不再转 markdown
    assert "![微信图片_20260919170210_9_911](" not in markdown
    assert "![" not in markdown


def test_fetch_detail_raises_without_content_container() -> None:
    with pytest.raises(RuntimeError, match="content"):
        sd._parse_detail_html("<html><body><p>nothing</p></body></html>", TARGET_URL)


def test_rows_carry_fixed_stdaily_source() -> None:
    fetched_at = datetime(2026, 9, 19, tzinfo=timezone.utc)
    item = sd.FeedItemLike(
        title="青岛：以时尚“织忆未来”",
        url=TARGET_URL,
        section=None,
        publish_time_iso="2026-09-19T22:29:53+08:00",
        raw={},
    )

    feed_row = sd.feed_item_to_row(item, TARGET_ID, fetched_at=fetched_at)
    assert feed_row["source"] == "科技日报"
    assert feed_row["publish_time_iso"].isoformat() == "2026-09-19T22:29:53+08:00"

    detail_row = sd.build_detail_update(
        item,
        TARGET_ID,
        {"title": "青岛：以时尚“织忆未来”", "source": "中国科技网"},
        detail_fetched_at=fetched_at,
    )
    assert detail_row["source"] == "科技日报"
