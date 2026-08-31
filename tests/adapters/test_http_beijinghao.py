from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
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


def test_load_column_entries_accepts_bare_ids_urls_bom_and_deduplicates(tmp_path: Path) -> None:
    path = tmp_path / "columns.txt"
    path.write_text(
        "\ufeff# columns\n6142fe79e4b0a8b3e76510d9\n"
        "https://peking.bjd.com.cn/bjhrootcolumn/system/6142fe79e4b0a8b3e76510d9\n"
        "61443619e4b0637be8d99e0f\n",
        encoding="utf-8",
    )

    entries = http_beijinghao.load_column_entries(path)

    assert [entry.column_code for entry in entries] == [
        "6142fe79e4b0a8b3e76510d9",
        "61443619e4b0637be8d99e0f",
    ]


def test_parse_list_extracts_title_detail_id_and_beijing_date() -> None:
    items = http_beijinghao._parse_list_html(FIRST_PAGE_HTML)

    assert len(items) == 1
    assert items[0].title == "2000余名师生同台展演"
    assert items[0].url == (
        "https://peking.bjd.com.cn/content/s6a943dc1e4b03fa51a83960a.html"
    )
    assert items[0].publish_time_iso == "2026-08-30T00:00:00+08:00"
    assert items[0].section == "现代教育报"
    assert http_beijinghao.make_article_id(items[0].url) == (
        "beijinghao:s6a943dc1e4b03fa51a83960a"
    )


def test_list_items_fetches_requested_numeric_pages(monkeypatch: Any, tmp_path: Path) -> None:
    path = tmp_path / "columns.txt"
    path.write_text("6142fe79e4b0a8b3e76510d9\n", encoding="utf-8")
    session = FakeSession()
    monkeypatch.setattr(http_beijinghao, "_resolve_columns_path", lambda: path)
    monkeypatch.setattr(http_beijinghao, "_session", lambda: session)

    items = http_beijinghao.list_items(pages=2)

    assert len(items) == 2
    assert [call["url"] for call in session.calls] == [
        "https://peking.bjd.com.cn/bjhrootcolumn/system/6142fe79e4b0a8b3e76510d9",
        "https://peking.bjd.com.cn/bjhrootcolumn/system/6142fe79e4b0a8b3e76510d9/more_2",
    ]


def test_detail_payload_uses_json_content_and_removes_noise() -> None:
    url = "https://peking.bjd.com.cn/content/s6a943dc1e4b03fa51a83960a.html"
    data = http_beijinghao._parse_detail_payload(DETAIL_PAYLOAD, url)

    assert data["title"] == "2000余名师生同台展演"
    assert data["source"] == "现代教育报"
    assert data["publish_time_iso"] == "2026-08-30T14:27:13.505000+00:00"
    assert "这是正文第一段" in data["content_markdown"]
    assert "这是正文第二段" in data["content_markdown"]
    assert "https://static.bjd.com.cn/image.jpg" in data["content_markdown"]
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
    assert feed_row["article_id"] == detail_row["article_id"] == (
        "beijinghao:s6a943dc1e4b03fa51a83960a"
    )
    assert "CO6a943af7d5de5a8cf8a79be2" not in detail_row.values()

