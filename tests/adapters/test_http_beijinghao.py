from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from src.adapters import http_beijinghao


FIRST_PAGE_HTML = """
<div class="left-liebiao">
  <div class="picTxt">
    <div class="txt-box">
      <div class="title">
        <a href="//peking.bjd.com.cn/content/s6a943dc1e4b03fa51a83960a.html"
           title="2000余名师生同台展演">2000余名师生同台展演</a>
      </div>
      <div class="other-box"><span class="data">2026-08-30</span></div>
    </div>
    <div class="pic-warp">
      <a href="//peking.bjd.com.cn/content/s6a943dc1e4b03fa51a83960a.html">图片</a>
    </div>
  </div>
</div>
"""

SECOND_PAGE_HTML = """
<div class="picTxt">
  <div class="title">
    <a href="/content/s6a8afad7e4b0e45f3fd66687.html">城市阅读空间覆盖全市</a>
  </div>
  <div class="other-box"><span class="data">2026-08-23</span></div>
</div>
"""

DETAIL_PAYLOAD = {
    "code": 0,
    "message": "success",
    "data": {
        "id": "s6a943dc1e4b03fa51a83960a",
        "originalId": "CO6a943af7d5de5a8cf8a79be2",
        "title": "2000余名师生同台展演",
        "publishTime": "2026-08-30T14:27:13.505+0000",
        "columnName": "现代教育报",
        "url": "https://peking.bjd.com.cn/content/s6a943dc1e4b03fa51a83960a.html",
        "content": """
          <p>这是正文第一段。</p>
          <figure><img src="//static.bjd.com.cn/image.jpg" alt="现场"></figure>
          <div class="share">分享</div>
          <div class="recommend">相关推荐</div>
          <p>这是正文第二段。</p>
        """,
    },
}


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return FakeResponse(FIRST_PAGE_HTML if url.endswith("e76510d9") else SECOND_PAGE_HTML)


CATEGORY_URL = (
    "https://static.bjd.com.cn/static/category/"
    "?categoryId=6095f818e4b054f0ea85207a"
    "&channelId=5b165687a010550e5ddc0e6a"
    "&columnId=5ba1f926e4b0976ea0d9a0fc"
)

SEARCH_PAYLOAD = {
    "code": 0,
    "data": {
        "content": [
            {
                "id": "s6af67281e4b0e42f8f02d904",
                "title": "京津冀三校献艺 共建育人舞台",
                "url": "https://xinwen.bjd.com.cn/content/s6af67281e4b0e42f8f02d904.html",
                "publishTime": "2026-11-13T02:14:25.364+0000",
                "storyType": "COMPO",
            },
            {
                "id": "s6a943dc1e4b03fa51a83960a",
                "title": "视频稿不该入库",
                "url": "https://xinwen.bjd.com.cn/content/s6a943dc1e4b03fa51a83960a.html",
                "publishTime": "2026-11-12T02:14:25.364+0000",
                "storyType": "VIDEO",
            },
            {
                "id": "s6a943dc1e4b03fa51a83960b",
                "title": "站外稿件不该入库",
                "url": "https://example.com/content/s6a943dc1e4b03fa51a83960b.html",
                "publishTime": "2026-11-12T02:14:25.364+0000",
                "storyType": "COMPO",
            },
            {
                "id": "s6a943dc1e4b03fa51a83960c",
                "title": "2000余名师生同台展演",
                "url": "https://xinwen.bjd.com.cn/content/s6a943dc1e4b03fa51a83960c.html",
                "publishTime": "2026-08-30T14:27:13.505+0000",
                "storyType": "COMPO",
            },
        ],
        "size": 10,
        "totalElements": 4,
    },
}


class FakeJsonResponse(FakeResponse):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__("")
        self.payload = payload

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeJsonSession:
    """按请求 URL 的 page 参数返回不同页，并记录调用顺序。"""

    def __init__(self, pages: dict[str, dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeJsonResponse:
        self.calls.append({"url": url, **kwargs})
        page = url.rsplit("page=", 1)[-1]
        return FakeJsonResponse(self.pages.get(page, {"code": 0, "data": {"content": []}}))


def test_m11_beijinghao_parser_preserves_legacy_bare_and_url_results() -> None:
    bare = http_beijinghao.parse_column_input("6142fe79e4b0a8b3e76510d9")
    url = http_beijinghao.parse_column_input(
        "https://peking.bjd.com.cn/bjhrootcolumn/system/6142fe79e4b0a8b3e76510d9"
    )

    assert bare.column_code == url.column_code == "6142fe79e4b0a8b3e76510d9"
    assert bare.page_url == url.page_url


def test_parse_list_extracts_title_detail_id_and_beijing_date() -> None:
    items = http_beijinghao._parse_list_html(FIRST_PAGE_HTML)

    assert len(items) == 1
    assert items[0].title == "2000余名师生同台展演"
    assert items[0].url == (
        "https://peking.bjd.com.cn/content/s6a943dc1e4b03fa51a83960a.html"
    )
    assert items[0].publish_time_iso == "2026-08-30T00:00:00+08:00"
    assert items[0].section == "北京日报"
    assert http_beijinghao.make_article_id(items[0].url) == (
        "beijinghao:s6a943dc1e4b03fa51a83960a"
    )


def test_list_items_fetches_requested_numeric_pages(monkeypatch: Any) -> None:
    session = FakeSession()
    monkeypatch.setattr(http_beijinghao, "_session", lambda: session)

    items = http_beijinghao.list_items(
        entries=[
            http_beijinghao.parse_column_input("6142fe79e4b0a8b3e76510d9")
        ],
        pages=2,
    )

    assert len(items) == 2
    assert [call["url"] for call in session.calls] == [
        "https://peking.bjd.com.cn/bjhrootcolumn/system/6142fe79e4b0a8b3e76510d9",
        "https://peking.bjd.com.cn/bjhrootcolumn/system/6142fe79e4b0a8b3e76510d9/more_2",
    ]


def test_detail_payload_uses_json_content_and_removes_noise() -> None:
    url = "https://peking.bjd.com.cn/content/s6a943dc1e4b03fa51a83960a.html"
    data = http_beijinghao._parse_detail_payload(DETAIL_PAYLOAD, url)

    assert data["title"] == "2000余名师生同台展演"
    assert data["source"] == "北京日报"
    assert data["publish_time_iso"] == "2026-08-30T14:27:13.505000+00:00"
    assert "这是正文第一段" in data["content_markdown"]
    assert "这是正文第二段" in data["content_markdown"]
    assert "https://static.bjd.com.cn/image.jpg" not in data["content_markdown"]
    assert "分享" not in data["content_markdown"]
    assert "相关推荐" not in data["content_markdown"]


def test_detail_payload_rejects_nonzero_code() -> None:
    with pytest.raises(RuntimeError, match="error 500"):
        http_beijinghao._parse_detail_payload(
            {"code": 500, "message": "upstream failure"},
            "https://peking.bjd.com.cn/content/s6a943dc1e4b03fa51a83960a.html",
        )


def test_linked_rows_keep_shared_shape_and_ignore_original_id() -> None:
    item = http_beijinghao._parse_list_html(FIRST_PAGE_HTML)[0]
    article_id = http_beijinghao.make_article_id(item.url)
    fetched_at = datetime(2026, 8, 31, 0, 0, tzinfo=timezone.utc)
    feed_row = http_beijinghao.feed_item_to_row(item, article_id, fetched_at=fetched_at)
    detail_row = http_beijinghao.build_detail_update(
        item,
        article_id,
        http_beijinghao._parse_detail_payload(DETAIL_PAYLOAD, item.url),
        detail_fetched_at=fetched_at,
    )

    assert feed_row["summary"] is None
    assert feed_row["source"] == detail_row["source"] == "北京日报"
    assert feed_row["article_id"] == detail_row["article_id"] == (
        "beijinghao:s6a943dc1e4b03fa51a83960a"
    )
    assert "CO6a943af7d5de5a8cf8a79be2" not in detail_row.values()


def test_row_builders_force_beijing_daily_as_source() -> None:
    item = http_beijinghao.FeedItemLike(
        title="测试新闻",
        url="https://peking.bjd.com.cn/content/testarticle.html",
        section="具体北京号名称",
        publish_time_iso=None,
        raw={},
    )
    fetched_at = datetime(2026, 8, 31, 0, 0, tzinfo=timezone.utc)

    feed_row = http_beijinghao.feed_item_to_row(
        item,
        "beijinghao:testarticle",
        fetched_at=fetched_at,
    )
    detail_row = http_beijinghao.build_detail_update(
        item,
        "beijinghao:testarticle",
        {"source": "另一个具体北京号名称", "content": "<p>正文</p>"},
        detail_fetched_at=fetched_at,
    )

    assert feed_row["source"] == detail_row["source"] == "北京日报"


def test_parse_column_input_accepts_client_category_url() -> None:
    entry = http_beijinghao.parse_column_input(CATEGORY_URL)

    assert entry.column_code == entry.page_url == CATEGORY_URL
    assert http_beijinghao.parse_column_input(CATEGORY_URL).page_url == (
        http_beijinghao.parse_column_input(
            "https://static.bjd.com.cn/static/category/"
            "?columnId=5ba1f926e4b0976ea0d9a0fc"
            "&categoryId=6095f818e4b054f0ea85207a"
            "&channelId=5b165687a010550e5ddc0e6a"
        ).page_url
    )
    # 协议相对与不带协议两种写法都要落到同一个标识
    schemeless = http_beijinghao.parse_column_input(
        CATEGORY_URL.replace("https://", "//")
    )
    assert schemeless.page_url == CATEGORY_URL


def test_parse_category_input_fills_default_channel_and_rejects_incomplete_url() -> None:
    coordinate = http_beijinghao.parse_category_input(
        "https://static.bjd.com.cn/static/category/"
        "?categoryId=6095f818e4b054f0ea85207a"
        "&columnId=5ba1f926e4b0976ea0d9a0fc"
    )

    assert coordinate is not None
    assert coordinate.channel_id == http_beijinghao.DEFAULT_CHANNEL_ID
    assert coordinate.search_url(2) == (
        "https://jiansuo.bjd.com.cn/cass/search"
        "?channelId=5b165687a010550e5ddc0e6a"
        "&categoryId=6095f818e4b054f0ea85207a&page=2"
    )

    with pytest.raises(ValueError, match="缺少 categoryId"):
        http_beijinghao.parse_category_input(
            "https://static.bjd.com.cn/static/category/"
            "?columnId=5ba1f926e4b0976ea0d9a0fc"
        )
    with pytest.raises(ValueError, match="必须是"):
        http_beijinghao.parse_category_input(
            "https://static.bjd.com.cn/static/other/?categoryId=1&columnId=2"
        )


def test_parse_category_list_skips_video_and_offsite_rows() -> None:
    items = http_beijinghao._parse_category_list_payload(SEARCH_PAYLOAD)

    assert [item.title for item in items] == [
        "京津冀三校献艺 共建育人舞台",
        "2000余名师生同台展演",
    ]
    assert items[0].url == (
        "https://xinwen.bjd.com.cn/content/s6af67281e4b0e42f8f02d904.html"
    )
    assert items[0].section == "北京日报"
    # 列表自带精确发布时间，不再是日期零点
    assert items[0].publish_time_iso == "2026-11-13T02:14:25.364000+00:00"
    assert http_beijinghao.make_article_id(items[0].url) == (
        "beijinghao:s6af67281e4b0e42f8f02d904"
    )


def test_parse_category_list_filters_existing_ids() -> None:
    existing = {"beijinghao:s6af67281e4b0e42f8f02d904"}

    items = http_beijinghao._parse_category_list_payload(
        SEARCH_PAYLOAD,
        existing_ids=existing,
    )

    assert [item.title for item in items] == ["2000余名师生同台展演"]


def test_category_list_walks_search_offset_pages_and_stops_on_empty_page(
    monkeypatch: Any,
) -> None:
    session = FakeJsonSession(
        {
            "0": SEARCH_PAYLOAD,
            "1": {"code": 0, "data": {"content": []}},
        }
    )
    monkeypatch.setattr(http_beijinghao, "_session", lambda: session)

    items = http_beijinghao.list_items(
        entries=[http_beijinghao.parse_column_input(CATEGORY_URL)],
        pages=3,
    )

    assert len(items) == 2
    assert [call["url"] for call in session.calls] == [
        "https://jiansuo.bjd.com.cn/cass/search"
        "?channelId=5b165687a010550e5ddc0e6a"
        "&categoryId=6095f818e4b054f0ea85207a&page=0",
        "https://jiansuo.bjd.com.cn/cass/search"
        "?channelId=5b165687a010550e5ddc0e6a"
        "&categoryId=6095f818e4b054f0ea85207a&page=1",
    ]
    assert session.calls[0]["headers"] == {"Referer": CATEGORY_URL}


def test_category_list_keeps_paging_when_a_whole_page_is_already_stored(
    monkeypatch: Any,
) -> None:
    # existing_ids 只过滤、不截停翻页：第 1 页全已入库时仍要翻到第 2 页。
    session = FakeJsonSession(
        {
            "0": SEARCH_PAYLOAD,
            "1": {
                "code": 0,
                "data": {
                    "content": [
                        {
                            "id": "s6a0000000000000000000001",
                            "title": "更早的一条",
                            "url": (
                                "https://xinwen.bjd.com.cn/content/"
                                "s6a0000000000000000000001.html"
                            ),
                            "publishTime": "2026-07-01T00:00:00.000+0000",
                            "storyType": "COMPO",
                        }
                    ]
                },
            },
        }
    )
    monkeypatch.setattr(http_beijinghao, "_session", lambda: session)
    stored = {
        "beijinghao:s6af67281e4b0e42f8f02d904",
        "beijinghao:s6a943dc1e4b03fa51a83960c",
    }

    items = http_beijinghao.list_items(
        entries=[http_beijinghao.parse_column_input(CATEGORY_URL)],
        pages=2,
        existing_ids=stored,
    )

    assert [item.title for item in items] == ["更早的一条"]
    assert [call["url"].rsplit("page=", 1)[-1] for call in session.calls] == ["0", "1"]


def test_category_list_respects_limit(monkeypatch: Any) -> None:
    session = FakeJsonSession({"0": SEARCH_PAYLOAD})
    monkeypatch.setattr(http_beijinghao, "_session", lambda: session)

    items = http_beijinghao.list_items(
        entries=[http_beijinghao.parse_column_input(CATEGORY_URL)],
        limit=1,
        pages=3,
    )

    assert len(items) == 1
    assert len(session.calls) == 1


def test_category_search_error_raises(monkeypatch: Any) -> None:
    session = FakeJsonSession({"0": {"code": 500, "message": "boom"}})
    monkeypatch.setattr(http_beijinghao, "_session", lambda: session)

    with pytest.raises(RuntimeError, match="error 500"):
        http_beijinghao.list_items(
            entries=[http_beijinghao.parse_column_input(CATEGORY_URL)],
        )


def test_detail_json_url_follows_article_host() -> None:
    assert http_beijinghao._detail_json_url(
        "https://xinwen.bjd.com.cn/content/s6af67281e4b0e42f8f02d904.html"
    ) == "https://xinwen.bjd.com.cn/content/s6af67281e4b0e42f8f02d904.json"
    assert http_beijinghao._detail_json_url(
        "//peking.bjd.com.cn/content/s6a943dc1e4b03fa51a83960a.html"
    ) == "https://peking.bjd.com.cn/content/s6a943dc1e4b03fa51a83960a.json"
    assert http_beijinghao._is_detail_url(
        "https://xinwen.bjd.com.cn/content/s6af67281e4b0e42f8f02d904.html"
    )
    assert not http_beijinghao._is_detail_url(
        "https://static.bjd.com.cn/content/s6af67281e4b0e42f8f02d904.html"
    )


def test_client_category_detail_row_uses_xinwen_content() -> None:
    item = http_beijinghao._parse_category_list_payload(SEARCH_PAYLOAD)[0]
    article_id = http_beijinghao.make_article_id(item.url)
    fetched_at = datetime(2026, 11, 14, 0, 0, tzinfo=timezone.utc)
    data = http_beijinghao._parse_detail_payload(
        {
            "code": 0,
            "data": {
                "title": "京津冀三校献艺 共建育人舞台",
                "publishTime": "2026-11-13T02:14:25.364+0000",
                "url": "https://xinwen.bjd.com.cn/content/s6af67281e4b0e42f8f02d904.html",
                "content": "<p>这是正文。</p><figure><img src='//static.bjd.com.cn/a.jpg'/></figure>",
            },
        },
        item.url,
    )
    detail_row = http_beijinghao.build_detail_update(
        item,
        article_id,
        data,
        detail_fetched_at=fetched_at,
    )

    assert article_id == "beijinghao:s6af67281e4b0e42f8f02d904"
    assert detail_row["source"] == "北京日报"
    assert detail_row["publish_time_iso"].isoformat() == (
        "2026-11-13T02:14:25.364000+00:00"
    )
    assert "这是正文。" in detail_row["content_markdown"]
    assert "static.bjd.com.cn/a.jpg" not in detail_row["content_markdown"]
    assert detail_row["url"] == item.url
