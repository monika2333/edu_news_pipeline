from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import pytest
import requests

from src.adapters import http_xinjinghao


def _entry(xjh_id: str = "296") -> http_xinjinghao.AccountEntry:
    return http_xinjinghao.AccountEntry(
        xjh_id=xjh_id,
        page_url=f"https://m.bjnews.com.cn/mp/{xjh_id}",
    )


def _item(
    uuid: str = "1793885788019604",
    *,
    title: str = "京藏协同提质 联盟铸魂强师",
    content: str = "<p>正文第一段。</p><p>正文第二段。</p>",
    publish_timestamp: str = "1793886622",
    publish_time: str = "2026-11-05 21:50:22",
) -> dict[str, Any]:
    return {
        "type": "1",
        "show_type": "2",
        "uuid": uuid,
        "row": {
            "uuid": uuid,
            "title": title,
            "content": content,
            "publish_timestamp": publish_timestamp,
            "publish_time": publish_time,
        },
        "share_url": f"https://m.bjnews.com.cn/detail/{uuid}.html",
    }


def _payload(
    *,
    code: Any = 0,
    items: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    return {"code": code, "msg": "ok", "data": items or []}


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, pages: dict[int, Any]) -> None:
        self.pages = pages
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        page = int(kwargs["params"]["page"])
        value = self.pages.get(page, _payload())
        if isinstance(value, Exception):
            raise value
        return FakeResponse(value)


def test_parse_account_input_normalizes_bare_id_and_url_forms() -> None:
    bare = http_xinjinghao.parse_account_input("296")
    url = http_xinjinghao.parse_account_input("https://m.bjnews.com.cn/mp/296")
    query = http_xinjinghao.parse_account_input(
        "https://m.bjnews.com.cn/mp/296?from=list"
    )
    trailing_slash = http_xinjinghao.parse_account_input(
        "https://m.bjnews.com.cn/mp/296/"
    )

    assert bare.xjh_id == url.xjh_id == query.xjh_id == trailing_slash.xjh_id == "296"
    assert (
        bare.page_url
        == url.page_url
        == query.page_url
        == trailing_slash.page_url
        == "https://m.bjnews.com.cn/mp/296"
    )
    assert bare.raw_source == "296"


@pytest.mark.parametrize(
    "raw",
    [
        "https://m.bjnews.com.cn/detail/1793885788019604.html",
        "https://www.bjnews.com.cn/mp/296",
        "https://example.com/mp/296",
        "mp/296/extra",
        "",
    ],
)
def test_parse_account_input_rejects_article_foreign_and_empty_input(
    raw: str,
) -> None:
    with pytest.raises(ValueError):
        http_xinjinghao.parse_account_input(raw)


def test_make_article_id_from_url_and_bare_uuid_match() -> None:
    url = "https://m.bjnews.com.cn/detail/1793885788019604.html"

    assert http_xinjinghao.make_article_id(url) == (
        "xinjinghao:1793885788019604"
    )
    assert http_xinjinghao.make_article_id("1793885788019604") == (
        "xinjinghao:1793885788019604"
    )


def test_parse_list_payload_extracts_title_url_time_and_content() -> None:
    items = http_xinjinghao._parse_list_payload(_payload(items=[_item()]))

    assert len(items) == 1
    item = items[0]
    assert item.title == "京藏协同提质 联盟铸魂强师"
    assert item.url == "https://m.bjnews.com.cn/detail/1793885788019604.html"
    assert item.section == "新京报"
    assert item.publish_time_iso == "2026-11-05T21:50:22+08:00"
    assert "正文第一段" in item.raw["content"]


def test_parse_list_payload_accepts_string_code_and_missing_share_url() -> None:
    item = _item()
    item["share_url"] = ""

    items = http_xinjinghao._parse_list_payload(_payload(code="0", items=[item]))

    assert len(items) == 1
    assert items[0].url == "https://m.bjnews.com.cn/detail/1793885788019604.html"


def test_parse_list_payload_rejects_nonzero_code_and_invalid_data() -> None:
    with pytest.raises(RuntimeError, match="error 500"):
        http_xinjinghao._parse_list_payload({"code": 500, "msg": "upstream"})

    with pytest.raises(RuntimeError, match="invalid data"):
        http_xinjinghao._parse_list_payload({"code": 0, "data": {"x": 1}})


def test_parse_list_payload_empty_data_and_existing_ids() -> None:
    assert http_xinjinghao._parse_list_payload(_payload()) == []
    assert (
        http_xinjinghao._parse_list_payload(
            _payload(items=[_item()]),
            existing_ids={"xinjinghao:1793885788019604"},
        )
        == []
    )


def test_parse_list_payload_derives_article_id_from_share_url() -> None:
    item = _item("uuid-a")
    item["share_url"] = "https://m.bjnews.com.cn/detail/url-b.html"

    items = http_xinjinghao._parse_list_payload(_payload(items=[item]))

    assert len(items) == 1
    assert http_xinjinghao.make_article_id(items[0].url) == "xinjinghao:url-b"
    assert (
        http_xinjinghao._parse_list_payload(
            _payload(items=[item]),
            existing_ids={"xinjinghao:url-b"},
        )
        == []
    )


def test_list_items_fetches_one_page_and_passes_referer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession({1: _payload(items=[_item("a"), _item("b")])})
    monkeypatch.setattr(http_xinjinghao, "_session", lambda: session)

    items = http_xinjinghao.list_items([_entry()])

    assert len(items) == 2
    assert session.calls[0]["url"] == http_xinjinghao.LIST_API_URL
    assert session.calls[0]["params"] == {"xjh_id": "296", "page": 1}
    assert session.calls[0]["headers"] == {
        "Referer": "https://m.bjnews.com.cn/mp/296"
    }


def test_list_items_pages_until_short_page(monkeypatch: pytest.MonkeyPatch) -> None:
    first_page = [_item(str(1000 + index)) for index in range(10)]
    session = FakeSession(
        {
            1: _payload(items=first_page),
            2: _payload(items=[_item("2000"), _item("2001")]),
        }
    )
    monkeypatch.setattr(http_xinjinghao, "_session", lambda: session)

    items = http_xinjinghao.list_items([_entry()], pages=5)

    assert len(items) == 12
    assert [call["params"]["page"] for call in session.calls] == [1, 2]


def test_list_items_stops_on_empty_page(monkeypatch: pytest.MonkeyPatch) -> None:
    first_page = [_item(str(1000 + index)) for index in range(10)]
    session = FakeSession({1: _payload(items=first_page), 2: _payload()})
    monkeypatch.setattr(http_xinjinghao, "_session", lambda: session)

    items = http_xinjinghao.list_items([_entry()], pages=5)

    assert len(items) == 10
    assert [call["params"]["page"] for call in session.calls] == [1, 2]


def test_list_items_honors_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    first_page = [_item(str(1000 + index)) for index in range(10)]
    session = FakeSession({1: _payload(items=first_page)})
    monkeypatch.setattr(http_xinjinghao, "_session", lambda: session)

    items = http_xinjinghao.list_items([_entry()], limit=3, pages=5)

    assert len(items) == 3
    assert len(session.calls) == 1


def test_list_items_keeps_fetched_pages_when_next_page_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_page = [_item(str(1000 + index)) for index in range(10)]
    session = FakeSession({1: _payload(items=first_page), 2: requests.Timeout("slow")})
    monkeypatch.setattr(http_xinjinghao, "_session", lambda: session)

    items = http_xinjinghao.list_items([_entry()], pages=5)

    assert len(items) == 10
    assert [call["params"]["page"] for call in session.calls] == [1, 2]


def test_list_items_keeps_paging_when_first_page_still_has_new_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_page = [_item(str(1000 + index)) for index in range(10)]
    existing = {f"xinjinghao:{1000 + index}" for index in range(6)}
    session = FakeSession(
        {1: _payload(items=first_page), 2: _payload(items=[_item("2000")])}
    )
    monkeypatch.setattr(http_xinjinghao, "_session", lambda: session)

    items = http_xinjinghao.list_items(
        [_entry()],
        pages=5,
        existing_ids=existing,
    )

    assert len(items) == 5
    assert [call["params"]["page"] for call in session.calls] == [1, 2]


def test_list_items_keeps_paging_when_first_page_is_all_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_page = [_item(str(1000 + index)) for index in range(10)]
    existing = {f"xinjinghao:{1000 + index}" for index in range(10)}
    session = FakeSession(
        {1: _payload(items=first_page), 2: _payload(items=[_item("2000")])}
    )
    monkeypatch.setattr(http_xinjinghao, "_session", lambda: session)

    items = http_xinjinghao.list_items(
        [_entry()],
        pages=5,
        existing_ids=existing,
    )

    assert len(items) == 1
    assert http_xinjinghao.make_article_id(items[0].url) == "xinjinghao:2000"
    assert [call["params"]["page"] for call in session.calls] == [1, 2]


def test_feed_item_to_row_forces_xinjingbao_source() -> None:
    item = http_xinjinghao._parse_list_payload(_payload(items=[_item()]))[0]
    article_id = http_xinjinghao.make_article_id(item.url)
    fetched_at = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)

    row = http_xinjinghao.feed_item_to_row(
        item,
        article_id,
        fetched_at=fetched_at,
    )

    assert row["source"] == "新京报"
    assert row["article_id"] == "xinjinghao:1793885788019604"
    assert row["publish_time"] == 1793886622
    assert row["summary"] is None


def test_article_to_detail_row_strips_trailing_editorial_lines() -> None:
    content = (
        "<p>正文第一段。</p><p>正文第二段。</p>"
        "<p>供稿：教育援藏团队</p>"
        "<p>编辑：吕&nbsp; 娟</p>"
        "<p>校对：崔俊杰</p>"
        "<p>审核：周&nbsp; 凯</p>"
    )
    item = http_xinjinghao._parse_list_payload(
        _payload(items=[_item(content=content)])
    )[0]
    article_id = http_xinjinghao.make_article_id(item.url)
    detail_fetched_at = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)

    row = http_xinjinghao.article_to_detail_row(
        item,
        article_id,
        detail_fetched_at=detail_fetched_at,
    )

    assert row["source"] == "新京报"
    assert "正文第一段" in row["content_markdown"]
    assert "正文第二段" in row["content_markdown"]
    assert "供稿" not in row["content_markdown"]
    assert "编辑" not in row["content_markdown"]
    assert "校对" not in row["content_markdown"]
    assert "审核" not in row["content_markdown"]


def test_article_to_detail_row_keeps_source_line_inside_body() -> None:
    content = (
        "<p>正文第一段。</p>"
        "<p>来源：某单位</p>"
        "<p>正文第二段。</p>"
    )
    item = http_xinjinghao._parse_list_payload(
        _payload(items=[_item(content=content)])
    )[0]
    article_id = http_xinjinghao.make_article_id(item.url)

    row = http_xinjinghao.article_to_detail_row(
        item,
        article_id,
        detail_fetched_at=datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc),
    )

    assert "来源：某单位" in row["content_markdown"]


def test_article_to_detail_row_rejects_empty_content() -> None:
    item = http_xinjinghao._parse_list_payload(
        _payload(items=[_item(content="<p>　</p>")])
    )[0]
    article_id = http_xinjinghao.make_article_id(item.url)

    with pytest.raises(RuntimeError, match="Empty Xinjinghao article content"):
        http_xinjinghao.article_to_detail_row(
            item,
            article_id,
            detail_fetched_at=datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc),
        )


def test_html_to_markdown_strips_images_and_handles_empty_input() -> None:
    markdown = http_xinjinghao.html_to_markdown(
        '<p>正文保留。</p><img src="https://example.com/pic.jpg">'
    )

    assert markdown == "正文保留。"
    assert http_xinjinghao.html_to_markdown("") == ""
    assert http_xinjinghao.html_to_markdown(None) == ""


def test_html_to_markdown_keeps_content_without_trailing_metadata() -> None:
    markdown = http_xinjinghao.html_to_markdown("<p>只有正文。</p>")

    assert markdown == "只有正文。"


def test_row_builders_ignore_item_section_and_use_xinjingbao() -> None:
    item = http_xinjinghao.FeedItemLike(
        title="测试新闻",
        url="https://m.bjnews.com.cn/detail/testarticle.html",
        section="另一个号",
        publish_time_iso=None,
        raw={"content": "<p>正文</p>"},
    )
    fetched_at = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)

    feed_row = http_xinjinghao.feed_item_to_row(
        item,
        "xinjinghao:testarticle",
        fetched_at=fetched_at,
    )
    detail_row = http_xinjinghao.article_to_detail_row(
        item,
        "xinjinghao:testarticle",
        detail_fetched_at=fetched_at,
    )

    assert feed_row["source"] == detail_row["source"] == "新京报"
